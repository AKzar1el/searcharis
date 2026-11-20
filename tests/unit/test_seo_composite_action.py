"""Run the opt-in composite SEO action's actual shell against offline HTML."""

from __future__ import annotations

import json
import os
import re
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
    assert set(metadata["inputs"]) == {
        "manifest", "build-root", "require-full-coverage", "report-directory",
        "check-hreflang-reciprocity",
        "check-hreflang-clusters",
        "check-internal-nofollow",
        "check-unique-titles", "check-unique-descriptions",
        "check-reviewed-navigation", "check-reviewed-fragments",
        "require-inbound-paths",
    }
    assert metadata["inputs"]["manifest"]["required"] is True
    assert metadata["inputs"]["build-root"]["required"] is True
    assert metadata["inputs"]["require-full-coverage"]["default"] == "false"
    assert metadata["inputs"]["report-directory"]["default"] == ""
    assert metadata["inputs"]["check-hreflang-reciprocity"]["default"] == "false"
    assert metadata["inputs"]["check-hreflang-clusters"]["default"] == "false"
    assert metadata["inputs"]["check-internal-nofollow"]["default"] == "false"
    assert metadata["inputs"]["check-unique-titles"]["default"] == "false"
    assert metadata["inputs"]["check-unique-descriptions"]["default"] == "false"
    assert metadata["inputs"]["check-reviewed-navigation"]["default"] == "false"
    assert metadata["inputs"]["check-reviewed-fragments"]["default"] == "false"
    assert metadata["inputs"]["require-inbound-paths"]["default"] == ""
    assert step["env"]["SEO_MANIFEST"] == "${{ inputs.manifest }}"
    assert step["env"]["SEO_BUILD_ROOT"] == "${{ inputs.build-root }}"
    assert step["env"]["SEO_STRICT_COVERAGE"] == "${{ inputs.require-full-coverage }}"
    assert step["env"]["SEO_REPORT_DIRECTORY"] == "${{ inputs.report-directory }}"
    assert step["env"]["SEO_HREFLANG_RECIPROCITY"] == "${{ inputs.check-hreflang-reciprocity }}"
    assert step["env"]["SEO_HREFLANG_CLUSTERS"] == "${{ inputs.check-hreflang-clusters }}"
    assert step["env"]["SEO_INTERNAL_NOFOLLOW"] == "${{ inputs.check-internal-nofollow }}"
    assert step["env"]["SEO_UNIQUE_TITLES"] == "${{ inputs.check-unique-titles }}"
    assert step["env"]["SEO_UNIQUE_DESCRIPTIONS"] == "${{ inputs.check-unique-descriptions }}"
    assert step["env"]["SEO_REVIEWED_NAVIGATION"] == "${{ inputs.check-reviewed-navigation }}"
    assert step["env"]["SEO_REVIEWED_FRAGMENTS"] == "${{ inputs.check-reviewed-fragments }}"
    assert step["env"]["SEO_REQUIRED_INBOUND_PATHS"] == "${{ inputs.require-inbound-paths }}"
    assert step["env"]["PYTHONPATH"] == "${{ github.action_path }}/../../../../src"
    assert ACTION.parent.parents[3] == ROOT
    script = step["run"]
    assert "python3 -S -m searcharis.site_cli" in script
    assert "--check-canonical-consistency" in script
    assert "--check-hreflang-reciprocity" in script
    assert "--check-hreflang-clusters" in script
    assert "--check-internal-nofollow" in script
    assert "--check-unique-titles" in script
    assert "--check-unique-descriptions" in script
    assert "--check-reviewed-navigation" in script
    assert "--check-reviewed-fragments" in script
    assert "--require-inbound-link" in script
    assert "--require-full-coverage" in script
    assert "--github-annotations" in script
    assert "GITHUB_STEP_SUMMARY" in script
    assert "exit \"$status\"" in script
    assert "curl " not in script and "wget " not in script


@pytest.mark.parametrize(
    ("titles", "descriptions", "expected"),
    [
        ("false", "false", 0),
        ("true", "false", 1),
        ("false", "true", 1),
        ("true", "true", 1),
        ("TRUE", "false", 2),
        ("false", "enabled", 2),
    ],
)
def test_composite_action_opt_in_duplicate_metadata_gate(
    tmp_path: Path, titles: str, descriptions: str, expected: int,
) -> None:
    if sys.platform == "win32":
        candidate = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Git/bin/bash.exe"
        bash = str(candidate) if candidate.is_file() else None
    else:
        bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash required for runnable composite-action regression")

    build = tmp_path / "dist"
    build.mkdir()
    pages = []
    for name in ("a", "b"):
        (build / f"{name}.html").write_text(
            '<html><head><title>Shared product title</title>'
            '<meta name="description" content="Shared description">'
            f'<link rel="canonical" href="https://example.test/{name}">'
            '</head><body></body></html>',
            encoding="utf-8",
        )
        pages.append({
            "path": f"{name}.html",
            "contract": {"url": f"https://example.test/{name}", "title_present": True},
        })
    (tmp_path / "manifest.json").write_text(
        json.dumps({"version": 1, "pages": pages}), encoding="utf-8",
    )
    (tmp_path / "reports").mkdir()
    metadata = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT / "src"),
        "SEO_MANIFEST": "manifest.json",
        "SEO_BUILD_ROOT": "dist",
        "SEO_STRICT_COVERAGE": "false",
        "SEO_HREFLANG_RECIPROCITY": "false",
        "SEO_INTERNAL_NOFOLLOW": "false",
        "SEO_UNIQUE_TITLES": titles,
        "SEO_UNIQUE_DESCRIPTIONS": descriptions,
        "SEO_REVIEWED_NAVIGATION": "false",
        "SEO_REVIEWED_FRAGMENTS": "false",
        "SEO_REPORT_DIRECTORY": "reports",
        "GITHUB_STEP_SUMMARY": "summary.md",
    })
    result = subprocess.run(
        [bash, "-e", "-o", "pipefail", "-c", metadata["runs"]["steps"][0]["run"]],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == expected, result.stdout + "\n" + result.stderr
    if expected == 2:
        assert not (tmp_path / "summary.md").exists()
        assert list((tmp_path / "reports").iterdir()) == []
        return
    report = json.loads((tmp_path / "reports/seo-site.json").read_text(encoding="utf-8"))
    assert report["status"] == ("pass" if expected == 0 else "fail")
    issues = {issue["code"] for page in report["pages"] for issue in page["issues"]}
    assert ("title_duplicate" in issues) == (titles == "true")
    assert ("description_duplicate" in issues) == (descriptions == "true")
    assert (tmp_path / "summary.md").exists()


@pytest.mark.parametrize(
    ("nofollow_link", "check", "expected"),
    [
        (False, "true", 0),
        (True, "false", 0),
        (True, "true", 1),
        (False, "TRUE", 2),
        (False, "enabled", 2),
    ],
)
def test_composite_action_opt_in_reviewed_internal_nofollow(
    tmp_path: Path, nofollow_link: bool, check: str, expected: int,
) -> None:
    if sys.platform == "win32":
        candidate = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Git/bin/bash.exe"
        bash = str(candidate) if candidate.is_file() else None
    else:
        bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash required for runnable composite-action regression")

    build = tmp_path / "dist"
    build.mkdir()
    pages = []
    for page in ("home", "about"):
        target = f"https://example.test/{page}/"
        links = ('<a href="/about/" rel="NoFollow noopener">About</a>'
                 if page == "home" and nofollow_link else
                 '<a href="/about/">About</a>' if page == "home" else "")
        (build / f"{page}.html").write_text(
            f'<html><head><title>{page}</title>'
            f'<link rel="canonical" href="{target}"></head>'
            f'<body><h1>{page}</h1>{links}</body></html>',
            encoding="utf-8",
        )
        pages.append({
            "path": f"{page}.html",
            "contract": {"url": target, "title_present": True},
        })
    (tmp_path / "manifest.json").write_text(
        json.dumps({"version": 1, "pages": pages}), encoding="utf-8",
    )
    (tmp_path / "reports").mkdir()
    metadata = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT / "src"),
        "SEO_MANIFEST": "manifest.json",
        "SEO_BUILD_ROOT": "dist",
        "SEO_STRICT_COVERAGE": "false",
        "SEO_HREFLANG_RECIPROCITY": "false",
        "SEO_INTERNAL_NOFOLLOW": check,
        "SEO_UNIQUE_TITLES": "false",
        "SEO_UNIQUE_DESCRIPTIONS": "false",
        "SEO_REVIEWED_NAVIGATION": "false",
        "SEO_REVIEWED_FRAGMENTS": "false",
        "SEO_REPORT_DIRECTORY": "reports",
        "GITHUB_STEP_SUMMARY": "summary.md",
    })
    result = subprocess.run(
        [bash, "-e", "-o", "pipefail", "-c", metadata["runs"]["steps"][0]["run"]],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == expected, result.stdout + "\n" + result.stderr
    if expected == 2:
        assert not (tmp_path / "summary.md").exists()
        assert list((tmp_path / "reports").iterdir()) == []
        return
    report = json.loads((tmp_path / "reports/seo-site.json").read_text(encoding="utf-8"))
    assert report["status"] == ("pass" if expected == 0 else "fail")
    issues = {issue["code"] for page in report["pages"] for issue in page["issues"]}
    assert ("internal_link_nofollow" in issues) == (expected == 1)
    assert (tmp_path / "summary.md").exists()


@pytest.mark.parametrize(
    ("href", "navigation", "fragments", "expected", "issue_code"),
    [
        ("/about/#section", "true", "true", 0, None),
        ("/unreviewed/", "false", "false", 0, None),
        ("/unreviewed/", "true", "false", 1, "navigation_target_unreviewed"),
        ("/about/#missing", "false", "false", 0, None),
        ("/about/#missing", "false", "true", 1, "navigation_fragment_missing"),
        ("/about/#missing", "true", "true", 1, "navigation_fragment_missing"),
        ("/about/#section", "TRUE", "false", 2, None),
        ("/about/#section", "false", "enabled", 2, None),
    ],
)
def test_composite_action_optional_reviewed_navigation_and_fragments(
    tmp_path: Path, href: str, navigation: str, fragments: str,
    expected: int, issue_code: str | None,
) -> None:
    if sys.platform == "win32":
        candidate = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Git/bin/bash.exe"
        bash = str(candidate) if candidate.is_file() else None
    else:
        bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash required for runnable composite-action regression")

    pages = []
    for name in ("home", "about"):
        target = f"https://example.test/{name}/"
        body = f'<a href="{href}">Go</a>' if name == "home" else '<div id="section">Hi</div>'
        page = tmp_path / "dist" / name / "index.html"
        page.parent.mkdir(parents=True)
        page.write_text(
            '<html><head><title>Example</title>'
            f'<link rel="canonical" href="{target}"></head>'
            f"<body>{body}</body></html>",
            encoding="utf-8",
        )
        pages.append({
            "path": f"{name}/index.html",
            "contract": {"url": target, "title_present": True},
        })
    (tmp_path / "manifest.json").write_text(
        json.dumps({"version": 1, "pages": pages}), encoding="utf-8",
    )
    reports = tmp_path / "reports"
    reports.mkdir()
    metadata = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT / "src"),
        "SEO_MANIFEST": "manifest.json",
        "SEO_BUILD_ROOT": "dist",
        "SEO_STRICT_COVERAGE": "false",
        "SEO_HREFLANG_RECIPROCITY": "false",
        "SEO_INTERNAL_NOFOLLOW": "false",
        "SEO_UNIQUE_TITLES": "false",
        "SEO_UNIQUE_DESCRIPTIONS": "false",
        "SEO_REVIEWED_NAVIGATION": navigation,
        "SEO_REVIEWED_FRAGMENTS": fragments,
        "SEO_REPORT_DIRECTORY": "reports",
        "GITHUB_STEP_SUMMARY": "summary.md",
    })
    result = subprocess.run(
        [bash, "-e", "-o", "pipefail", "-c", metadata["runs"]["steps"][0]["run"]],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == expected, result.stdout + "\n" + result.stderr
    if expected == 2:
        assert not (tmp_path / "summary.md").exists()
        assert list(reports.iterdir()) == []
        return
    report = json.loads((reports / "seo-site.json").read_text(encoding="utf-8"))
    assert report["status"] == ("pass" if expected == 0 else "fail")
    codes = {issue["code"] for page in report["pages"] for issue in page["issues"]}
    if issue_code is not None:
        assert issue_code in codes
    assert (tmp_path / "summary.md").exists()


@pytest.mark.parametrize(
    ("broken_return", "check", "expected"),
    [
        (False, "true", 0),
        (True, "true", 1),
        (True, "false", 0),
        (False, "invalid", 2),
    ],
)
def test_composite_action_opt_in_local_hreflang_reciprocity(
    tmp_path: Path, broken_return: bool, check: str, expected: int
) -> None:
    if sys.platform == "win32":
        candidate = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Git/bin/bash.exe"
        bash = str(candidate) if candidate.is_file() else None
    else:
        bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash required for runnable composite-action regression")

    base_url = "https://example.test/"
    pages = []
    for language in ("en", "sl"):
        page = tmp_path / "dist" / language / "index.html"
        page.parent.mkdir(parents=True)
        alternates = '<link rel="alternate" hreflang="sl" href="/sl/">'
        if language != "sl" or not broken_return:
            alternates += '<link rel="alternate" hreflang="en" href="/en/">'
        page.write_text(
            '<html><head><title>Example</title>'
            f'<link rel="canonical" href="{base_url}{language}/">'
            f"{alternates}</head><body><h1>Example</h1></body></html>",
            encoding="utf-8",
        )
        pages.append({
            "path": f"{language}/index.html",
            "contract": {"url": f"{base_url}{language}/", "title_present": True},
        })
    (tmp_path / "manifest.json").write_text(
        json.dumps({"version": 1, "pages": pages}), encoding="utf-8"
    )
    reports = tmp_path / "reports"
    reports.mkdir()
    metadata = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT / "src"),
        "SEO_MANIFEST": "manifest.json",
        "SEO_BUILD_ROOT": "dist",
        "SEO_STRICT_COVERAGE": "false",
        "SEO_HREFLANG_RECIPROCITY": check,
        "SEO_INTERNAL_NOFOLLOW": "false",
        "SEO_UNIQUE_TITLES": "false",
        "SEO_UNIQUE_DESCRIPTIONS": "false",
        "SEO_REVIEWED_NAVIGATION": "false",
        "SEO_REVIEWED_FRAGMENTS": "false",
        "SEO_REPORT_DIRECTORY": "reports",
        "GITHUB_STEP_SUMMARY": "summary.md",
    })
    result = subprocess.run(
        [bash, "-e", "-o", "pipefail", "-c", metadata["runs"]["steps"][0]["run"]],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == expected, result.stdout + "\n" + result.stderr
    if expected == 2:
        assert not (tmp_path / "summary.md").exists()
        assert list(reports.iterdir()) == []
    else:
        report = json.loads((reports / "seo-site.json").read_text(encoding="utf-8"))
        assert report["status"] == ("pass" if expected == 0 else "fail")
        assert (tmp_path / "summary.md").exists()
        if check == "true":
            assert report["hreflang_reciprocity"]["checked_pairs"] == (
                1 if broken_return else 2
            )
        if expected == 1:
            assert any(
                issue["code"].startswith("hreflang_return:")
                for page in report["pages"] for issue in page["issues"]
            )


@pytest.mark.parametrize(
    ("different_default", "check", "expected"),
    [
        (False, "true", 0),
        (True, "true", 1),
        (True, "false", 0),
        (False, "TRUE", 2),
    ],
)
def test_composite_action_opt_in_hreflang_cluster_consistency(
    tmp_path: Path, different_default: bool, check: str, expected: int,
) -> None:
    if sys.platform == "win32":
        candidate = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Git/bin/bash.exe"
        bash = str(candidate) if candidate.is_file() else None
    else:
        bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash required for runnable composite-action regression")

    pages = []
    for language in ("en", "sl"):
        destination = "/sl/" if language == "sl" and different_default else "/en/"
        alternates = (
            '<link rel="alternate" hreflang="en" href="/en/">'
            '<link rel="alternate" hreflang="sl" href="/sl/">'
            f'<link rel="alternate" hreflang="x-default" href="{destination}">'
        )
        page = tmp_path / "dist" / language / "index.html"
        page.parent.mkdir(parents=True)
        page.write_text(
            f'<html><head><title>Example</title><link rel="canonical" '
            f'href="https://example.test/{language}/">{alternates}</head>'
            '<body><h1>Example</h1></body></html>',
            encoding="utf-8",
        )
        pages.append({
            "path": f"{language}/index.html",
            "contract": {"url": f"https://example.test/{language}/", "title_present": True},
        })
    (tmp_path / "manifest.json").write_text(
        json.dumps({"version": 1, "pages": pages}), encoding="utf-8",
    )
    reports = tmp_path / "reports"
    reports.mkdir()
    metadata = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT / "src"),
        "SEO_MANIFEST": "manifest.json",
        "SEO_BUILD_ROOT": "dist",
        "SEO_STRICT_COVERAGE": "false",
        "SEO_HREFLANG_RECIPROCITY": "false",
        "SEO_HREFLANG_CLUSTERS": check,
        "SEO_INTERNAL_NOFOLLOW": "false",
        "SEO_UNIQUE_TITLES": "false",
        "SEO_UNIQUE_DESCRIPTIONS": "false",
        "SEO_REVIEWED_NAVIGATION": "false",
        "SEO_REVIEWED_FRAGMENTS": "false",
        "SEO_REPORT_DIRECTORY": "reports",
        "GITHUB_STEP_SUMMARY": "summary.md",
    })
    result = subprocess.run(
        [bash, "-e", "-o", "pipefail", "-c", metadata["runs"]["steps"][0]["run"]],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == expected, result.stdout + "\n" + result.stderr
    if expected == 2:
        assert not (tmp_path / "summary.md").exists()
        assert list(reports.iterdir()) == []
        return
    report = json.loads((reports / "seo-site.json").read_text(encoding="utf-8"))
    assert report["status"] == ("pass" if expected == 0 else "fail")
    assert (tmp_path / "summary.md").exists()
    if check == "true":
        assert report["hreflang_clusters"]["checked_pairs"] == 2
        codes = {issue["code"] for page in report["pages"] for issue in page["issues"]}
        assert ("hreflang_cluster_mismatch:x-default" in codes) == different_default


@pytest.mark.parametrize(
    ("case", "strict", "expected", "retain_reports"),
    [
        ("passing", "false", 0, False),
        ("unreviewed", "false", 0, False),
        ("unreviewed", "true", 1, False),
        ("canonical_fail", "false", 1, False),
        ("invalid", "false", 2, False),
        ("passing", "unknown", 2, False),
        ("missing_manifest", "false", 2, False),
        ("missing_build_root", "false", 2, False),
        ("quoted_path", "false", 0, False),
        ("passing", "false", 0, True),
        ("canonical_fail", "false", 1, True),
        ("invalid", "false", 2, True),
        ("missing_report_directory", "false", 2, True),
        ("quoted_report_directory", "false", 0, True),
        ("report_collision", "false", 2, True),
    ],
)
def test_composite_action_shell_preserves_gates_and_summary(
    tmp_path: Path, case: str, strict: str, expected: int, retain_reports: bool
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
    report_name = (
        "reports dir;touch hacked2.json" if case == "quoted_report_directory" else "reports"
    )
    if retain_reports and case != "missing_report_directory":
        report_dir = tmp_path / report_name
        report_dir.mkdir()
        if case == "report_collision":
            (report_dir / "seo-site.json").write_text("existing", encoding="utf-8")
    metadata = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    script = metadata["runs"]["steps"][0]["run"]
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(ROOT / "src"),
            "SEO_MANIFEST": "" if case == "missing_manifest" else name,
            "SEO_BUILD_ROOT": "" if case == "missing_build_root" else "dist",
            "SEO_STRICT_COVERAGE": strict,
            "SEO_HREFLANG_RECIPROCITY": "false",
            "SEO_INTERNAL_NOFOLLOW": "false",
            "SEO_UNIQUE_TITLES": "false",
            "SEO_UNIQUE_DESCRIPTIONS": "false",
            "SEO_REVIEWED_NAVIGATION": "false",
            "SEO_REVIEWED_FRAGMENTS": "false",
            "SEO_REPORT_DIRECTORY": report_name if retain_reports else "",
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
    assert not (tmp_path / "hacked2.json").exists()
    summary = tmp_path / "summary.md"
    if expected == 2:
        assert not summary.exists()
    else:
        text = summary.read_text(encoding="utf-8")
        assert f"**Result: {'PASS' if expected == 0 else 'FAIL'}**" in text
        if case == "unreviewed":
            assert "pricing/index.html" in text
    assert ("::error" in result.stdout) == (expected == 1)
    if retain_reports:
        report_dir = tmp_path / report_name
        files = sorted(p.name for p in report_dir.iterdir()) if report_dir.exists() else []
        assert files == (
            ["seo-site.json", "seo-site.md", "seo-site.sarif", "seo-site.xml"]
            if expected in {0, 1} else ["seo-site.json"] if case == "report_collision" else []
        )
        if case == "report_collision":
            assert (report_dir / "seo-site.json").read_text(encoding="utf-8") == "existing"
        if expected in {0, 1}:
            data = json.loads((report_dir / "seo-site.json").read_text(encoding="utf-8"))
            assert data["status"] == ("pass" if expected == 0 else "fail")
            assert (report_dir / "seo-site.md").read_text(encoding="utf-8") == summary.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("paths", "service_link", "pricing_link", "expected", "missing"),
    [
        ("", False, False, 0, []),
        ("services/index.html", False, False, 1, ["services/index.html"]),
        ("services/index.html", True, False, 0, []),
        ("services/index.html\npricing/index.html", True, False, 1, ["pricing/index.html"]),
        ("services/index.html\npricing/index.html", True, True, 0, []),
        ("services/index.html\npricing/index.html\n", True, True, 0, []),
        ("services/index.html\r\npricing/index.html\r\n", True, True, 0, []),
        ("launch page/index.html", True, True, 0, []),
        ("missing/index.html", True, True, 2, []),
        ("services/index.html;touch injected.txt", True, True, 2, []),
        ("services/index.html\nservices/index.html", True, True, 2, []),
        ("services/index.html\n\npricing/index.html", True, True, 2, []),
        ("\n".join(["services/index.html"] * 129), True, True, 2, []),
    ],
)
def test_composite_action_opt_in_critical_inbound_paths(
    tmp_path: Path, paths: str, service_link: bool, pricing_link: bool,
    expected: int, missing: list[str],
) -> None:
    if sys.platform == "win32":
        candidate = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Git/bin/bash.exe"
        bash = str(candidate) if candidate.is_file() else None
    else:
        bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash required for runnable composite-action regression")

    build = tmp_path / "dist"
    pages = []
    for name in ("", "services/", "pricing/", "launch page/"):
        artifact = build / name / "index.html"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        links = ""
        if not name:
            links += '<a href="/services/">Services</a>' if service_link else ""
            links += '<a href="/pricing/">Pricing</a>' if pricing_link else ""
            links += '<a href="/launch%20page/">Launch page</a>'
        url = f"https://example.test/{name.replace(' ', '%20')}"
        artifact.write_text(
            f'<html><head><title>{name or "Home"}</title>'
            f'<link rel="canonical" href="{url}">'
            f'</head><body>{links}</body></html>', encoding="utf-8",
        )
        pages.append({
            "path": f"{name}index.html",
            "contract": {"url": url, "title_present": True},
        })
    (tmp_path / "manifest.json").write_text(
        json.dumps({"version": 1, "pages": pages}), encoding="utf-8",
    )
    (tmp_path / "reports").mkdir()
    script = yaml.safe_load(ACTION.read_text(encoding="utf-8"))["runs"]["steps"][0]["run"]
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT / "src"),
        "SEO_MANIFEST": "manifest.json", "SEO_BUILD_ROOT": "dist",
        "SEO_STRICT_COVERAGE": "false", "SEO_HREFLANG_RECIPROCITY": "false",
        "SEO_REQUIRED_INBOUND_PATHS": paths,
        "SEO_INTERNAL_NOFOLLOW": "false", "SEO_REPORT_DIRECTORY": "reports",
        "SEO_REVIEWED_NAVIGATION": "false", "SEO_REVIEWED_FRAGMENTS": "false",
        "SEO_UNIQUE_TITLES": "false", "SEO_UNIQUE_DESCRIPTIONS": "false",
        "GITHUB_STEP_SUMMARY": "summary.md",
    })
    result = subprocess.run(
        [bash, "-e", "-o", "pipefail", "-c", script],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == expected, result.stdout + "\n" + result.stderr
    assert not (tmp_path / "injected.txt").exists()
    if expected == 2:
        assert list((tmp_path / "reports").iterdir()) == []
        assert not (tmp_path / "summary.md").exists()
    else:
        report = json.loads((tmp_path / "reports/seo-site.json").read_text(encoding="utf-8"))
        assert report["status"] == ("pass" if expected == 0 else "fail")
        if paths:
            assert report["reviewed_inbound"]["missing_paths"] == missing
        assert (tmp_path / "summary.md").exists()


def test_composite_action_adoption_guide_preserves_manual_approval_boundary() -> None:
    guide = (ACTION.parent.parent / "README.md").read_text(encoding="utf-8")
    # A copyable example must resolve to an independently merged action, not
    # an unresolvable placeholder or mutable ref.
    pin = re.search(
        r"uses: AKzar1el/searcharis/examples/seo-contract/github-actions/"
        r"seo-site-action@([0-9a-f]{40})",
        guide,
    )
    assert pin is not None
    # Pin the independently merged Action revision implementing critical inbound
    # reviewed paths, not the older revision that silently ignores this input.
    assert pin.group(1) == "c81e291c2b9bbaa8b19d417090b5efe2f2c982c3"
    assert "<reviewed-40-character-commit>" not in guide
    assert "independently merged" in guide
    assert "require-full-coverage: 'false'" in guide
    assert "manual approval" in guide.lower()
    assert "manifest_review_cli" in guide
    assert "python-version: '3.13'" in guide
    assert "ubuntu-24.04" in guide
    assert "neither downloads" in guide.lower()
    assert "does not replace" in guide.lower()
    assert "report-directory: seo-ci-reports" in guide
    assert "check-hreflang-reciprocity: 'true'" in guide
    assert "# check-hreflang-clusters: 'true'" in guide
    assert "already supports the strict locale-cluster input" in guide
    assert "does not yet support this new" not in guide
    assert "check-unique-titles: 'true'" in guide
    assert "check-unique-descriptions: 'true'" in guide
    assert "# check-internal-nofollow: 'true'" in guide
    assert "already supports the internal nofollow input" in guide
    assert "check-reviewed-navigation: 'true'" in guide
    assert "check-reviewed-fragments: 'true'" in guide
    assert "# check-reviewed-navigation: 'true'" in guide
    assert "# check-reviewed-fragments: 'true'" in guide
    assert "# require-inbound-paths: |" in guide
    assert "#   pricing/index.html" in guide
    assert "already supports the critical inbound" in guide
    assert "predates this new" not in guide
    assert "already supports both reviewed navigation and fragment inputs" in guide
    assert "**does not** contain these two new Action inputs" not in guide
    assert "public route list is complete" in guide
    assert "predates this new input" not in guide
    assert "already supports these two inputs" in guide
    assert "# check-unique-titles: 'true'" in guide
    assert "# check-unique-descriptions: 'true'" in guide
    assert "seo-site.json" in guide
    assert "predates the option" not in guide
