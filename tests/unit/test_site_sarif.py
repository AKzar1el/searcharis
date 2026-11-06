"""SARIF export for local build artifacts; no cloud or source-code claims."""

import json
import socket

import pytest

from searcharis.site_cli import main
from searcharis.site_sarif import build_sarif_report

BASE = "https://example.test/"


def _fixture(tmp_path):
    for lang in ("en", "sl"):
        page = tmp_path / lang / "index.html"
        page.parent.mkdir()
        page.write_text(
            "<!doctype html><html><head><title>Test</title>"
            f'<link rel="canonical" href="/{lang}/">'
            "</head><body><h1>Heading</h1></body></html>",
            encoding="utf-8",
        )
    manifest = {
        "version": 1,
        "pages": [
            {
                "path": f"{lang}/index.html",
                "contract": {
                    "url": f"{BASE}{lang}/",
                    "canonical": f"{BASE}{lang}/",
                    "title_present": True,
                },
            }
            for lang in ("sl", "en")
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _run(path, root, capsys, *flags):
    code = main([str(path), str(root), "--sarif", *flags])
    output = capsys.readouterr()
    return code, json.loads(output.out)


def test_sarif_pass_is_valid_empty_run(tmp_path, capsys):
    manifest = _fixture(tmp_path)
    code, report = _run(manifest, tmp_path, capsys)
    assert code == 0
    assert report["version"] == "2.1.0"
    assert report["$schema"] == "https://json.schemastore.org/sarif-2.1.0.json"
    assert len(report["runs"]) == 1
    run = report["runs"][0]
    assert run["tool"]["driver"]["name"] == "Searcharis SEO Contract CI"
    assert run["results"] == []
    assert run["tool"]["driver"]["rules"] == []


def test_sarif_contract_failure_links_to_built_html(tmp_path, capsys):
    manifest = _fixture(tmp_path)
    path = tmp_path / "sl" / "index.html"
    path.write_text('<html><head><title>Ok</title><link rel="canonical" href="/bad/"></head></html>')
    code, report = _run(manifest, tmp_path, capsys)
    assert code == 1
    run = report["runs"][0]
    assert len(run["results"]) == 1
    issue = run["results"][0]
    assert issue["ruleId"] == "canonical"
    assert issue["level"] == "error"
    assert "https://example.test/sl/" in issue["message"]["text"]
    location = issue["locations"][0]["physicalLocation"]
    assert location["artifactLocation"]["uri"] == "sl/index.html"
    assert location["artifactLocation"]["uriBaseId"] == "%BUILDROOT%"
    assert location["region"]["startLine"] == 1
    assert run["tool"]["driver"]["rules"][0]["id"] == "canonical"
    assert issue["partialFingerprints"]["searcharis/issue/v1"]


def test_strict_coverage_creates_finding_with_uri_escaping(tmp_path, capsys):
    manifest = _fixture(tmp_path)
    (tmp_path / "odd #page.html").write_text("<html><head></head></html>")
    assert _run(manifest, tmp_path, capsys)[0] == 0
    code, report = _run(manifest, tmp_path, capsys, "--require-full-coverage")
    assert code == 1
    assert report["runs"][0]["results"][0]["ruleId"] == "unlisted_html"
    assert report["runs"][0]["results"][0]["locations"][0]["physicalLocation"][
        "artifactLocation"
    ]["uri"] == "odd%20%23page.html"


def test_sarif_normalizes_language_codes_and_is_deterministic():
    page = {
        "path": "en/index.html",
        "checked_url": "https://example.test/en/",
        "status": "fail",
        "issues": [
            {"code": "hreflang:sl", "expected": ["a"], "actual": ["b"]},
            {"code": "hreflang:en", "expected": ["c"], "actual": []},
        ],
    }
    report = {
        "status": "fail",
        "pages": [page],
        "coverage": {"unlisted": [], "require_full_coverage": False},
    }
    first = build_sarif_report(report)
    page["issues"].reverse()
    second = build_sarif_report(report)
    assert first == second
    assert [rule["id"] for rule in first["runs"][0]["tool"]["driver"]["rules"]] == [
        "hreflang"
    ]
    assert len(first["runs"][0]["results"]) == 2
    assert "hreflang:en" in first["runs"][0]["results"][0]["message"]["text"]


def test_sarif_message_is_bounded_for_large_observed_values():
    report = {
        "status": "fail",
        "pages": [{
            "path": "index.html",
            "checked_url": "https://example.test/",
            "status": "fail",
            "issues": [{"code": "canonical", "expected": "a", "actual": "b" * 30_000}],
        }],
        "coverage": {"unlisted": [], "require_full_coverage": False},
    }
    message = build_sarif_report(report)["runs"][0]["results"][0]["message"]["text"]
    assert len(message) < 2000
    assert "truncated" in message


def test_unverified_remote_urls_not_marked_as_failures_or_fetched(tmp_path, capsys, monkeypatch):
    manifest = _fixture(tmp_path)
    (tmp_path / "sl" / "index.html").write_text(
        '<html><head><title>Ok</title>'
        '<link rel="canonical" href="https://offsite.invalid/sl/"></head></html>'
    )
    specs = json.loads(manifest.read_text(encoding="utf-8"))
    specs["pages"][0]["contract"].pop("canonical")
    manifest.write_text(json.dumps(specs), encoding="utf-8")

    def deny(*_args, **_kwargs):
        raise AssertionError("No network access permitted")

    monkeypatch.setattr(socket, "create_connection", deny)
    code, report = _run(manifest, tmp_path, capsys, "--check-canonical-consistency")
    assert code == 0
    assert report["runs"][0]["results"] == []


def test_sarif_invalid_manifest_errors_to_stderr_without_partial_sarif(tmp_path, capsys):
    path = tmp_path / "manifest.json"
    path.write_text('{"version":2,"pages":[]}', encoding="utf-8")
    assert main([str(path), str(tmp_path), "--sarif"]) == 2
    output = capsys.readouterr()
    assert not output.out
    assert "Invalid input:" in output.err


def test_json_and_sarif_are_mutually_exclusive(tmp_path):
    manifest = _fixture(tmp_path)
    with pytest.raises(SystemExit) as failure:
        main([str(manifest), str(tmp_path), "--json", "--sarif"])
    assert failure.value.code == 2


def _source_map(tmp_path, mapping):
    path = tmp_path / "source-map.json"
    path.write_text(json.dumps({"version": 1, "sources": mapping}), encoding="utf-8")
    return path


def test_sarif_explicit_source_mapping_points_to_checked_in_template(tmp_path, capsys, monkeypatch):
    manifest = _fixture(tmp_path)
    (tmp_path / "sl" / "index.html").write_text("<html><title>Wrong</title></html>")
    source = tmp_path / "src" / "pages" / "sl" / "index.astro"
    source.parent.mkdir(parents=True)
    source.write_text("<h1>Source</h1>")
    mapping = _source_map(tmp_path, {"sl/index.html": "src/pages/sl/index.astro"})
    monkeypatch.chdir(tmp_path)

    code, report = _run(manifest, tmp_path, capsys, "--sarif-source-map", str(mapping))
    assert code == 1
    result = report["runs"][0]["results"][0]
    location = result["locations"][0]["physicalLocation"]
    assert location["artifactLocation"] == {"uri": "src/pages/sl/index.astro"}
    assert location["region"]["startLine"] == 1
    assert result["relatedLocations"][0]["physicalLocation"]["artifactLocation"] == {
        "uri": "sl/index.html", "uriBaseId": "%BUILDROOT%",
    }
    _, unmapped = _run(manifest, tmp_path, capsys)
    assert (
        result["partialFingerprints"]
        == unmapped["runs"][0]["results"][0]["partialFingerprints"]
    )


def test_sarif_unmapped_artifacts_fall_back_to_build_paths(tmp_path, capsys, monkeypatch):
    manifest = _fixture(tmp_path)
    (tmp_path / "sl" / "index.html").write_text("<html></html>")
    mapping = _source_map(tmp_path, {})
    monkeypatch.chdir(tmp_path)
    code, report = _run(manifest, tmp_path, capsys, "--sarif-source-map", str(mapping))
    assert code == 1
    for result in report["runs"][0]["results"]:
        assert result["locations"][0]["physicalLocation"]["artifactLocation"] == {
            "uri": "sl/index.html", "uriBaseId": "%BUILDROOT%",
        }
        assert "relatedLocations" not in result


@pytest.mark.parametrize("mapping", [
    {"../escape.html": "src/page.astro"},
    {"sl/index.html": "../outside.astro"},
    {"sl/index.html": "C:/outside.astro"},
    {"sl/index.html": "src\\page.astro"},
    {"missing.html": "src/page.astro"},
])
def test_sarif_source_map_rejects_unsafe_or_unknown_paths(tmp_path, capsys, monkeypatch, mapping):
    manifest = _fixture(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "page.astro").write_text("hello")
    monkeypatch.chdir(tmp_path)
    source_map = _source_map(tmp_path, mapping)
    assert main([str(manifest), str(tmp_path), "--sarif", "--sarif-source-map", str(source_map)]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "Invalid input:" in output.err


def test_sarif_source_map_rejects_missing_source_file(tmp_path, capsys, monkeypatch):
    manifest = _fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    mapping = _source_map(tmp_path, {"sl/index.html": "src/does-not-exist.astro"})
    assert main([str(manifest), str(tmp_path), "--sarif", "--sarif-source-map", str(mapping)]) == 2
    assert "Invalid input:" in capsys.readouterr().err


def test_sarif_source_map_rejects_duplicate_artifact_keys(tmp_path, capsys, monkeypatch):
    manifest = _fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    mapping = tmp_path / "source-map.json"
    mapping.write_text('{"version":1,"sources":{"sl/index.html":"a","sl/index.html":"b"}}')
    assert main([str(manifest), str(tmp_path), "--sarif", "--sarif-source-map", str(mapping)]) == 2
    assert "Invalid input:" in capsys.readouterr().err


def test_sarif_source_map_rejects_casefold_duplicate_artifact_keys(tmp_path, capsys, monkeypatch):
    manifest = _fixture(tmp_path)
    source = tmp_path / "src" / "page.astro"
    source.parent.mkdir()
    source.write_text("hello")
    monkeypatch.chdir(tmp_path)
    mapping = _source_map(tmp_path, {
        "sl/index.html": "src/page.astro",
        "SL/index.html": "src/page.astro",
    })
    assert main([str(manifest), str(tmp_path), "--sarif", "--sarif-source-map", str(mapping)]) == 2
    assert "Duplicate source-map artifact" in capsys.readouterr().err


def test_sarif_source_map_rejects_unsupported_version(tmp_path, capsys, monkeypatch):
    manifest = _fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    mapping = tmp_path / "source-map.json"
    mapping.write_text('{"version":true,"sources":{}}')
    assert main([str(manifest), str(tmp_path), "--sarif", "--sarif-source-map", str(mapping)]) == 2
    assert "Unsupported source-map version" in capsys.readouterr().err


def test_source_mapping_only_with_sarif(tmp_path):
    manifest = _fixture(tmp_path)
    with pytest.raises(SystemExit) as failure:
        main([str(manifest), str(tmp_path), "--json", "--sarif-source-map", "x.json"])
    assert failure.value.code == 2
