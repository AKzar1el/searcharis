"""Exercise offline SEO CI against representative Astro and Vite output layouts."""

import json
import re
import shutil
import socket
from pathlib import Path

import pytest

from searcharis.manifest_bootstrap import main as bootstrap_main
from searcharis.site_cli import main

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "examples" / "seo-contract" / "generator-fixtures"
SITE_FLAGS = (
    "--require-full-coverage",
    "--check-hreflang-reciprocity",
    "--check-hreflang-clusters",
    "--check-canonical-consistency",
)


def _run_json(project, build, capsys, *flags):
    exit_code = main([str(project / "manifest.json"), str(build), "--json", *flags])
    output = capsys.readouterr()
    assert output.err == ""
    return exit_code, json.loads(output.out)


def test_astro_multilingual_build_passes_all_site_checks_offline(capsys, monkeypatch):
    project = FIXTURES / "astro"

    def no_network(*_args, **_kwargs):
        raise AssertionError("A built artifact must never trigger an HTTP connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    exit_code, report = _run_json(project, project / "artifacts", capsys, *SITE_FLAGS)

    assert exit_code == 0
    assert report["status"] == "pass"
    assert report["summary"] == {"checked": 2, "passed": 2, "failed": 0}
    assert report["coverage"]["unlisted"] == []
    assert report["hreflang_reciprocity"]["checked_pairs"] == 2
    assert report["hreflang_clusters"]["checked_pairs"] == 2
    assert report["hreflang_clusters"]["unverified_targets"] == []
    assert report["canonical_consistency"]["unverified_targets"] == []


def test_astro_reviewed_multilingual_bootstrap_to_site_gate_offline(
    tmp_path, capsys, monkeypatch
):
    """A first-time user can generate, review and run a multilingual CI gate."""
    project = FIXTURES / "astro"

    def no_network(*_args, **_kwargs):
        raise AssertionError("SEO onboarding must not use the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    output = tmp_path / "reviewed-manifest.json"
    result = bootstrap_main([
        str(project / "artifacts"), "https://fixtures.invalid/",
        "--lang-map", str(project / "language-map.json"),
        "--hreflang-map", str(project / "hreflang-map.json"),
        "--expect-self-canonical", "--expect-indexable",
        "--check-built", "--output", str(output),
    ])
    captured = capsys.readouterr()
    assert result == 0
    assert captured.out == ""
    assert "PASS 2/2" in captured.err

    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert [page["path"] for page in manifest["pages"]] == [
        "en/index.html", "sl/index.html",
    ]
    assert [page["contract"]["html_lang"] for page in manifest["pages"]] == [
        "en", "sl",
    ]
    assert manifest["pages"][0]["contract"]["hreflang"] == {
        "en": "https://fixtures.invalid/en/",
        "sl": "https://fixtures.invalid/sl/",
        "x-default": "https://fixtures.invalid/en/",
    }
    exit_code = main([
        str(output), str(project / "artifacts"), "--json", *SITE_FLAGS,
    ])
    report = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert report["status"] == "pass"
    assert report["coverage"]["unlisted"] == []

    # Preserve the reviewed manifest; only mutate an independent build copy.
    candidate = tmp_path / "candidate"
    shutil.copytree(project / "artifacts", candidate)
    sl_page = candidate / "sl" / "index.html"
    original = sl_page.read_text(encoding="utf-8")
    assert '<html lang="sl">' in original
    sl_page.write_text(original.replace('<html lang="sl">', '<html lang="en">'), encoding="utf-8")
    exit_code = main([str(output), str(candidate), "--json", *SITE_FLAGS])
    report = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert report["status"] == "fail"
    failed = next(page for page in report["pages"] if page["path"] == "sl/index.html")
    assert "html_lang" in [issue["code"] for issue in failed["issues"]]

    # A wrong translation destination fails independently of language drift.
    wrong_link = 'hreflang="en" href="https://fixtures.invalid/en/"'
    assert wrong_link in original
    sl_page.write_text(
        original.replace(wrong_link, 'hreflang="en" href="https://fixtures.invalid/sl/"'),
        encoding="utf-8",
    )
    exit_code = main([str(output), str(candidate), "--json", *SITE_FLAGS])
    report = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    failed = next(page for page in report["pages"] if page["path"] == "sl/index.html")
    assert "hreflang:en" in [issue["code"] for issue in failed["issues"]]


def test_astro_regression_fails_with_explicit_sarif_source_location(
    tmp_path, capsys, monkeypatch
):
    project = FIXTURES / "astro"
    build = tmp_path / "artifacts"
    shutil.copytree(project / "artifacts", build)
    target = build / "sl" / "index.html"
    original = target.read_text(encoding="utf-8")
    assert '<link rel="canonical" href="https://fixtures.invalid/sl/">' in original
    target.write_text(
        original.replace(
            '<link rel="canonical" href="https://fixtures.invalid/sl/">',
            '<link rel="canonical" href="https://fixtures.invalid/en/">',
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(ROOT)
    exit_code = main([
        str(project / "manifest.json"), str(build), "--sarif", *SITE_FLAGS,
        "--sarif-source-map", str(project / "source-map.json"),
    ])
    result = json.loads(capsys.readouterr().out)["runs"][0]["results"]

    assert exit_code == 1
    canonical = next(issue for issue in result if issue["ruleId"] == "canonical")
    assert canonical["locations"][0]["physicalLocation"]["artifactLocation"] == {
        "uri": "examples/seo-contract/generator-fixtures/astro/src/pages/sl/index.astro",
    }
    assert canonical["relatedLocations"][0]["physicalLocation"]["artifactLocation"] == {
        "uri": "sl/index.html", "uriBaseId": "%BUILDROOT%",
    }


def test_vite_static_multipage_build_and_unrendered_regression(tmp_path, capsys, monkeypatch):
    project = FIXTURES / "vite"

    def no_network(*_args, **_kwargs):
        raise AssertionError("Checking a Vite bundle must not fetch remote resources")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    exit_code, report = _run_json(
        project, project / "artifacts", capsys, "--require-full-coverage",
        "--check-canonical-consistency",
    )
    assert exit_code == 0
    assert report["summary"]["checked"] == 2
    assert report["coverage"]["unlisted"] == []

    build = tmp_path / "artifacts"
    shutil.copytree(project / "artifacts", build)
    pricing = build / "pricing" / "index.html"
    html = pricing.read_text(encoding="utf-8")
    assert "<h1>Plans</h1>" in html
    pricing.write_text(html.replace("<h1>Plans</h1>", '<div id="root"></div>'), encoding="utf-8")
    exit_code, report = _run_json(
        project, build, capsys, "--require-full-coverage",
        "--check-canonical-consistency",
    )
    assert exit_code == 1
    failed = next(page for page in report["pages"] if page["path"] == "pricing/index.html")
    assert "h1_min" in [issue["code"] for issue in failed["issues"]]


@pytest.mark.parametrize("page", ["index.html", "pricing/index.html"])
def test_vite_compiled_html_references_its_checked_in_bundle(page):
    project = FIXTURES / "vite"
    # The tracked source imports main.js; Vite replaces it with a hashed bundle.
    source = (project / "site" / page).read_text(encoding="utf-8")
    html = (project / "artifacts" / page).read_text(encoding="utf-8")
    assert '<script type="module" src="/main.js"></script>' in source
    match = re.search(r'<script type="module" crossorigin src="/assets/(main-[\w-]+\.js)"></script>', html)
    assert match is not None
    asset = project / "artifacts" / "assets" / match.group(1)
    assert asset.is_file()
    assert 'document.documentElement.dataset.fixture="vite-static-mpa"' in asset.read_text(encoding="utf-8")
