"""Validate the opt-in consumer workflow recipe without running GitHub Actions."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples/seo-contract/github-actions/seo-contract.example.yml"


def test_consumer_example_pins_code_and_remains_read_only() -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    action_refs = re.findall(r"^\s+uses: ([^\s#]+)", text, re.MULTILINE)
    assert len(action_refs) == 4
    assert all(re.fullmatch(r"actions/[\w-]+@[a-f0-9]{40}", ref) for ref in action_refs)
    assert "ref: 059c84016888a725223d1c961f9b77fd92b7059a" in text
    assert text.count("persist-credentials: false") == 2
    assert "permissions:\n  contents: read" in text
    assert "pull_request:\n" in text
    assert "pull_request_target:" not in text
    assert "secrets." not in text
    assert "upload-artifact@" not in text
    assert "deploy" not in "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


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
