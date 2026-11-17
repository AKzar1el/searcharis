"""Current offline sitemap CI exports complete, create-only JUnit test reports."""

import json
import os
import socket
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from searcharis.site_junit import build_current_sitemap_junit
from searcharis.sitemap_cli import main


def _inputs(tmp_path, *, failing=False):
    pages = [{
        "path": "index.html", "contract": {
            "url": "https://example.test/", "indexable": True,
        },
    }]
    urls = ["https://example.test/"]
    if failing:
        pages.extend([
            {"path": "missing.html", "contract": {
                "url": "https://example.test/missing", "indexable": True,
            }},
            {"path": "excluded.html", "contract": {
                "url": "https://example.test/excluded", "indexable": False,
            }},
            {"path": "alias.html", "contract": {
                "url": "https://example.test/alias",
                "canonical": "https://example.test/", "indexable": True,
            }},
        ])
        urls.extend([
            "https://example.test/excluded", "https://example.test/alias",
            "https://example.test/unreviewed",
        ])
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": 1, "pages": pages}), encoding="utf-8")
    built = tmp_path / "dist"
    built.mkdir()
    sitemap = built / "sitemap.xml"
    sitemap.write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{url}</loc></url>" for url in urls)
        + "</urlset>", encoding="utf-8",
    )
    flags = ["--reject-noncanonical-urls", "--reject-unreviewed-urls"]
    return manifest, sitemap, flags


def test_junit_file_pass_is_portable_and_does_not_change_exit_status(tmp_path, capsys):
    manifest, sitemap, _ = _inputs(tmp_path)
    target = tmp_path / "sitemap.xml"
    assert main([str(manifest), str(sitemap), "--junit-file", str(target)]) == 0
    output = capsys.readouterr()
    assert output.out == output.err == ""
    assert target.read_bytes().startswith(b"<?xml")
    suite = ET.parse(target).getroot()
    assert suite.attrib["tests"] == "1"
    assert suite.attrib["failures"] == "0"
    assert suite.find("testcase").attrib["name"] == "current-sitemap-passed"


def test_junit_records_only_current_blocking_categories(tmp_path, capsys):
    manifest, sitemap, flags = _inputs(tmp_path, failing=True)
    target = tmp_path / "current.xml"
    assert main([str(manifest), str(sitemap), *flags,
                 "--junit-file", str(target)]) == 1
    assert capsys.readouterr().out == ""
    suite = ET.parse(target).getroot()
    assert suite.attrib["tests"] == suite.attrib["failures"] == "4"
    cases = suite.findall("testcase")
    assert len(cases) == 4
    assert all(case.find("failure") is not None for case in cases)
    labels = [case.find("failure").attrib["message"] for case in cases]
    assert labels == [
        "Required canonical URL missing", "Reviewed noindex URL present",
        "Reviewed noncanonical URL present", "Unreviewed sitemap URL present",
    ]
    assert all("https://example.test/" in case.find("failure").text for case in cases)


def test_json_and_junit_in_one_run_emit_annotations_after_files(tmp_path, capsys):
    manifest, sitemap, flags = _inputs(tmp_path, failing=True)
    json_path, junit_path = tmp_path / "site.json", tmp_path / "site.xml"
    assert main([str(manifest), str(sitemap), *flags,
                 "--json-file", str(json_path), "--junit-file", str(junit_path),
                 "--github-annotations"]) == 1
    assert json.loads(json_path.read_text(encoding="utf-8"))["status"] == "fail"
    assert ET.parse(junit_path).getroot().attrib["failures"] == "4"
    captured = capsys.readouterr()
    assert captured.err == ""
    assert len(captured.out.splitlines()) == 4
    assert all(line.startswith("::error title=Sitemap ")
               for line in captured.out.splitlines())


@pytest.mark.parametrize("bad_target", ["duplicate", "existing", "missing-parent", "inside-build"])
def test_junit_preflight_rejects_conflicts_without_earlier_artifacts(
    tmp_path, capsys, bad_target,
):
    manifest, sitemap, _ = _inputs(tmp_path)
    json_path = tmp_path / "report.json"
    junit_path = tmp_path / "report.xml"
    if bad_target == "duplicate":
        junit_path = json_path
    elif bad_target == "existing":
        junit_path.write_text("someone else's report", encoding="utf-8")
    elif bad_target == "missing-parent":
        junit_path = tmp_path / "missing" / "report.xml"
    elif bad_target == "inside-build":
        junit_path = sitemap.parent / "report.xml"

    assert main([str(manifest), str(sitemap), "--json-file", str(json_path),
                 "--junit-file", str(junit_path), "--github-annotations"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Invalid input" in captured.err
    assert not json_path.exists()
    if bad_target == "existing":
        assert junit_path.read_text(encoding="utf-8") == "someone else's report"
    else:
        assert not junit_path.exists()


@pytest.mark.parametrize("replaced", [False, True])
def test_failed_second_report_cleans_only_owned_outputs(
    tmp_path, monkeypatch, capsys, replaced,
):
    manifest, sitemap, _ = _inputs(tmp_path)
    json_path, junit_path = tmp_path / "report.json", tmp_path / "report.xml"
    original_open = Path.open

    class Interrupted:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            result = self.handle.__exit__(exc_type, exc_value, traceback)
            if replaced:
                foreign = tmp_path / "foreign.xml"
                foreign.write_text("foreign replacement", encoding="utf-8")
                os.replace(foreign, junit_path)
            return result

        def fileno(self):
            return self.handle.fileno()

        def write(self, content):
            self.handle.write(content[:11])
            self.handle.flush()
            raise OSError("injected JUnit write failure")

    def patched_open(path, mode="r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        return Interrupted(handle) if path == junit_path and mode == "x" else handle

    monkeypatch.setattr(Path, "open", patched_open)
    assert main([str(manifest), str(sitemap), "--json-file", str(json_path),
                 "--junit-file", str(junit_path), "--github-annotations"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "", "no successful annotations on failed bundle"
    assert "injected JUnit write failure" in captured.err
    assert not json_path.exists(), "earlier artifact must be removed on late failure"
    if replaced:
        assert junit_path.read_text(encoding="utf-8") == "foreign replacement"
        assert "partial report cleanup skipped" in captured.err
    else:
        assert not junit_path.exists()


def test_stdout_json_cannot_mix_with_junit_artifact(tmp_path):
    manifest, sitemap, _ = _inputs(tmp_path)
    with pytest.raises(SystemExit, match="2"):
        main([str(manifest), str(sitemap), "--json",
              "--junit-file", str(tmp_path / "sitemap.xml")])


def test_junit_does_not_connect_to_manifest_urls(tmp_path, monkeypatch):
    manifest, sitemap, flags = _inputs(tmp_path, failing=True)

    def reject_network(*_args, **_kwargs):
        raise AssertionError("offline sitemap gate attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    output = tmp_path / "sitemap-junit.xml"
    assert main([str(manifest), str(sitemap), *flags,
                 "--junit-file", str(output)]) == 1
    assert ET.parse(output).getroot().attrib["failures"] == "4"


def test_junit_diagnostics_escape_xml_and_bound_hostile_values():
    hostile = "https://example.test/" + "x" * 1200 + "\x01\ud800<&"
    report = {"missing_required": [hostile], "present_excluded": []}
    suite = ET.fromstring(build_current_sitemap_junit(report))
    assert suite.attrib["tests"] == "1"
    case = suite.find("testcase")
    assert len(case.attrib["name"]) <= 300
    message = case.find("failure").text
    assert len(message) < 1100
    assert "\ud800" not in message and "\x01" not in message
