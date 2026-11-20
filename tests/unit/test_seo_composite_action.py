"""Run the opt-in composite SEO action's actual shell against offline HTML."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / "examples/seo-contract/github-actions/seo-site-action/action.yml"
FIXTURE = ROOT / "examples/seo-contract/generator-fixtures/vite"


def test_composite_action_is_inert_read_only_and_uses_reviewed_inputs() -> None:
    metadata = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    assert metadata["runs"]["using"] == "composite"
    assert len(metadata["runs"]["steps"]) == 1
    step = metadata["runs"]["steps"][0]
    assert step["shell"] == "bash"
    assert "uses" not in step
    assert set(metadata["inputs"]) == {"manifest", "build-root", "require-full-coverage"}
    assert metadata["inputs"]["manifest"]["required"] is True
    assert metadata["inputs"]["build-root"]["required"] is True
    assert metadata["inputs"]["require-full-coverage"]["default"] == "false"
    assert step["env"]["SEO_MANIFEST"] == "${{ inputs.manifest }}"
    assert step["env"]["SEO_BUILD_ROOT"] == "${{ inputs.build-root }}"
    assert step["env"]["SEO_STRICT_COVERAGE"] == "${{ inputs.require-full-coverage }}"
    assert step["env"]["PYTHONPATH"] == "${{ github.action_path }}/../../../../src"
    assert ACTION.parent.parents[3] == ROOT
    script = step["run"]
    assert "python3 -S -m searcharis.site_cli" in script
    assert "--check-canonical-consistency" in script
    assert "--require-full-coverage" in script
    assert "--github-annotations" in script
    assert "GITHUB_STEP_SUMMARY" in script
    assert "exit \"$status\"" in script
    assert "curl " not in script and "wget " not in script


@pytest.mark.parametrize(
    ("case", "strict", "expected"),
    [
        ("passing", "false", 0),
        ("unreviewed", "false", 0),
        ("unreviewed", "true", 1),
        ("canonical_fail", "false", 1),
        ("invalid", "false", 2),
        ("passing", "unknown", 2),
        ("missing_manifest", "false", 2),
        ("missing_build_root", "false", 2),
        ("quoted_path", "false", 0),
    ],
)
def test_composite_action_shell_preserves_gates_and_summary(
    tmp_path: Path, case: str, strict: str, expected: int
) -> None:
    if sys.platform == "win32":
        bash_path = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Git/bin/bash.exe"
        bash = str(bash_path) if bash_path.is_file() else None
    else:
        bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash required for runnable composite-action regression")

    shutil.copytree(FIXTURE / "artifacts", tmp_path / "dist")
    manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    if case in {"unreviewed", "canonical_fail"}:
        if case == "unreviewed":
            manifest["pages"] = manifest["pages"][:1]
        else:
            manifest["pages"][0]["contract"]["canonical"] = (
                "https://fixtures.invalid/incorrect/"
            )

    name = "site manifest;touch hacked.json" if case == "quoted_path" else "seo-manifest.json"
    (tmp_path / name).write_text("{" if case == "invalid" else json.dumps(manifest), encoding="utf-8")
    metadata = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    script = metadata["runs"]["steps"][0]["run"]
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(ROOT / "src"),
            "SEO_MANIFEST": "" if case == "missing_manifest" else name,
            "SEO_BUILD_ROOT": "" if case == "missing_build_root" else "dist",
            "SEO_STRICT_COVERAGE": strict,
            "GITHUB_STEP_SUMMARY": "summary.md",
        }
    )
    result = subprocess.run(
        [bash, "-e", "-o", "pipefail", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == expected, result.stdout + "\n" + result.stderr
    assert not (tmp_path / "hacked.json").exists()
    summary = tmp_path / "summary.md"
    if expected == 2:
        assert not summary.exists()
    else:
        text = summary.read_text(encoding="utf-8")
        assert f"**Result: {'PASS' if expected == 0 else 'FAIL'}**" in text
        if case == "unreviewed":
            assert "pricing/index.html" in text
    assert ("::error" in result.stdout) == (expected == 1)


def test_composite_action_adoption_guide_preserves_manual_approval_boundary() -> None:
    guide = (ACTION.parent.parent / "README.md").read_text(encoding="utf-8")
    assert "seo-site-action@<reviewed-40-character-commit>" in guide
    assert "require-full-coverage: 'false'" in guide
    assert "manual approval" in guide.lower()
    assert "manifest_review_cli" in guide
    assert "python-version: '3.13'" in guide
    assert "ubuntu-24.04" in guide
    assert "neither downloads" in guide.lower()
    assert "does not replace" in guide.lower()
