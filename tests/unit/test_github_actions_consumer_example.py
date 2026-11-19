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
    assert "--require-full-coverage --check-canonical-consistency" in text
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
