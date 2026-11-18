"""Reviewed Cloudflare Pages redirect bytes must survive the static build."""

import hashlib
import json
import socket
from pathlib import Path

import pytest

from searcharis.redirects_policy_cli import main


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "dist"
    root.mkdir()
    source = tmp_path / "reviewed-redirects"
    content = (
        b"# Approved public redirects\n"
        b"/legacy /new-home 301\n"
        b"/blog/* /posts/:splat 302\n"
    )
    source.write_bytes(content)
    (root / "_redirects").write_bytes(content)
    return source, root


def _run(capsys, source: Path, build: Path):
    exit_code = main([str(source), str(build), "--json"])
    output = capsys.readouterr()
    return exit_code, json.loads(output.out), output.err


def test_identical_reviewed_redirects_pass_without_network(tmp_path, capsys, monkeypatch):
    source, build = _fixture(tmp_path)

    def no_network(*args, **kwargs):
        raise AssertionError("redirect artifact verification must not fetch URLs")

    monkeypatch.setattr(socket, "socket", no_network)
    exit_code, report, errors = _run(capsys, source, build)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    assert exit_code == 0 and errors == ""
    assert report == {
        "status": "pass",
        "artifact": "_redirects",
        "reason": None,
        "expected_sha256": digest,
        "observed_sha256": digest,
    }


@pytest.mark.parametrize("content", [
    b"/legacy /new-home 302\n/blog/* /posts/:splat 302\n",
    b"/legacy /gone 301\n",
    b"/legacy /new-home 301\n",
])
def test_changed_rules_or_order_fail_without_claiming_server_semantics(
    tmp_path, capsys, content,
):
    source, build = _fixture(tmp_path)
    (build / "_redirects").write_bytes(content)
    exit_code, report, errors = _run(capsys, source, build)
    assert exit_code == 1 and errors == ""
    assert report["reason"] == "changed"
    assert report["expected_sha256"] != report["observed_sha256"]
    assert "legacy" not in json.dumps(report)


def test_missing_redirects_artifact_is_a_blocking_mismatch(tmp_path, capsys):
    source, build = _fixture(tmp_path)
    (build / "_redirects").unlink()
    exit_code, report, errors = _run(capsys, source, build)
    assert exit_code == 1 and errors == ""
    assert report["reason"] == "missing"
    assert report["observed_sha256"] is None


def test_wrong_case_is_missing_even_on_case_insensitive_windows(tmp_path, capsys):
    source, build = _fixture(tmp_path)
    (build / "_redirects").rename(build / "_Redirects")
    exit_code, report, _ = _run(capsys, source, build)
    assert exit_code == 1 and report["reason"] == "missing"


@pytest.mark.parametrize("invalid", [
    "missing_reviewed", "missing_build", "reviewed_inside_build", "build_is_file",
])
def test_unsafe_or_missing_input_is_exit_two(tmp_path, capsys, invalid):
    source, build = _fixture(tmp_path)
    if invalid == "missing_reviewed":
        source = tmp_path / "missing"
    elif invalid == "missing_build":
        build = tmp_path / "missing-build"
    elif invalid == "reviewed_inside_build":
        source = build / "_redirects"
    else:
        build = source
    exit_code, report, errors = _run(capsys, source, build)
    assert exit_code == 2 and errors == ""
    assert report["status"] == "error"
    assert report["error"]


@pytest.mark.parametrize("where", ["reviewed", "built"])
def test_oversized_redirect_file_is_rejected(tmp_path, capsys, where):
    source, build = _fixture(tmp_path)
    target = source if where == "reviewed" else build / "_redirects"
    target.write_bytes(b"X" * (8_500_000 + 1))
    exit_code, report, _ = _run(capsys, source, build)
    assert exit_code == 2
    assert "limit" in report["error"].lower()


def test_built_symlink_cannot_follow_outside_build(tmp_path, capsys):
    source, build = _fixture(tmp_path)
    target = tmp_path / "external"
    target.write_bytes(source.read_bytes())
    (build / "_redirects").unlink()
    try:
        (build / "_redirects").symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlinks not available on this host: {exc}")
    exit_code, report, _ = _run(capsys, source, build)
    assert exit_code == 2 and report["status"] == "error"


def test_built_directory_named_redirects_is_invalid(tmp_path, capsys):
    source, build = _fixture(tmp_path)
    (build / "_redirects").unlink()
    (build / "_redirects").mkdir()
    exit_code, report, _ = _run(capsys, source, build)
    assert exit_code == 2 and report["status"] == "error"


def test_empty_reviewed_redirects_file_can_match_exactly(tmp_path, capsys):
    source, build = _fixture(tmp_path)
    source.write_bytes(b"")
    (build / "_redirects").write_bytes(b"")
    exit_code, report, _ = _run(capsys, source, build)
    assert exit_code == 0 and report["status"] == "pass"


def test_plain_output_is_bounded_and_carries_same_status(tmp_path, capsys):
    source, build = _fixture(tmp_path)
    assert main([str(source), str(build)]) == 0
    assert "PASS" in capsys.readouterr().out
    (build / "_redirects").write_bytes(b"/legacy /somewhere 301\n")
    assert main([str(source), str(build)]) == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert "FAIL" in output.out and "SHA-256" in output.out


def test_invalid_plain_input_does_not_emit_pass(tmp_path, capsys):
    source, build = _fixture(tmp_path)
    assert main([str(tmp_path / "no-such-policy"), str(build)]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "Invalid input" in output.err
