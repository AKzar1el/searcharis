"""Reviewed robots.txt output must not silently drift between static builds."""

import hashlib
import json
import socket
from pathlib import Path

import pytest

from searcharis.robots_policy_cli import main


def _fixture(tmp_path):
    build = tmp_path / "dist"
    build.mkdir()
    reviewed = tmp_path / "production-robots.txt"
    content = b"User-agent: *\nAllow: /\nSitemap: https://example.test/sitemap.xml\n"
    reviewed.write_bytes(content)
    (build / "robots.txt").write_bytes(content)
    return reviewed, build


def _run(capsys, reviewed, build, *flags):
    code = main([str(reviewed), str(build), "--json", *flags])
    stream = capsys.readouterr()
    return code, json.loads(stream.out), stream.err


def test_identical_reviewed_robots_policy_passes_offline(tmp_path, capsys, monkeypatch):
    reviewed, build = _fixture(tmp_path)

    def deny_socket(*args, **kwargs):
        raise AssertionError("offline robots policy validation must not open sockets")

    monkeypatch.setattr(socket, "socket", deny_socket)
    code, payload, stderr = _run(capsys, reviewed, build)
    assert code == 0 and stderr == ""
    assert payload["status"] == "pass"
    digest = hashlib.sha256(reviewed.read_bytes()).hexdigest()
    assert payload["expected_sha256"] == payload["observed_sha256"] == digest
    assert payload["artifact"] == "robots.txt"


def test_staging_disallow_all_drift_blocks_release_without_interpreting_rules(
    tmp_path, capsys,
):
    reviewed, build = _fixture(tmp_path)
    (build / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    code, report, stderr = _run(capsys, reviewed, build)
    assert code == 1 and stderr == ""
    assert report["status"] == "fail"
    assert report["expected_sha256"] != report["observed_sha256"]
    assert "Disallow" not in json.dumps(report)


def test_missing_built_robots_file_is_a_release_mismatch(tmp_path, capsys):
    reviewed, build = _fixture(tmp_path)
    (build / "robots.txt").unlink()
    code, report, stderr = _run(capsys, reviewed, build)
    assert code == 1 and stderr == ""
    assert report["status"] == "fail"
    assert report["observed_sha256"] is None


def test_wrong_case_robots_filename_is_missing_on_all_ci_platforms(tmp_path, capsys):
    reviewed, build = _fixture(tmp_path)
    (build / "robots.txt").rename(build / "Robots.txt")
    code, report, stderr = _run(capsys, reviewed, build)
    assert code == 1 and stderr == ""
    assert report["reason"] == "missing"
    assert report["observed_sha256"] is None


@pytest.mark.parametrize("invalid", ["missing_reviewed", "missing_root", "reference_in_build"])
def test_bad_reference_or_build_root_is_input_error(tmp_path, capsys, invalid):
    reviewed, build = _fixture(tmp_path)
    if invalid == "missing_reviewed":
        reviewed = tmp_path / "absent.txt"
    elif invalid == "missing_root":
        build = tmp_path / "absent-dist"
    else:
        reviewed = build / "robots.txt"
    code, payload, stderr = _run(capsys, reviewed, build)
    assert code == 2 and stderr == ""
    assert payload["status"] == "error"
    assert isinstance(payload["error"], str) and payload["error"]


@pytest.mark.parametrize("which", ["reviewed", "built"])
def test_oversized_policy_is_rejected_before_hashing(tmp_path, capsys, which):
    reviewed, build = _fixture(tmp_path)
    target = reviewed if which == "reviewed" else build / "robots.txt"
    target.write_bytes(b"x" * (512_000 + 1))
    code, payload, stderr = _run(capsys, reviewed, build)
    assert code == 2 and stderr == ""
    assert payload["status"] == "error"
    assert "limit" in payload["error"].lower()


def test_built_robots_symlink_cannot_escape_build(tmp_path, capsys):
    reviewed, build = _fixture(tmp_path)
    target = tmp_path / "external-robots.txt"
    target.write_bytes(reviewed.read_bytes())
    (build / "robots.txt").unlink()
    try:
        (build / "robots.txt").symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks not available in this environment: {exc}")
    code, payload, _ = _run(capsys, reviewed, build)
    assert code == 2
    assert payload["status"] == "error"


def test_plain_output_preserves_exit_code_and_actionable_digest(tmp_path, capsys):
    reviewed, build = _fixture(tmp_path)
    assert main([str(reviewed), str(build)]) == 0
    assert "PASS" in capsys.readouterr().out
    (build / "robots.txt").write_bytes(b"User-agent: *\nDisallow: /\n")
    assert main([str(reviewed), str(build)]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "SHA-256" in out


def test_checked_in_policy_smoke_matches_documented_exit_codes(capsys):
    root = Path(__file__).resolve().parents[2] / "examples/seo-contract/robots-policy"
    good, report, _ = _run(capsys, root / "reviewed.txt", root / "passing")
    bad, drift, _ = _run(capsys, root / "reviewed.txt", root / "blocked")
    assert good == 0 and report["status"] == "pass"
    assert bad == 1 and drift["reason"] == "changed"
