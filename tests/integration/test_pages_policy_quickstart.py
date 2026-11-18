"""Checked-in first-run Cloudflare Pages artifacts exercise the actual offline CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "pages-policy"
REVIEWED = EXAMPLE / "reviewed"
BUILD = EXAMPLE / "site-root"


def _run(build: Path, *options: str, reviewed: Path = REVIEWED) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, "-m", "searcharis.pages_policy_cli",
            str(reviewed / "_redirects"), str(reviewed / "_headers"),
            str(build), *options,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        check=False,
        cwd=ROOT,
    )


def test_quickstart_fixtures_pass_real_module_entrypoint():
    for name in ("_redirects", "_headers"):
        assert (REVIEWED / name).read_bytes() == (BUILD / name).read_bytes()
    assert (BUILD / "index.html").is_file()

    result = _run(BUILD, "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "pass"
    assert [part["artifact"] for part in report["checks"]] == ["_redirects", "_headers"]
    assert all(part["status"] == "pass" for part in report["checks"])


def test_quickstart_report_bundle_is_parseable_and_create_only(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()

    result = _run(BUILD, "--report-dir", str(reports))
    assert result.returncode == 0, result.stderr
    assert {item.name for item in reports.iterdir()} == {
        "seo-pages.json", "seo-pages.xml", "seo-pages.md",
    }
    assert json.loads((reports / "seo-pages.json").read_text(encoding="utf-8"))["status"] == "pass"
    assert len(ET.parse(reports / "seo-pages.xml").findall(".//testcase")) == 2
    assert "**Result: PASS**" in (reports / "seo-pages.md").read_text(encoding="utf-8")

    # CI reruns cannot silently overwrite artifacts from another job.
    second = _run(BUILD, "--report-dir", str(reports))
    assert second.returncode == 2 and "already exists" in second.stderr


@pytest.mark.parametrize("changed", ["_redirects", "_headers"])
def test_quickstart_mutated_build_blocks_without_touching_reviewed(tmp_path, changed):
    candidate = tmp_path / "candidate"
    shutil.copytree(BUILD, candidate)
    (candidate / changed).write_bytes(b"changed in candidate only\n")

    result = _run(candidate, "--json")
    assert result.returncode == 1, result.stderr
    by_artifact = {
        part["artifact"]: part for part in json.loads(result.stdout)["checks"]
    }
    assert by_artifact[changed]["status"] == "fail"
    assert by_artifact[changed]["reason"] == "changed"
    assert by_artifact[("_headers" if changed == "_redirects" else "_redirects")]["status"] == "pass"


def test_quickstart_missing_artifact_and_invalid_reference_are_distinct(tmp_path):
    candidate = tmp_path / "candidate"
    shutil.copytree(BUILD, candidate)
    (candidate / "_headers").unlink()
    drift = _run(candidate, "--json")
    assert drift.returncode == 1
    headers = next(
        item for item in json.loads(drift.stdout)["checks"]
        if item["artifact"] == "_headers"
    )
    assert headers["reason"] == "missing"

    error = _run(BUILD, "--json", reviewed=tmp_path / "nonexistent")
    assert error.returncode == 2
    assert json.loads(error.stdout)["status"] == "error"
