"""Reviewed Cloudflare Pages _headers must survive static builds unchanged."""

import hashlib
import json
import os
import socket
from pathlib import Path

import pytest

from searcharis.headers_policy_cli import main


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    build = tmp_path / "dist"
    build.mkdir()
    reviewed = tmp_path / "approved-headers"
    content = (
        b"# Reviewed indexing behavior for static Pages assets\n"
        b"/*\n  X-Content-Type-Options: nosniff\n"
        b"https://:project.pages.dev/*\n  X-Robots-Tag: noindex\n"
    )
    reviewed.write_bytes(content)
    (build / "_headers").write_bytes(content)
    return reviewed, build


def _check(capsys, reviewed: Path, build: Path):
    status = main([str(reviewed), str(build), "--json"])
    result = capsys.readouterr()
    return status, json.loads(result.out), result.err


def test_identical_headers_pass_offline(tmp_path, capsys, monkeypatch):
    reviewed, build = _fixture(tmp_path)
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: pytest.fail("network request forbidden"))
    status, report, err = _check(capsys, reviewed, build)
    digest = hashlib.sha256(reviewed.read_bytes()).hexdigest()
    assert status == 0 and err == ""
    assert report == {
        "status": "pass",
        "artifact": "_headers",
        "reason": None,
        "expected_sha256": digest,
        "observed_sha256": digest,
    }


@pytest.mark.parametrize("content", [
    b"/*\n  X-Content-Type-Options: nosniff\n",
    b"https://:project.pages.dev/*\n  X-Robots-Tag: index\n",
    b"https://:project.pages.dev/*\n  X-Robots-Tag: noindex\n/*\n  X-Content-Type-Options: nosniff\n",
])
def test_header_removal_modification_and_order_drift_block(tmp_path, capsys, content):
    reviewed, build = _fixture(tmp_path)
    (build / "_headers").write_bytes(content)
    status, report, err = _check(capsys, reviewed, build)
    assert status == 1 and err == ""
    assert report["reason"] == "changed"
    assert report["expected_sha256"] != report["observed_sha256"]
    assert "noindex" not in json.dumps(report)


def test_missing_headers_blocks_release(tmp_path, capsys):
    reviewed, build = _fixture(tmp_path)
    (build / "_headers").unlink()
    status, report, _ = _check(capsys, reviewed, build)
    assert status == 1 and report["reason"] == "missing"
    assert report["observed_sha256"] is None


def test_wrong_cased_filename_is_missing_on_windows(tmp_path, capsys):
    reviewed, build = _fixture(tmp_path)
    (build / "_headers").rename(build / "_Headers")
    status, report, _ = _check(capsys, reviewed, build)
    assert status == 1 and report["reason"] == "missing"


@pytest.mark.parametrize("bad", ["missing_reviewed", "missing_build", "reviewed_inside_build", "file_as_build"])
def test_invalid_paths_fail_closed(tmp_path, capsys, bad):
    reviewed, build = _fixture(tmp_path)
    if bad == "missing_reviewed":
        reviewed = tmp_path / "missing"
    elif bad == "missing_build":
        build = tmp_path / "missing-build"
    elif bad == "reviewed_inside_build":
        reviewed = build / "_headers"
    else:
        build = reviewed
    status, report, err = _check(capsys, reviewed, build)
    assert status == 2 and err == ""
    assert report["status"] == "error" and report["error"]


@pytest.mark.parametrize("location", ["reviewed", "built"])
def test_oversized_headers_file_is_rejected(tmp_path, capsys, location):
    reviewed, build = _fixture(tmp_path)
    target = reviewed if location == "reviewed" else build / "_headers"
    target.write_bytes(b"X" * 8_500_001)
    status, report, _ = _check(capsys, reviewed, build)
    assert status == 2 and "limit" in report["error"].lower()


def test_built_headers_symlink_is_invalid(tmp_path, capsys):
    reviewed, build = _fixture(tmp_path)
    (build / "_headers").unlink()
    outside = tmp_path / "outside-headers"
    outside.write_bytes(reviewed.read_bytes())
    try:
        (build / "_headers").symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    status, report, _ = _check(capsys, reviewed, build)
    assert status == 2 and report["status"] == "error"


def test_built_headers_directory_is_invalid(tmp_path, capsys):
    reviewed, build = _fixture(tmp_path)
    (build / "_headers").unlink()
    (build / "_headers").mkdir()
    status, report, _ = _check(capsys, reviewed, build)
    assert status == 2 and report["status"] == "error"


def test_hardlinked_built_headers_cannot_alias_reviewed_reference(tmp_path, capsys):
    reviewed, build = _fixture(tmp_path)
    built = build / "_headers"
    built.unlink()
    try:
        os.link(reviewed, built)
    except OSError as error:
        pytest.skip(f"Hard links unavailable: {error}")

    # A build write can otherwise silently change the reviewed reference too.
    built.write_bytes(b"/*\n  X-Robots-Tag: index\n")
    assert reviewed.read_bytes() == built.read_bytes()
    status, report, err = _check(capsys, reviewed, build)
    assert status == 2 and err == ""
    assert report["status"] == "error"
    assert "hard link" in report["error"].lower()


def test_empty_headers_policy_may_match(tmp_path, capsys):
    reviewed, build = _fixture(tmp_path)
    reviewed.write_bytes(b"")
    (build / "_headers").write_bytes(b"")
    status, report, _ = _check(capsys, reviewed, build)
    assert status == 0 and report["status"] == "pass"


def test_human_output_and_invalid_input_exit_codes(tmp_path, capsys):
    reviewed, build = _fixture(tmp_path)
    assert main([str(reviewed), str(build)]) == 0
    assert "PASS" in capsys.readouterr().out
    (build / "_headers").write_bytes(b"/*\n  X-Robots-Tag: noindex\n")
    assert main([str(reviewed), str(build)]) == 1
    assert "FAIL" in capsys.readouterr().out
    assert main([str(tmp_path / "missing"), str(build)]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "Invalid input" in output.err


def test_checked_in_pages_headers_first_use_examples(capsys):
    root = Path(__file__).resolve().parents[2] / "examples/seo-contract/headers-policy"
    approved = root / "reviewed.txt"
    for subdir, expected_status, expected_reason in (
        ("passing", 0, None),
        ("drifted", 1, "changed"),
    ):
        status, report, err = _check(capsys, approved, root / subdir)
        assert status == expected_status and err == ""
        assert report["status"] == ("pass" if status == 0 else "fail")
        assert report["reason"] == expected_reason


@pytest.mark.parametrize("changed", [False, True])
def test_headers_json_file_is_direct_utf8_ci_artifact(tmp_path, capsys, monkeypatch, changed):
    reviewed, build = _fixture(tmp_path)
    monkeypatch.setattr(
        socket, "socket",
        lambda *args, **kwargs: pytest.fail("offline header reporting cannot fetch URLs"),
    )
    if changed:
        (build / "_headers").write_bytes(b"/*\n  X-Robots-Tag: index\n")
    target = tmp_path / "report.json"
    code = main([str(reviewed), str(build), "--json-file", str(target)])
    output = capsys.readouterr()
    assert code == (1 if changed else 0)
    assert output.out == output.err == ""
    raw = target.read_bytes()
    assert raw.startswith(b"{") and b"\x00" not in raw and not raw.startswith(b"\xef\xbb\xbf")
    report = json.loads(raw.decode("utf-8"))
    assert report["status"] == ("fail" if changed else "pass")
    assert report["reason"] == ("changed" if changed else None)
    assert report["artifact"] == "_headers"


def test_headers_json_file_never_overwrites_existing(tmp_path, capsys):
    reviewed, build = _fixture(tmp_path)
    target = tmp_path / "report.json"
    target.write_text("existing report", encoding="utf-8")
    assert main([str(reviewed), str(build), "--json-file", str(target)]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "Invalid input" in output.err
    assert target.read_text(encoding="utf-8") == "existing report"


@pytest.mark.parametrize("destination", ["inside_build", "missing_parent", "symlink"])
def test_headers_json_file_rejects_unsafe_destinations(tmp_path, capsys, destination):
    reviewed, build = _fixture(tmp_path)
    target = tmp_path / "report.json"
    if destination == "inside_build":
        target = build / "report.json"
    elif destination == "missing_parent":
        target = tmp_path / "missing" / "report.json"
    else:
        target.write_text("another report", encoding="utf-8")
        link = tmp_path / "linked.json"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError) as error:
            pytest.skip(f"symlinks unavailable: {error}")
        target = link
    assert main([str(reviewed), str(build), "--json-file", str(target)]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "Invalid input" in output.err
    if destination == "inside_build":
        assert not target.exists()
    elif destination == "symlink":
        assert target.read_text(encoding="utf-8") == "another report"


def test_headers_invalid_input_does_not_create_json_artifact(tmp_path, capsys):
    reviewed, build = _fixture(tmp_path)
    target = tmp_path / "report.json"
    assert main([str(tmp_path / "missing"), str(build), "--json-file", str(target)]) == 2
    assert not target.exists()
    assert "Invalid input" in capsys.readouterr().err


def test_headers_interrupted_json_artifact_is_identity_checked(tmp_path, capsys, monkeypatch):
    reviewed, build = _fixture(tmp_path)
    target = tmp_path / "report.json"
    original_open = Path.open

    class BrokenFile:
        def __init__(self, path, **kwargs):
            self.file = original_open(path, "x", **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.file.__exit__(*args)

        def fileno(self):
            return self.file.fileno()

        def write(self, content):
            self.file.write(content[:8])
            self.file.flush()
            raise OSError("simulated interrupted header report")

    def broken_open(path, mode="r", **kwargs):
        if path == target and mode == "x":
            return BrokenFile(path, **kwargs)
        return original_open(path, mode, **kwargs)

    monkeypatch.setattr(Path, "open", broken_open)
    assert main([str(reviewed), str(build), "--json-file", str(target)]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "simulated interrupted" in output.err
    assert not target.exists()
