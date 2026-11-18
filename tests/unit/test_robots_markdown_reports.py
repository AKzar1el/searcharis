"""Standalone robots reviewer artifacts stay deterministic, offline and create-only."""

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
    content = b"User-agent: *\nAllow: /\nSitemap: https://example.test/sitemap.xml\n"
    reviewed.write_bytes(content)
    (build / "robots.txt").write_bytes(content)
    return reviewed, build


@pytest.mark.parametrize("mode,exit_code", [
    ("pass", 0), ("changed", 1), ("missing", 1),
])
def test_markdown_current_robots_is_bounded_offline_and_honest(
    tmp_path, monkeypatch, capsys, mode, exit_code,
):
    reviewed, build = _inputs(tmp_path)
    if mode == "changed":
        (build / "robots.txt").write_bytes(b"User-agent: *\nDisallow: /\n")
    elif mode == "missing":
        (build / "robots.txt").unlink()
    destination = tmp_path / "review.md"
    monkeypatch.setattr(socket, "socket", lambda *_a, **_kw: (
        (_ for _ in ()).throw(AssertionError("no URL fetch permitted"))
    ))

    assert main([str(reviewed), str(build), "--markdown-file", str(destination)]) == exit_code
    assert capsys.readouterr().out == ""
    report = destination.read_bytes()
    assert report.endswith(b"\n") and not report.startswith(b"\xef\xbb\xbf")
    content = report.decode("utf-8")
    assert "# Searcharis SEO current robots" in content
    assert f"**Result: {'PASS' if exit_code == 0 else 'FAIL'}**" in content
    assert "reviewed local" in content.lower()
    assert "no URLs" in content or "No URLs" in content
    if mode == "pass":
        assert "No blocking" in content
    else:
        assert mode in content
        assert "Reviewer next steps" in content


def test_markdown_reports_missing_sitemap_urls_escaped(tmp_path, capsys):
    reviewed, build = _inputs(tmp_path)
    destination = tmp_path / "review.md"
    first = "https://example.test/news.xml?a=1&b=2"
    second = "https://example.test/videos.xml"
    assert main([
        str(reviewed), str(build), "--markdown-file", str(destination),
        "--require-robots-sitemap", first,
        "--require-robots-sitemap", second,
    ]) == 1
    output = capsys.readouterr()
    assert output.out == output.err == ""
    content = destination.read_text(encoding="utf-8")
    assert "news.xml?a=1&amp;b=2" in content
    assert second in content
    assert "Sitemap" in content
    assert "## Current blocking findings" in content


@pytest.mark.parametrize("mode,expected", [
    ("pass", 0), ("changed", 1),
])
def test_report_dir_produces_all_three_offline_once(
    tmp_path, monkeypatch, capsys, mode, expected,
):
    reviewed, build = _inputs(tmp_path)
    if mode == "changed":
        (build / "robots.txt").write_bytes(b"User-agent: *\nDisallow: /\n")
    destination = tmp_path / "reports"
    destination.mkdir()
    monkeypatch.setattr(socket, "socket", lambda *_a, **_kw: (
        (_ for _ in ()).throw(AssertionError("no network"))
    ))
    assert main([str(reviewed), str(build), "--report-dir", str(destination)]) == expected
    assert capsys.readouterr().out == ""
    names = {p.name for p in destination.iterdir()}
    assert names == {"seo-robots.json", "seo-robots.xml", "seo-robots.md"}
    report = json.loads((destination / "seo-robots.json").read_text(encoding="utf-8"))
    assert report["status"] == ("pass" if expected == 0 else "fail")
    junit = ET.parse(destination / "seo-robots.xml").getroot()
    assert int(junit.attrib["failures"]) == expected
    assert f"**Result: {report['status'].upper()}**" in (
        destination / "seo-robots.md"
    ).read_text(encoding="utf-8")

    # Reusing the directory must not overwrite any artifact or partially update it.
    contents = {name: (destination / name).read_bytes() for name in names}
    assert main([str(reviewed), str(build), "--report-dir", str(destination)]) == 2
    assert {name: (destination / name).read_bytes() for name in names} == contents


@pytest.mark.parametrize("unsafe", [
    "inside_build", "missing_dir", "existing_json", "existing_junit", "existing_md",
])
def test_report_dir_preflight_fails_before_any_artifact(
    tmp_path, capsys, unsafe,
):
    reviewed, build = _inputs(tmp_path)
    dest = {
        "inside_build": build / "reports",
        "missing_dir": tmp_path / "absent",
    }.get(unsafe, tmp_path / "reports")
    if unsafe != "missing_dir":
        dest.mkdir()
    names = ["seo-robots.json", "seo-robots.xml", "seo-robots.md"]
    existing_name = {
        "existing_json": names[0],
        "existing_junit": names[1],
        "existing_md": names[2],
    }.get(unsafe)
    if existing_name:
        (dest / existing_name).write_bytes(b"KEEP")
    original = (build / "robots.txt").read_bytes()
    assert main([str(reviewed), str(build), "--report-dir", str(dest)]) == 2
    assert "Invalid input:" in capsys.readouterr().err
    assert (build / "robots.txt").read_bytes() == original
    for name in names:
        if existing_name == name:
            assert (dest / name).read_bytes() == b"KEEP"
        else:
            assert not (dest / name).exists()


def test_explicit_json_junit_markdown_and_annotations_share_one_offline_pass(
    tmp_path, capsys,
):
    reviewed, build = _inputs(tmp_path)
    (build / "robots.txt").unlink()
    paths = [tmp_path / f"robots.{extension}" for extension in ("json", "xml", "md")]
    assert main([
        str(reviewed), str(build),
        "--json-file", str(paths[0]),
        "--junit-file", str(paths[1]),
        "--markdown-file", str(paths[2]),
        "--github-annotations",
    ]) == 1
    output = capsys.readouterr()
    assert output.err == "" and output.out.count("::error ") == 1
    assert json.loads(paths[0].read_text(encoding="utf-8"))["reason"] == "missing"
    assert ET.parse(paths[1]).getroot().attrib["failures"] == "1"
    assert "missing" in paths[2].read_text(encoding="utf-8")


def test_partial_markdown_export_rolls_back_owned_json_junit_without_annotations(
    tmp_path, monkeypatch, capsys,
):
    reviewed, build = _inputs(tmp_path)
    (build / "robots.txt").unlink()
    json_path, xml_path, md_path = (
        tmp_path / "robots.json", tmp_path / "robots.xml", tmp_path / "robots.md",
    )
    original_open = Path.open

    class Interrupted:
        def __init__(self, inner):
            self.inner = inner

        def __enter__(self):
            self.inner.__enter__()
            return self

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

        def fileno(self):
            return self.inner.fileno()

        def write(self, content):
            self.inner.write(content[:9])
            self.inner.flush()
            raise OSError("injected Markdown write interruption")

    def interrupted_open(path, mode="r", *args, **kwargs):
        f = original_open(path, mode, *args, **kwargs)
        return Interrupted(f) if path == md_path and mode == "x" else f

    monkeypatch.setattr(Path, "open", interrupted_open)
    assert main([
        str(reviewed), str(build),
        "--json-file", str(json_path), "--junit-file", str(xml_path),
        "--markdown-file", str(md_path), "--github-annotations",
    ]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "injected Markdown write interruption" in output.err
    assert all(not path.exists() for path in (json_path, xml_path, md_path))


@pytest.mark.parametrize("extra", [
    ["--json"], ["--json-file"], ["--junit-file"], ["--markdown-file"],
])
def test_report_dir_rejects_conflicting_modes(tmp_path, capsys, extra):
    reviewed, build = _inputs(tmp_path)
    destination = tmp_path / "reports"
    destination.mkdir()
    arguments = extra if extra == ["--json"] else [*extra, str(tmp_path / "other")]
    with pytest.raises(SystemExit) as error:
        main([str(reviewed), str(build), "--report-dir", str(destination), *arguments])
    assert error.value.code == 2
    assert "report-dir" in capsys.readouterr().err
    assert list(destination.iterdir()) == []


def test_markdown_refuses_json_stdout(tmp_path, capsys):
    reviewed, build = _inputs(tmp_path)
    with pytest.raises(SystemExit) as error:
        main([
            str(reviewed), str(build), "--json", "--markdown-file",
            str(tmp_path / "report.md"),
        ])
    assert error.value.code == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_report_dir_invalid_input_creates_nothing(tmp_path, capsys):
    reviewed, build = _inputs(tmp_path)
    destination = tmp_path / "reports"
    destination.mkdir()
    assert main([
        str(reviewed.with_name("missing-policy.txt")), str(build),
        "--report-dir", str(destination),
    ]) == 2
    assert not list(destination.iterdir())
    assert "Invalid input:" in capsys.readouterr().err
