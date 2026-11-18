"""One offline command checks both reviewed Cloudflare Pages static artifacts."""

import json
import socket
from pathlib import Path

import pytest

from searcharis.pages_policy_cli import main


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    build = tmp_path / "dist"
    build.mkdir()
    redirects = tmp_path / "reviewed-redirects"
    headers = tmp_path / "reviewed-headers"
    redirects.write_bytes(b"/old /new 301\n")
    headers.write_bytes(b"/*\n  X-Robots-Tag: noindex\n")
    (build / "_redirects").write_bytes(redirects.read_bytes())
    (build / "_headers").write_bytes(headers.read_bytes())
    return redirects, headers, build


def _run(capsys, redirects: Path, headers: Path, build: Path, *flags: str):
    code = main([str(redirects), str(headers), str(build), *flags])
    printed = capsys.readouterr()
    return code, printed.out, printed.err


def test_combined_pass_is_one_offline_json_report(tmp_path, capsys, monkeypatch):
    redirects, headers, build = _fixture(tmp_path)
    monkeypatch.setattr(
        socket, "socket",
        lambda *a, **kw: pytest.fail("offline Pages gate must never fetch URLs"),
    )
    code, out, err = _run(capsys, redirects, headers, build, "--json")
    report = json.loads(out)
    assert code == 0 and err == ""
    assert report["status"] == "pass"
    assert [item["artifact"] for item in report["checks"]] == ["_redirects", "_headers"]
    assert all(item["status"] == "pass" for item in report["checks"])
    assert not any("error" in item for item in report["checks"])


@pytest.mark.parametrize("drift", ["_redirects", "_headers", "both", "both_missing"])
def test_one_or_both_drifts_block_without_masking_other_check(tmp_path, capsys, drift):
    redirects, headers, build = _fixture(tmp_path)
    if drift in {"_redirects", "both"}:
        (build / "_redirects").write_bytes(b"/old /other 302\n")
    if drift in {"_headers", "both"}:
        (build / "_headers").write_bytes(b"/*\n  X-Robots-Tag: index\n")
    if drift == "both_missing":
        (build / "_redirects").unlink()
        (build / "_headers").unlink()
    code, out, err = _run(capsys, redirects, headers, build, "--json")
    report = json.loads(out)
    assert code == 1 and err == "" and report["status"] == "fail"
    assert len(report["checks"]) == 2
    by_name = {item["artifact"]: item for item in report["checks"]}
    failing = {"_redirects", "_headers"} if drift.startswith("both") else {drift}
    assert {name for name, item in by_name.items() if item["status"] == "fail"} == failing
    for name in failing:
        assert by_name[name]["reason"] == ("missing" if drift == "both_missing" else "changed")


def test_plain_output_covers_both_checks_and_preserves_failure(tmp_path, capsys):
    redirects, headers, build = _fixture(tmp_path)
    (build / "_headers").unlink()
    code, out, err = _run(capsys, redirects, headers, build)
    assert code == 1 and err == ""
    assert "PASS _redirects" in out
    assert "FAIL _headers missing" in out


@pytest.mark.parametrize("missing", ["redirects", "headers", "build"])
def test_invalid_input_is_exit_two_with_explicit_error_not_pass(tmp_path, capsys, missing):
    redirects, headers, build = _fixture(tmp_path)
    if missing == "redirects":
        redirects = tmp_path / "missing-redirects"
    elif missing == "headers":
        headers = tmp_path / "missing-headers"
    else:
        build = tmp_path / "missing-dist"
    code, out, err = _run(capsys, redirects, headers, build, "--json")
    report = json.loads(out)
    assert code == 2 and err == ""
    assert report["status"] == "error"
    assert report["errors"]
    assert any(item["artifact"] in {"_redirects", "_headers"} for item in report["errors"])


@pytest.mark.parametrize("drift", [False, True])
def test_combined_json_file_is_create_only_utf8_and_keeps_exit(tmp_path, capsys, drift):
    redirects, headers, build = _fixture(tmp_path)
    if drift:
        (build / "_redirects").write_bytes(b"/old /wrong 301\n")
    target = tmp_path / "combined.json"
    code, out, err = _run(capsys, redirects, headers, build, "--json-file", str(target))
    assert code == (1 if drift else 0) and out == err == ""
    raw = target.read_bytes()
    assert raw.startswith(b"{") and b"\\x00" not in raw and not raw.startswith(b"\\xef\\xbb\\xbf")
    report = json.loads(raw.decode("utf-8"))
    assert report["status"] == ("fail" if drift else "pass")
    assert len(report["checks"]) == 2


def test_existing_combined_report_is_preserved(tmp_path, capsys):
    redirects, headers, build = _fixture(tmp_path)
    target = tmp_path / "combined.json"
    target.write_text("owned by another job", encoding="utf-8")
    code, out, err = _run(capsys, redirects, headers, build, "--json-file", str(target))
    assert code == 2 and out == "" and "Invalid input" in err
    assert target.read_text(encoding="utf-8") == "owned by another job"


@pytest.mark.parametrize("destination", ["inside_build", "missing_parent", "symlink"])
def test_combined_report_rejects_unsafe_destination(tmp_path, capsys, destination):
    redirects, headers, build = _fixture(tmp_path)
    target = tmp_path / "combined.json"
    if destination == "inside_build":
        target = build / "combined.json"
    elif destination == "missing_parent":
        target = tmp_path / "missing-parent" / "combined.json"
    else:
        target.write_text("existing", encoding="utf-8")
        link = tmp_path / "linked.json"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError) as error:
            pytest.skip(f"symlinks not available: {error}")
        target = link
    code, out, err = _run(capsys, redirects, headers, build, "--json-file", str(target))
    assert code == 2 and out == "" and "Invalid input" in err
    if destination == "inside_build":
        assert not target.exists()
    elif destination == "symlink":
        assert target.read_text(encoding="utf-8") == "existing"


def test_invalid_input_never_creates_combined_report(tmp_path, capsys):
    redirects, headers, build = _fixture(tmp_path)
    target = tmp_path / "combined.json"
    code, out, err = _run(
        capsys, tmp_path / "missing-redirects", headers, build,
        "--json-file", str(target),
    )
    assert code == 2 and out == "" and "Invalid input" in err
    assert not target.exists()


def test_combined_preserves_drift_evidence_when_other_input_is_invalid(tmp_path, capsys):
    redirects, headers, build = _fixture(tmp_path)
    (build / "_redirects").write_bytes(b"/old /regression 302\n")
    headers.unlink()
    code, out, err = _run(capsys, redirects, headers, build, "--json")
    report = json.loads(out)
    assert code == 2 and err == ""
    assert report["status"] == "error"
    assert [(item["artifact"], item["status"]) for item in report["checks"]] == [
        ("_redirects", "fail"),
    ]
    assert report["errors"][0]["artifact"] == "_headers"


def test_combined_interrupted_report_creation_cleans_only_its_file(
    tmp_path, capsys, monkeypatch,
):
    redirects, headers, build = _fixture(tmp_path)
    target = tmp_path / "combined.json"

    def interrupt_serialization(*args, **kwargs):
        raise ValueError("simulated JSON serialization interruption")

    monkeypatch.setattr("searcharis.pages_policy_cli.json.dumps", interrupt_serialization)
    code, out, err = _run(capsys, redirects, headers, build, "--json-file", str(target))
    assert code == 2 and out == ""
    assert "Invalid input" in err
    assert not target.exists()


def test_json_stdout_and_json_file_are_mutually_exclusive(tmp_path):
    redirects, headers, build = _fixture(tmp_path)
    with pytest.raises(SystemExit) as raised:
        main([str(redirects), str(headers), str(build), "--json", "--json-file", str(tmp_path / "x")])
    assert raised.value.code == 2


def test_github_annotations_pass_emits_no_commands(tmp_path, capsys):
    redirects, headers, build = _fixture(tmp_path)
    code, out, err = _run(capsys, redirects, headers, build, "--github-annotations")
    assert code == 0 and err == ""
    assert "PASS _redirects" in out and "PASS _headers" in out
    assert "::error" not in out


def test_github_annotations_pass_with_report_is_quiet(tmp_path, capsys):
    redirects, headers, build = _fixture(tmp_path)
    target = tmp_path / "pages.json"
    code, out, err = _run(
        capsys, redirects, headers, build,
        "--json-file", str(target), "--github-annotations",
    )
    assert code == 0 and out == err == ""
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "pass"


@pytest.mark.parametrize("artifact", ["_redirects", "_headers"])
def test_github_annotations_identify_only_the_drifting_artifact(tmp_path, capsys, artifact):
    redirects, headers, build = _fixture(tmp_path)
    (build / artifact).unlink()
    code, out, err = _run(capsys, redirects, headers, build, "--github-annotations")
    assert code == 1 and err == ""
    commands = [line for line in out.splitlines() if line.startswith("::error")]
    assert len(commands) == 1
    assert commands[0].startswith(f"::error title=SEO Pages {artifact}::")
    assert "missing" in commands[0].lower()
    other = "_headers" if artifact == "_redirects" else "_redirects"
    assert other not in commands[0]


def test_github_annotations_report_both_changes_without_forged_lines(tmp_path, capsys):
    redirects, headers, build = _fixture(tmp_path)
    (build / "_redirects").write_bytes(b"/wrong /changed 301\n")
    (build / "_headers").write_bytes(b"/*\n X-Robots-Tag: index\n")
    code, out, err = _run(capsys, redirects, headers, build, "--github-annotations")
    assert code == 1 and err == ""
    commands = [line for line in out.splitlines() if line.startswith("::error")]
    assert len(commands) == 2
    assert "_redirects" in commands[0] and "changed" in commands[0].lower()
    assert "_headers" in commands[1] and "changed" in commands[1].lower()
    assert all(line.count("::") == 2 for line in commands)


def test_github_annotations_and_utf8_json_file_coexist_after_write(tmp_path, capsys):
    redirects, headers, build = _fixture(tmp_path)
    (build / "_headers").unlink()
    target = tmp_path / "pages.json"
    code, out, err = _run(
        capsys, redirects, headers, build,
        "--json-file", str(target), "--github-annotations",
    )
    assert code == 1 and err == ""
    assert out.splitlines() == [
        "::error title=SEO Pages _headers::Compiled _headers missing from the static build"
    ]
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "fail"


def test_github_annotations_not_emitted_when_json_report_write_fails(tmp_path, capsys):
    redirects, headers, build = _fixture(tmp_path)
    (build / "_redirects").unlink()
    target = tmp_path / "pages.json"
    target.write_text("preserve me", encoding="utf-8")
    code, out, err = _run(
        capsys, redirects, headers, build,
        "--json-file", str(target), "--github-annotations",
    )
    assert code == 2 and out == "" and "already exists" in err
    assert target.read_text(encoding="utf-8") == "preserve me"


def test_github_annotations_invalid_input_does_not_fake_drift(tmp_path, capsys):
    redirects, headers, build = _fixture(tmp_path)
    code, out, err = _run(
        capsys, tmp_path / "missing-reviewed-redirects", headers, build,
        "--github-annotations",
    )
    assert code == 2 and out == "" and "Invalid input (_redirects)" in err
    assert "::error" not in err


def test_github_annotations_rejects_json_stdout_mode(tmp_path):
    redirects, headers, build = _fixture(tmp_path)
    with pytest.raises(SystemExit) as raised:
        main([str(redirects), str(headers), str(build), "--json", "--github-annotations"])
    assert raised.value.code == 2
