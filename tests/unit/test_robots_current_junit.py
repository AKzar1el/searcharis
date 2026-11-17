"""Portable offline JUnit reporting for the standalone reviewed robots gate."""

import json
import socket
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from searcharis.robots_policy_cli import main


def _inputs(tmp_path):
    build = tmp_path / "dist"
    build.mkdir()
    reviewed = tmp_path / "reviewed-robots.txt"
    policy = b"User-agent: *\nAllow: /\nSitemap: https://example.test/sitemap.xml\n"
    reviewed.write_bytes(policy)
    (build / "robots.txt").write_bytes(policy)
    return reviewed, build


@pytest.mark.parametrize("mode,expected_code", [
    ("pass", 0), ("changed", 1), ("missing", 1),
])
def test_junit_file_represents_current_robots_result_offline(
    tmp_path, capsys, monkeypatch, mode, expected_code,
):
    reviewed, build = _inputs(tmp_path)
    if mode == "changed":
        (build / "robots.txt").write_bytes(b"User-agent: *\nDisallow: /\n")
    elif mode == "missing":
        (build / "robots.txt").unlink()
    report = tmp_path / "robots.xml"

    def deny_socket(*_args, **_kwargs):
        raise AssertionError("JUnit reporting must never fetch a URL")

    monkeypatch.setattr(socket, "socket", deny_socket)
    assert main([str(reviewed), str(build), "--junit-file", str(report)]) == expected_code
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    raw = report.read_bytes()
    assert raw.startswith(b"<?xml") and raw.endswith(b"\n")
    assert not raw.startswith(b"\xef\xbb\xbf")
    suite = ET.fromstring(raw)
    assert suite.tag == "testsuite"
    assert suite.attrib["tests"] == "1"
    assert suite.attrib["failures"] == str(expected_code)
    case = suite.find("testcase")
    assert case is not None
    if expected_code == 0:
        assert case.find("failure") is None
    else:
        failure = case.find("failure")
        assert failure is not None
        assert mode in ET.tostring(failure, encoding="unicode")


def test_junit_reports_each_missing_reviewed_sitemap_with_xml_safe_urls(tmp_path, capsys):
    reviewed, build = _inputs(tmp_path)
    report = tmp_path / "robots.xml"
    first = "https://example.test/news.xml?a=1&b=2"
    second = "https://example.test/video.xml"
    assert main([
        str(reviewed), str(build), "--junit-file", str(report),
        "--require-robots-sitemap", first,
        "--require-robots-sitemap", second,
    ]) == 1
    assert capsys.readouterr().err == ""
    suite = ET.parse(report).getroot()
    assert suite.attrib["tests"] == suite.attrib["failures"] == "2"
    failures = [case.find("failure") for case in suite.findall("testcase")]
    assert len(failures) == 2 and all(item is not None for item in failures)
    assert first in failures[0].text
    assert second in failures[1].text
    assert b"&amp;" in report.read_bytes()


def test_junit_can_share_one_offline_check_with_complete_json_and_annotations(
    tmp_path, capsys,
):
    reviewed, build = _inputs(tmp_path)
    (build / "robots.txt").unlink()
    json_path = tmp_path / "robots.json"
    junit_path = tmp_path / "robots.xml"
    args = [
        str(reviewed), str(build), "--json-file", str(json_path),
        "--junit-file", str(junit_path), "--github-annotations",
    ]
    assert main(args) == 1
    output = capsys.readouterr()
    assert output.err == "" and output.out.count("::error ") == 1
    assert json.loads(json_path.read_text(encoding="utf-8"))["reason"] == "missing"
    assert ET.parse(junit_path).getroot().attrib["failures"] == "1"


@pytest.mark.parametrize("unsafe", [
    "same", "existing", "missing_parent", "inside_build", "reviewed_source",
])
def test_junit_preflight_refuses_unsafe_paths_without_partial_json(
    tmp_path, capsys, unsafe,
):
    reviewed, build = _inputs(tmp_path)
    json_path = tmp_path / "robots.json"
    target = {
        "same": json_path,
        "existing": tmp_path / "existing.xml",
        "missing_parent": tmp_path / "missing" / "robots.xml",
        "inside_build": build / "robots.xml",
        "reviewed_source": reviewed,
    }[unsafe]
    if unsafe == "existing":
        target.write_bytes(b"KEEP")
    original_policy = reviewed.read_bytes()
    original_built = (build / "robots.txt").read_bytes()
    assert main([
        str(reviewed), str(build), "--json-file", str(json_path),
        "--junit-file", str(target),
    ]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "Invalid input:" in captured.err
    assert not json_path.exists()
    assert reviewed.read_bytes() == original_policy
    assert (build / "robots.txt").read_bytes() == original_built
    if unsafe == "existing":
        assert target.read_bytes() == b"KEEP"


def test_junit_refuses_json_stdout_combination(tmp_path, capsys):
    reviewed, build = _inputs(tmp_path)
    with pytest.raises(SystemExit) as error:
        main([str(reviewed), str(build), "--json", "--junit-file",
              str(tmp_path / "robots.xml")])
    assert error.value.code == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_junit_invalid_input_creates_no_artifact(tmp_path, capsys):
    reviewed, build = _inputs(tmp_path)
    target = tmp_path / "robots.xml"
    assert main([str(reviewed.with_name("absent.txt")), str(build),
                 "--junit-file", str(target)]) == 2
    assert not target.exists()
    assert "Invalid input:" in capsys.readouterr().err


def test_interrupted_junit_export_cleans_only_its_owned_reports(
    tmp_path, capsys, monkeypatch,
):
    reviewed, build = _inputs(tmp_path)
    json_path = tmp_path / "robots.json"
    junit_path = tmp_path / "robots.xml"
    original_open = Path.open

    class Interrupted:
        def __init__(self, file):
            self.file = file

        def __enter__(self):
            self.file.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return self.file.__exit__(exc_type, exc_value, traceback)

        def fileno(self):
            return self.file.fileno()

        def write(self, payload):
            self.file.write(payload[:9])
            self.file.flush()
            raise OSError("injected JUnit export interruption")

    def interrupt_junit(path, mode="r", *args, **kwargs):
        file = original_open(path, mode, *args, **kwargs)
        return Interrupted(file) if path == junit_path and mode == "x" else file

    monkeypatch.setattr(Path, "open", interrupt_junit)
    assert main([
        str(reviewed), str(build), "--json-file", str(json_path),
        "--junit-file", str(junit_path), "--github-annotations",
    ]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "injected JUnit export interruption" in captured.err
    assert not json_path.exists()
    assert not junit_path.exists()
