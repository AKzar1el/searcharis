"""One offline command checks both reviewed Cloudflare Pages static artifacts."""

import json
import os
import socket
import xml.etree.ElementTree as ET
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


def test_combined_gate_rejects_hardlinked_headers_reference(tmp_path, capsys):
    redirects, headers, build = _fixture(tmp_path)
    built = build / "_headers"
    built.unlink()
    try:
        os.link(headers, built)
    except OSError as error:
        pytest.skip(f"Hard links unavailable: {error}")

    code, out, err = _run(capsys, redirects, headers, build, "--json")
    report = json.loads(out)
    assert code == 2 and err == "" and report["status"] == "error"
    assert [item["artifact"] for item in report["checks"]] == ["_redirects"]
    assert report["checks"][0]["status"] == "pass"
    assert report["errors"][0]["artifact"] == "_headers"
    assert "hard link" in report["errors"][0]["error"].lower()


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


@pytest.mark.parametrize("artifact", ["_redirects", "_headers"])
def test_opt_in_drift_location_reports_line_column_and_byte_offset(
    tmp_path, capsys, artifact,
):
    redirects, headers, build = _fixture(tmp_path)
    original = (build / artifact).read_bytes()
    marker = b"noindex" if artifact == "_headers" else b"new"
    position = original.index(marker)
    replacement = b"index" if artifact == "_headers" else b"bad"
    (build / artifact).write_bytes(
        original[:position] + replacement + original[position + len(marker):]
    )

    code, out, err = _run(capsys, redirects, headers, build, "--json", "--locate-drift")
    assert code == 1 and err == ""
    checks = {item["artifact"]: item for item in json.loads(out)["checks"]}
    assert checks[artifact]["drift_location"] == {
        "line": original[:position].count(b"\n") + 1,
        "byte_column": position - original.rfind(b"\n", 0, position),
        "byte_offset": position,
    }
    other = "_headers" if artifact == "_redirects" else "_redirects"
    assert "drift_location" not in checks[other]


def test_opt_in_drift_location_handles_trailing_bytes_and_missing_artifact(
    tmp_path, capsys,
):
    redirects, headers, build = _fixture(tmp_path)
    old = (build / "_redirects").read_bytes()
    (build / "_redirects").write_bytes(old + b"/extra /target 301\n")
    (build / "_headers").unlink()

    code, out, err = _run(capsys, redirects, headers, build, "--json", "--locate-drift")
    assert code == 1 and err == ""
    redir, header = json.loads(out)["checks"]
    assert redir["drift_location"] == {
        "line": 2, "byte_column": 1, "byte_offset": len(old),
    }
    assert header["reason"] == "missing" and "drift_location" not in header


def test_drift_location_is_opt_in_and_reports_without_file_contents(tmp_path, capsys):
    redirects, headers, build = _fixture(tmp_path)
    (build / "_headers").write_bytes(b"/*\n  X-Robots-Tag: index\n")
    code, out, err = _run(capsys, redirects, headers, build, "--json")
    assert code == 1 and err == ""
    assert all("drift_location" not in item for item in json.loads(out)["checks"])

    reports = tmp_path / "reports"
    reports.mkdir()
    code, out, err = _run(
        capsys, redirects, headers, build, "--report-dir", str(reports),
        "--github-annotations", "--locate-drift",
    )
    assert code == 1 and err == ""
    assert "first differing byte at line 2" in out
    assert "noindex" not in out and "index\n" not in out
    data = json.loads((reports / "seo-pages.json").read_text(encoding="utf-8"))
    assert data["checks"][1]["drift_location"]["line"] == 2
    assert "first differing byte at line 2" in (reports / "seo-pages.md").read_text(encoding="utf-8")
    junit = ET.parse(reports / "seo-pages.xml").getroot()
    failure = junit.findall("testcase")[1].find("failure")
    assert "first differing byte at line 2" in (failure.text or "")


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


@pytest.mark.parametrize("invalid,drifted", [
    ("_redirects", "_headers"),
    ("_headers", "_redirects"),
])
def test_human_input_error_still_displays_other_artifact_drift(
    tmp_path, capsys, invalid, drifted,
):
    redirects, headers, build = _fixture(tmp_path)
    (redirects if invalid == "_redirects" else headers).unlink()
    (build / drifted).write_bytes(b"changed policy\n")

    code, out, err = _run(capsys, redirects, headers, build)

    assert code == 2 and out == ""
    assert f"Invalid input ({invalid}):" in err
    assert f"Partial result: FAIL {drifted} changed:" in err
    assert "expected SHA-256" in err and "observed SHA-256" in err


def test_human_input_error_labels_other_artifact_pass_as_partial(tmp_path, capsys):
    redirects, headers, build = _fixture(tmp_path)
    headers.unlink()

    code, out, err = _run(capsys, redirects, headers, build)

    assert code == 2 and out == ""
    assert "Partial result: PASS _redirects reviewed SHA-256" in err
    assert "Invalid input (_headers):" in err


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


@pytest.mark.parametrize(
    ("drift", "expected_failures"),
    [
        ("none", set()),
        ("_redirects", {"_redirects"}),
        ("_headers", {"_headers"}),
        ("both", {"_redirects", "_headers"}),
    ],
)
def test_junit_reports_one_case_per_pages_artifact(
    tmp_path, capsys, drift, expected_failures,
):
    redirects, headers, build = _fixture(tmp_path)
    for artifact in expected_failures:
        (build / artifact).unlink()
    target = tmp_path / "pages.xml"
    code, out, err = _run(capsys, redirects, headers, build, "--junit-file", str(target))
    assert code == (1 if expected_failures else 0) and out == err == ""
    content = target.read_bytes()
    assert content.startswith(b"<?xml") and not content.startswith(b"\xef\xbb\xbf")
    suite = ET.fromstring(content)
    assert suite.tag == "testsuite"
    assert suite.attrib["tests"] == "2"
    assert suite.attrib["failures"] == str(len(expected_failures))
    cases = suite.findall("testcase")
    assert [case.attrib["name"] for case in cases] == ["_redirects", "_headers"]
    assert {
        case.attrib["name"] for case in cases if case.find("failure") is not None
    } == expected_failures
    for case in cases:
        failure = case.find("failure")
        if failure is not None:
            assert "missing" in failure.attrib["message"]
            assert "SHA-256" in (failure.text or "")


def test_json_junit_and_annotations_share_one_offline_check(tmp_path, capsys, monkeypatch):
    redirects, headers, build = _fixture(tmp_path)
    (build / "_headers").write_bytes(b"/*\n X-Robots-Tag: index\n")
    json_target, junit_target = tmp_path / "pages.json", tmp_path / "pages.xml"
    monkeypatch.setattr(
        socket, "socket",
        lambda *a, **kw: pytest.fail("Pages CI artifacts must be local-only"),
    )
    code, out, err = _run(
        capsys, redirects, headers, build,
        "--json-file", str(json_target), "--junit-file", str(junit_target),
        "--github-annotations",
    )
    assert code == 1 and err == ""
    assert out.splitlines() == [
        "::error title=SEO Pages _headers::Compiled _headers changed from the reviewed reference"
    ]
    assert json.loads(json_target.read_text(encoding="utf-8"))["status"] == "fail"
    cases = ET.parse(junit_target).getroot().findall("testcase")
    assert len(cases) == 2 and cases[1].find("failure") is not None


@pytest.mark.parametrize("invalid", ["existing", "build", "missing_parent", "same_target"])
def test_paired_reports_preflight_all_destinations_before_writing(
    tmp_path, capsys, invalid,
):
    redirects, headers, build = _fixture(tmp_path)
    json_target, junit_target = tmp_path / "pages.json", tmp_path / "pages.xml"
    if invalid == "existing":
        junit_target.write_text("preserve existing report", encoding="utf-8")
    elif invalid == "build":
        junit_target = build / "pages.xml"
    elif invalid == "missing_parent":
        junit_target = tmp_path / "absent" / "pages.xml"
    else:
        junit_target = json_target
    code, out, err = _run(
        capsys, redirects, headers, build,
        "--json-file", str(json_target), "--junit-file", str(junit_target),
    )
    assert code == 2 and out == "" and "Invalid input" in err
    assert not json_target.exists()
    if invalid == "existing":
        assert junit_target.read_text(encoding="utf-8") == "preserve existing report"
    else:
        assert not junit_target.exists()


def test_second_report_io_failure_cleans_first_own_file(tmp_path, capsys, monkeypatch):
    redirects, headers, build = _fixture(tmp_path)
    json_target, junit_target = tmp_path / "pages.json", tmp_path / "pages.xml"
    original_open = Path.open

    def fail_second_open(self, mode="r", *args, **kwargs):
        if self == junit_target and mode == "x":
            raise OSError("simulated JUnit destination failure")
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_second_open)
    code, out, err = _run(
        capsys, redirects, headers, build,
        "--json-file", str(json_target), "--junit-file", str(junit_target),
        "--github-annotations",
    )
    assert code == 2 and out == "" and "simulated JUnit" in err
    assert not json_target.exists() and not junit_target.exists()


def test_invalid_input_does_not_create_junit_or_json(tmp_path, capsys):
    redirects, headers, build = _fixture(tmp_path)
    json_target, junit_target = tmp_path / "pages.json", tmp_path / "pages.xml"
    code, out, err = _run(
        capsys, tmp_path / "missing-reviewed", headers, build,
        "--json-file", str(json_target), "--junit-file", str(junit_target),
    )
    assert code == 2 and out == "" and "Invalid input" in err
    assert not json_target.exists() and not junit_target.exists()


@pytest.mark.parametrize("drift", [False, True])
def test_pages_markdown_is_actionable_and_create_only(tmp_path, capsys, drift):
    redirects, headers, build = _fixture(tmp_path)
    if drift:
        (build / "_redirects").unlink()
    target = tmp_path / "pages.md"
    code, out, err = _run(capsys, redirects, headers, build, "--markdown-file", str(target))
    assert code == (1 if drift else 0) and out == err == ""
    body = target.read_text(encoding="utf-8")
    assert "# Searcharis Pages static policy check" in body
    assert f"**Result: {'FAIL' if drift else 'PASS'}**" in body
    assert "_redirects" in body and "_headers" in body
    if drift:
        assert "Rebuild" in body and "missing" in body
    else:
        assert "No static policy drift" in body
    code, out, err = _run(capsys, redirects, headers, build, "--markdown-file", str(target))
    assert code == 2 and out == "" and "already exists" in err
    assert body == target.read_text(encoding="utf-8")


@pytest.mark.parametrize("drift", [False, True])
def test_pages_report_bundle_one_verification_and_annotations(
    tmp_path, capsys, monkeypatch, drift,
):
    redirects, headers, build = _fixture(tmp_path)
    if drift:
        (build / "_headers").write_bytes(b"/*\n  X-Robots-Tag: index\n")
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(
        socket, "socket",
        lambda *a, **kw: pytest.fail("Pages report bundle must never fetch URLs"),
    )
    code, out, err = _run(
        capsys, redirects, headers, build,
        "--report-dir", str(reports), "--github-annotations",
    )
    assert code == (1 if drift else 0) and err == ""
    assert sorted(p.name for p in reports.iterdir()) == [
        "seo-pages.json", "seo-pages.md", "seo-pages.xml",
    ]
    data = json.loads((reports / "seo-pages.json").read_text(encoding="utf-8"))
    assert data["status"] == ("fail" if drift else "pass")
    suite = ET.parse(reports / "seo-pages.xml").getroot()
    assert suite.attrib["tests"] == "2"
    assert suite.attrib["failures"] == str(int(drift))
    markdown = (reports / "seo-pages.md").read_text(encoding="utf-8")
    assert f"**Result: {'FAIL' if drift else 'PASS'}**" in markdown
    if drift:
        assert out.count("::error") == 1 and "_headers" in out
    else:
        assert out == ""


@pytest.mark.parametrize("bad", ["existing", "in_build", "missing_directory"])
def test_bundle_preflights_every_destination_without_partial_output(
    tmp_path, capsys, bad,
):
    redirects, headers, build = _fixture(tmp_path)
    directory = tmp_path / "reports"
    if bad == "in_build":
        directory = build / "reports"
        directory.mkdir()
    elif bad == "missing_directory":
        directory = tmp_path / "missing"
    else:
        directory.mkdir()
        (directory / "seo-pages.md").write_text("other job", encoding="utf-8")
    code, out, err = _run(
        capsys, redirects, headers, build,
        "--report-dir", str(directory), "--github-annotations",
    )
    assert code == 2 and out == "" and "Invalid input" in err
    assert not (directory / "seo-pages.json").exists()
    assert not (directory / "seo-pages.xml").exists()
    if bad == "existing":
        assert (directory / "seo-pages.md").read_text(encoding="utf-8") == "other job"
    else:
        assert not (directory / "seo-pages.md").exists()


def test_bundle_late_markdown_write_failure_cleans_owned_reports(
    tmp_path, capsys, monkeypatch,
):
    redirects, headers, build = _fixture(tmp_path)
    reports = tmp_path / "reports"
    reports.mkdir()
    original_open = Path.open

    def fail_markdown_open(self, mode="r", *args, **kwargs):
        if self == reports / "seo-pages.md" and mode == "x":
            raise OSError("simulated Markdown artifact failure")
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_markdown_open)
    (build / "_headers").unlink()
    code, out, err = _run(
        capsys, redirects, headers, build,
        "--report-dir", str(reports), "--github-annotations",
    )
    assert code == 2 and out == "" and "simulated Markdown" in err
    assert list(reports.iterdir()) == []


def test_invalid_pages_input_creates_no_bundle(tmp_path, capsys):
    redirects, headers, build = _fixture(tmp_path)
    reports = tmp_path / "reports"
    reports.mkdir()
    code, out, err = _run(
        capsys, tmp_path / "missing-policy", headers, build,
        "--report-dir", str(reports),
    )
    assert code == 2 and out == "" and "Invalid input" in err
    assert list(reports.iterdir()) == []


@pytest.mark.parametrize("extra", [
    ("--json",),
    ("--json-file", "other.json"),
    ("--markdown-file", "other.md"),
    ("--junit-file", "other.xml"),
])
def test_bundle_rejects_conflicting_explicit_report_modes(tmp_path, extra):
    redirects, headers, build = _fixture(tmp_path)
    with pytest.raises(SystemExit) as result:
        main([
            str(redirects), str(headers), str(build),
            "--report-dir", str(tmp_path), *extra,
        ])
    assert result.value.code == 2
