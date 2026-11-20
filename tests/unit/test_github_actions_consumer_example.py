"""Validate the opt-in consumer workflow recipe without running GitHub Actions."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples/seo-contract/github-actions/seo-contract.example.yml"


def test_consumer_example_pins_code_and_remains_read_only() -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    action_refs = re.findall(r"^\s+uses: ([^\s#]+)", text, re.MULTILINE)
    assert len(action_refs) == 5
    assert all(re.fullmatch(r"actions/[\w-]+@[a-f0-9]{40}", ref) for ref in action_refs)
    # This merged source supports the entire manually reviewed queue workflow:
    # unreviewed export, explicit selected-path import, and prior contract retention.
    # Source must contain manifest_review_cli's opt-in annotation support.
    assert "ref: 47e92a7ce4fcba87d1fb523e066db7ad5351822d" in text
    assert text.count("persist-credentials: false") == 3
    assert "permissions:\n  contents: read" in text
    assert "pull_request:\n" in text
    assert "pull_request_target:" not in text
    assert "secrets." not in text
    assert "upload-artifact@" not in text
    assert "deploy" not in "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_consumer_guide_describes_manual_review_queue_without_ci_auto_approval() -> None:
    guide = EXAMPLE.with_name("README.md").read_text(encoding="utf-8")
    workflow = EXAMPLE.read_text(encoding="utf-8")
    assert "--unreviewed-file candidate-paths.txt" in guide
    assert "--only-file reviewed-next-paths.txt" in guide
    assert "--extend-from seo-manifest.json" in guide
    assert "manually" in guide.lower()
    # No generated manifests or candidate queues are approved automatically in CI.
    assert "-m searcharis.manifest_bootstrap" not in workflow
    assert "--unreviewed-file" not in workflow
    assert "--only-file" not in workflow


def _powershell_rehearsal() -> str:
    guide = EXAMPLE.with_name("README.md").read_text(encoding="utf-8")
    match = re.search(r"```powershell\n(.*?)\n```", guide, re.DOTALL)
    assert match is not None, "Windows consumers need a copyable PowerShell rehearsal"
    return match.group(1)


def test_consumer_guide_rehearses_exit_codes_in_powershell() -> None:
    script = _powershell_rehearsal()
    assert "python -S -m searcharis.site_cli" in script
    assert "--check-canonical-consistency" in script
    assert "--github-annotations" in script
    assert "$LASTEXITCODE -ne 0" in script
    assert "$LASTEXITCODE -ne 1" in script
    assert "$LASTEXITCODE -ne 2" in script
    assert "PYTHONPATH" in script
    assert "Remove-Item" not in script
    assert "Invoke-WebRequest" not in script


def test_consumer_guide_rehearses_manual_approval_integrity_in_powershell() -> None:
    script = _powershell_rehearsal()
    assert script.count("python -S -m searcharis.manifest_review_cli") == 3
    assert "approved.json" in script
    assert "weakened.json" in script
    assert "ConvertTo-Json -Depth" in script
    assert "UTF8Encoding]::new($false)" in script  # PowerShell 5.1 needs no-BOM JSON.
    assert "approved manifest review did not return exit 0" in script
    assert "weakened manifest review did not return exit 1" in script
    assert "invalid manifest review did not return exit 2" in script
    assert "Set-ExecutionPolicy" not in script


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell Windows rehearsal")
def test_consumer_guide_powershell_rehearsal_runs_locally() -> None:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", _powershell_rehearsal()],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=50,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "PowerShell offline rehearsal PASS" in result.stdout


def test_consumer_example_preserves_nonzero_gate_exit_and_review_scope() -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "PYTHONPATH: ${{ github.workspace }}/.sear-checker/src" in text
    assert "python -S -m searcharis.site_cli seo-manifest.json dist" in text
    assert "--check-canonical-consistency" in text
    commands = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "--require-full-coverage" not in commands  # First use may review critical pages.
    assert "--report-dir seo-ci-reports --github-annotations" in text
    assert "gate_status=$?" in text
    assert 'exit "$gate_status"' in text
    assert '"$gate_status" -le 1' in text  # Never append partial exit-2 output.


def test_consumer_example_compares_approved_manifest_from_immutable_pr_base() -> None:
    workflow = EXAMPLE.read_text(encoding="utf-8")
    assert workflow.count("uses: actions/checkout@") == 3
    assert "ref: ${{ github.event.pull_request.base.sha }}" in workflow
    assert "sparse-checkout: seo-manifest.json" in workflow
    assert "sparse-checkout-cone-mode: false" in workflow
    assert "path: .seo-approved-base" in workflow
    assert workflow.count("persist-credentials: false") == 3
    assert "if: github.event_name == 'pull_request'" in workflow
    assert "python -S -m searcharis.manifest_review_cli" in workflow
    assert ".seo-approved-base/seo-manifest.json seo-manifest.json dist" in workflow
    assert "python -S -m searcharis.site_cli" in workflow
    assert "pull_request_target:" not in workflow
    assert "fetch-depth: 0" not in workflow


def test_consumer_example_surfaces_approval_drift_in_github_annotations() -> None:
    workflow = EXAMPLE.read_text(encoding="utf-8")
    approval_step = workflow.split("- name: Reject changed previously approved assertions", 1)[1].split(
        "- name: Verify developer-reviewed SEO contracts", 1
    )[0]
    # The flag is evaluated against the immutable merged source, not an older
    # checker revision that would reject the CLI invocation as an input error.
    assert "ref: 47e92a7ce4fcba87d1fb523e066db7ad5351822d" in workflow
    assert re.search(
        r"\.seo-approved-base/seo-manifest\.json seo-manifest\.json dist\s+\\\s+--github-annotations",
        approval_step,
    )
    assert "else" in approval_step  # Unapproved first-use still requires human review.
    guide = EXAMPLE.with_name("README.md").read_text(encoding="utf-8")
    assert "approval drift" in guide.lower()
    assert "annotation" in guide.lower()


def test_consumer_example_surfaces_approval_markdown_without_masking_gate() -> None:
    workflow = EXAMPLE.read_text(encoding="utf-8")
    approval_step = workflow.split("- name: Reject changed previously approved assertions", 1)[1].split(
        "- name: Verify developer-reviewed SEO contracts", 1
    )[0]
    # The pinned revision must actually contain the Markdown CLI capability.
    assert "ref: 47e92a7ce4fcba87d1fb523e066db7ad5351822d" in workflow
    assert "mkdir -p seo-ci-reports" in approval_step
    assert "--github-annotations --markdown-file seo-ci-reports/approval-review.md" in approval_step
    assert "set +e" in approval_step
    assert "approval_status=$?" in approval_step
    assert "set -e" in approval_step
    assert 'if [ "$approval_status" -le 1 ] && [ -f seo-ci-reports/approval-review.md ]; then' in approval_step
    assert 'cat seo-ci-reports/approval-review.md >> "$GITHUB_STEP_SUMMARY"' in approval_step
    assert 'exit "$approval_status"' in approval_step
    assert approval_step.index("approval_status=$?") < approval_step.index("cat seo-ci-reports/")
    assert "continue-on-error:" not in approval_step
    # First adoption has no trusted base, so never fabricate an approval report.
    assert "if [ -f .seo-approved-base/seo-manifest.json ]; then" in approval_step
    assert "Initial SEO manifest adoption" in approval_step


def test_consumer_example_marks_missing_approved_base_in_step_summary() -> None:
    workflow = EXAMPLE.read_text(encoding="utf-8")
    approval_step = workflow.split("- name: Reject changed previously approved assertions", 1)[1].split(
        "- name: Verify developer-reviewed SEO contracts", 1
    )[0]
    first_use = approval_step.split("\n          else\n", 1)[1]
    assert "printf '%s\\n'" in first_use
    assert '>> "$GITHUB_STEP_SUMMARY"' in first_use
    assert "manual review required" in first_use
    assert "Approval-integrity comparison was **not run**" in first_use
    assert "does **not** approve" in first_use
    assert "--markdown-file" not in first_use


def test_consumer_first_use_shell_writes_only_unapproved_notice(tmp_path: Path) -> None:
    if sys.platform == "win32":
        git_bash = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Git/bin/bash.exe"
        bash = str(git_bash) if git_bash.is_file() else None
    else:
        bash = shutil.which("bash")
    if not bash:
        pytest.skip("Git Bash or POSIX Bash is required to execute the consumer recipe")

    workflow = EXAMPLE.read_text(encoding="utf-8")
    approval_step = workflow.split("- name: Reject changed previously approved assertions", 1)[1].split(
        "- name: Verify developer-reviewed SEO contracts", 1
    )[0]
    script = textwrap.dedent(approval_step.split("        run: |\n", 1)[1])
    env = os.environ.copy()
    env["GITHUB_STEP_SUMMARY"] = "summary.md"
    result = subprocess.run(
        [bash, "-e", "-o", "pipefail", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "manual review required" in summary
    assert "Approval-integrity comparison was **not run**" in summary
    assert "does **not** approve" in summary
    assert "Initial SEO manifest adoption" in result.stdout
    assert not (tmp_path / "seo-ci-reports").exists()


@pytest.mark.parametrize(
    ("case", "expected_exit"),
    [("intact_with_new_page", 0), ("weakened_reviewed_page", 1), ("invalid_candidate", 2)],
)
def test_consumer_approved_base_shell_preserves_review_outcomes(
    tmp_path: Path, case: str, expected_exit: int
) -> None:
    """Run the copyable Actions step, including shell exit and summary handling."""
    if sys.platform == "win32":
        git_bash = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Git/bin/bash.exe"
        bash = str(git_bash) if git_bash.is_file() else None
    else:
        bash = shutil.which("bash")
    if not bash:
        pytest.skip("Git Bash or POSIX Bash is required to execute the consumer recipe")

    fixture = ROOT / "examples/seo-contract/generator-fixtures/vite"
    manifest = json.loads((fixture / "manifest.json").read_text(encoding="utf-8"))
    approved = json.loads(json.dumps(manifest))
    approved["pages"] = approved["pages"][:1]
    approved_dir = tmp_path / ".seo-approved-base"
    approved_dir.mkdir()
    (approved_dir / "seo-manifest.json").write_text(json.dumps(approved), encoding="utf-8")
    shutil.copytree(fixture / "artifacts", tmp_path / "dist")

    if case == "weakened_reviewed_page":
        manifest["pages"][0]["contract"]["title_present"] = False
    candidate = "{" if case == "invalid_candidate" else json.dumps(manifest)
    (tmp_path / "seo-manifest.json").write_text(candidate, encoding="utf-8")

    workflow = EXAMPLE.read_text(encoding="utf-8")
    approval_step = workflow.split("- name: Reject changed previously approved assertions", 1)[1].split(
        "- name: Verify developer-reviewed SEO contracts", 1
    )[0]
    script = textwrap.dedent(approval_step.split("        run: |\n", 1)[1])
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["GITHUB_STEP_SUMMARY"] = "summary.md"
    result = subprocess.run(
        [bash, "-e", "-o", "pipefail", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    assert result.returncode == expected_exit, result.stdout + "\n" + result.stderr
    summary = tmp_path / "summary.md"
    report = tmp_path / "seo-ci-reports/approval-review.md"
    if expected_exit == 2:
        assert not summary.exists()
        assert not report.exists()
        assert "Invalid input:" in result.stderr
    else:
        assert report.is_file()
        assert summary.read_bytes() == report.read_bytes()
        text = summary.read_text(encoding="utf-8")
        if expected_exit == 0:
            assert "UNREVIEWED" in text
            assert "pricing/index.html" in text
        else:
            # An altered approved prefix blocks comparison; do not imply that
            # downstream candidate pages were safely classified as append-only.
            assert "Blocking changes to approved assertions" in text
            assert "UNREVIEWED" not in text
        assert ("::error" in result.stdout) == (expected_exit == 1)


@pytest.mark.parametrize(
    ("case", "expected_exit"),
    [("matching_site", 0), ("regressed_canonical", 1), ("invalid_manifest", 2)],
)
def test_consumer_site_gate_shell_preserves_status_and_step_summary(
    tmp_path: Path, case: str, expected_exit: int
) -> None:
    """Exercise the actual independent site step under Actions Bash semantics."""
    if sys.platform == "win32":
        git_bash = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Git/bin/bash.exe"
        bash = str(git_bash) if git_bash.is_file() else None
    else:
        bash = shutil.which("bash")
    if not bash:
        pytest.skip("Git Bash or POSIX Bash is required to execute the consumer recipe")

    fixture = ROOT / "examples/seo-contract/generator-fixtures/vite"
    shutil.copytree(fixture / "artifacts", tmp_path / "dist")
    manifest = json.loads((fixture / "manifest.json").read_text(encoding="utf-8"))
    if case == "regressed_canonical":
        manifest["pages"][0]["contract"]["canonical"] = "https://fixtures.invalid/incorrect/"
    candidate = "{" if case == "invalid_manifest" else json.dumps(manifest)
    (tmp_path / "seo-manifest.json").write_text(candidate, encoding="utf-8")

    workflow = EXAMPLE.read_text(encoding="utf-8")
    site_step = workflow.split("- name: Verify developer-reviewed SEO contracts", 1)[1]
    script = textwrap.dedent(site_step.split("        run: |\n", 1)[1].split("\n# No artifact upload", 1)[0])
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["GITHUB_STEP_SUMMARY"] = "summary.md"
    result = subprocess.run(
        [bash, "-e", "-o", "pipefail", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    assert result.returncode == expected_exit, result.stdout + "\n" + result.stderr
    summary = tmp_path / "summary.md"
    report = tmp_path / "seo-ci-reports/seo-site.md"
    if expected_exit == 2:
        assert not report.exists()
        assert not summary.exists()
        assert "Invalid input:" in result.stderr
    else:
        assert report.is_file()
        assert summary.read_bytes() == report.read_bytes()
        assert f"**Result: {'PASS' if expected_exit == 0 else 'FAIL'}**" in summary.read_text(
            encoding="utf-8"
        )
        assert ("::error" in result.stdout) == (expected_exit == 1)


def test_consumer_guide_explains_approval_summary_and_fail_closed_inputs() -> None:
    guide = EXAMPLE.with_name("README.md").read_text(encoding="utf-8")
    assert "#302" in guide
    assert "approval-review.md" in guide
    assert "GITHUB_STEP_SUMMARY" in guide
    assert "exit 2" in guide
    assert "UNREVIEWED" in guide


def test_consumer_guide_explains_first_use_summary_without_claiming_approval() -> None:
    guide = EXAMPLE.with_name("README.md").read_text(encoding="utf-8")
    assert "first-use job summary" in guide.lower()
    assert "no approval-integrity comparison" in guide.lower()
    assert "manual review" in guide.lower()


def test_consumer_guide_requires_trusted_base_not_candidate_approval() -> None:
    guide = EXAMPLE.with_name("README.md").read_text(encoding="utf-8")
    assert "manifest_review_cli" in guide
    assert "base commit" in guide.lower()
    assert "initial adoption" in guide.lower()
    assert "same pull request" in guide.lower()


def test_consumer_example_runs_independent_site_gate_after_review_failure() -> None:
    workflow = EXAMPLE.read_text(encoding="utf-8")
    approval = workflow.split("- name: Reject changed previously approved assertions", 1)[1].split(
        "- name: Verify developer-reviewed SEO contracts", 1
    )[0]
    site_gate = workflow.split("- name: Verify developer-reviewed SEO contracts", 1)[1]
    assert re.search(r"^\s+id: review_approved_assertions$", approval, re.MULTILINE)
    assert (
        "if: ${{ success() || (failure() && steps.review_approved_assertions.outcome == 'failure') }}"
        in site_gate
    )
    assert "python -S -m searcharis.site_cli" in site_gate


def test_consumer_guide_explains_both_independent_failures() -> None:
    guide = EXAMPLE.with_name("README.md").read_text(encoding="utf-8")
    assert "approval review fails" in guide
    assert "SEO policy gate still runs" in guide
    assert "build or checkout fails" in guide


@pytest.mark.parametrize(
    ("manifest", "build", "expected_status", "expected_exit"),
    [
        (
            "examples/seo-contract/generator-fixtures/vite/manifest.json",
            "examples/seo-contract/generator-fixtures/vite/artifacts",
            "pass",
            0,
        ),
        (
            "examples/seo-contract/regression-demo/manifest.json",
            "examples/seo-contract/regression-demo/candidate",
            "fail",
            1,
        ),
        ("examples/seo-contract/missing-manifest.json", "dist", "error", 2),
    ],
)
def test_source_checkout_stdlib_runner_propagates_result(
    manifest: str, build: str, expected_status: str, expected_exit: int
) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-m",
            "searcharis.site_cli",
            manifest,
            build,
            "--require-full-coverage",
            "--json",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == expected_exit
    assert json.loads(result.stdout)["status"] == expected_status


@pytest.mark.parametrize(
    ("manifest", "build", "expected_status", "expected_exit"),
    [
        (
            "examples/seo-contract/generator-fixtures/vite/manifest.json",
            "examples/seo-contract/generator-fixtures/vite/artifacts",
            "pass",
            0,
        ),
        (
            "examples/seo-contract/regression-demo/manifest.json",
            "examples/seo-contract/regression-demo/candidate",
            "fail",
            1,
        ),
        ("examples/seo-contract/missing-manifest.json", "dist", "error", 2),
    ],
)
def test_consumer_workflow_gate_and_reports_locally(
    tmp_path: Path, manifest: str, build: str, expected_status: str, expected_exit: int
) -> None:
    """Exercise the example's full gate, not just a simpler --json invocation."""
    report_dir = tmp_path / "seo-ci-reports"
    report_dir.mkdir()  # The example's mkdir -p is required by --report-dir.
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-m",
            "searcharis.site_cli",
            manifest,
            build,
            "--check-canonical-consistency",
            "--report-dir",
            str(report_dir),
            "--github-annotations",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == expected_exit
    if expected_exit == 2:
        assert not list(report_dir.iterdir())
        assert "Invalid input:" in result.stderr
        return

    assert {item.name for item in report_dir.iterdir()} == {
        "seo-site.json",
        "seo-site.md",
        "seo-site.sarif",
        "seo-site.xml",
    }
    assert json.loads((report_dir / "seo-site.json").read_text(encoding="utf-8"))[
        "status"
    ] == expected_status
    summary = (report_dir / "seo-site.md").read_text(encoding="utf-8")
    assert f"**Result: {expected_status.upper()}**" in summary
    if expected_exit == 1:
        assert "::error title=SEO page issue::" in result.stdout
    else:
        assert "::error" not in result.stdout


def test_consumer_first_use_accepts_partial_manifest_and_rejects_reviewed_regression(
    tmp_path: Path,
) -> None:
    """Allow incremental adoption, but keep reviewed page failures blocking."""
    manifest = json.loads(
        (ROOT / "examples/seo-contract/generator-fixtures/vite/manifest.json").read_text(
            encoding="utf-8"
        )
    )
    manifest["pages"] = manifest["pages"][:1]
    build = ROOT / "examples/seo-contract/generator-fixtures/vite/artifacts"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")

    for broken in (False, True):
        reviewed = json.loads(json.dumps(manifest))
        if broken:
            reviewed["pages"][0]["contract"]["canonical"] = (
                "https://fixtures.invalid/incorrect/"
            )
        path = tmp_path / ("broken.json" if broken else "reviewed.json")
        path.write_text(json.dumps(reviewed), encoding="utf-8")
        reports = tmp_path / ("broken-reports" if broken else "passing-reports")
        reports.mkdir()
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                "-m",
                "searcharis.site_cli",
                str(path),
                str(build),
                "--check-canonical-consistency",
                "--report-dir",
                str(reports),
                "--github-annotations",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == (1 if broken else 0)
        report = json.loads((reports / "seo-site.json").read_text(encoding="utf-8"))
        assert report["coverage"]["unlisted"] == ["pricing/index.html"]
        assert report["coverage"]["require_full_coverage"] is False
        assert report["status"] == ("fail" if broken else "pass")
        summary = (reports / "seo-site.md").read_text(encoding="utf-8")
        assert "## Unreviewed built HTML (not gated)" in summary
        assert "pricing/index.html" in summary
        assert ("::error title=SEO page issue::" in result.stdout) is broken
