"""Combined offline release SARIF output keeps HTML and sitemap findings together."""

import json
import socket

from searcharis.release_cli import main


def _fixture(tmp_path):
    build = tmp_path / "dist"
    build.mkdir()
    (build / "index.html").write_text(
        '<html><head><title>Home</title></head><body><h1>Home</h1></body></html>',
        encoding="utf-8",
    )
    (build / "draft.html").write_text(
        '<html><head><title>Draft</title>'
        '<meta name="robots" content="noindex"></head><body></body></html>',
        encoding="utf-8",
    )
    manifest = tmp_path / "seo-manifest.json"
    manifest.write_text(json.dumps({"version": 1, "pages": [
        {"path": "index.html", "contract": {
            "url": "https://example.test/", "title_present": True,
            "indexable": True,
        }},
        {"path": "draft.html", "contract": {
            "url": "https://example.test/draft", "title_present": True,
            "indexable": False,
        }},
    ]}), encoding="utf-8")
    sitemap = tmp_path / "sitemap.xml"
    _sitemap(sitemap, ["https://example.test/"])
    return manifest, build, sitemap


def _sitemap(path, urls):
    path.write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{url}</loc></url>" for url in urls)
        + "</urlset>",
        encoding="utf-8",
    )


def _run(manifest, build, sitemap, *options):
    return main([str(manifest), str(build), str(sitemap), *map(str, options)])


def test_release_sarif_file_bundles_html_and_sitemap_findings_offline(
    tmp_path, capsys, monkeypatch,
):
    manifest, build, sitemap = _fixture(tmp_path)
    (build / "index.html").write_text("<html><head></head><body/></html>", encoding="utf-8")
    _sitemap(sitemap, ["https://example.test/draft"])
    def no_socket(*_args, **_kwargs):
        raise AssertionError("Offline release CI must never fetch a URL")
    monkeypatch.setattr(socket, "create_connection", no_socket)
    report_path = tmp_path / "report.sarif"
    assert _run(manifest, build, sitemap, "--sarif-file", report_path) == 1
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    raw = report_path.read_bytes()
    assert not raw.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf"))
    payload = json.loads(raw.decode("utf-8"))
    assert payload["version"] == "2.1.0"
    runs = payload["runs"]
    assert len(runs) == 1
    results = runs[0]["results"]
    ids = {result["ruleId"] for result in results}
    assert any(identifier.startswith("title") for identifier in ids)
    assert "sitemap_missing_required" in ids
    assert "sitemap_present_excluded" in ids
    assert all(result["partialFingerprints"] for result in results)
    assert all(result["locations"][0]["physicalLocation"]["region"]["startLine"] == 1 for result in results)
    sitemap_rows = [r for r in results if r["ruleId"].startswith("sitemap_")]
    assert all(r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "seo-manifest.json"
               for r in sitemap_rows)


def test_release_sarif_file_passes_without_findings(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    target = tmp_path / "pass.sarif"
    assert _run(manifest, build, sitemap, "--sarif-file", target) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["runs"][0]["results"] == []
    output = capsys.readouterr()
    assert output.out == output.err == ""


def test_release_sarif_file_is_create_only_and_cannot_touch_build(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    existing = tmp_path / "existing.sarif"
    existing.write_text("preserved", encoding="utf-8")
    assert _run(manifest, build, sitemap, "--sarif-file", existing) == 2
    assert existing.read_text(encoding="utf-8") == "preserved"
    target = build / "report.sarif"
    assert _run(manifest, build, sitemap, "--sarif-file", target) == 2
    assert not target.exists()
    assert "Invalid input" in capsys.readouterr().err


def test_release_sarif_preflights_other_artifact_paths_before_any_write(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    target = tmp_path / "report.sarif"
    missing = tmp_path / "missing" / "report.json"
    assert _run(manifest, build, sitemap, "--sarif-file", target, "--json-file", missing) == 2
    assert not target.exists()
    assert not missing.exists()
    assert _run(manifest, build, sitemap, "--sarif-file", target, "--json-file", target) == 2
    assert not target.exists()
    assert "Invalid input" in capsys.readouterr().err


def test_release_sarif_and_json_files_can_share_one_offline_pass(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    sarif = tmp_path / "release.sarif"
    raw_json = tmp_path / "release.json"
    assert _run(manifest, build, sitemap, "--sarif-file", sarif, "--json-file", raw_json) == 0
    assert json.loads(sarif.read_text(encoding="utf-8"))["runs"][0]["results"] == []
    assert json.loads(raw_json.read_text(encoding="utf-8"))["status"] == "pass"
    output = capsys.readouterr()
    assert output.out == output.err == ""


def test_release_sarif_preserves_all_four_sitemap_blocker_categories(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    (build / "alias.html").write_text(
        '<html><head><title>Alias</title>'
        '<link rel="canonical" href="https://example.test/"></head></html>',
        encoding="utf-8",
    )
    reviewed = json.loads(manifest.read_text(encoding="utf-8"))
    reviewed["pages"].append({"path": "alias.html", "contract": {
        "url": "https://example.test/alias", "title_present": True,
        "canonical": "https://example.test/", "indexable": True,
    }})
    manifest.write_text(json.dumps(reviewed), encoding="utf-8")
    _sitemap(sitemap, [
        "https://example.test/draft",
        "https://example.test/alias",
        "https://example.test/stale",
    ])
    target = tmp_path / "categories.sarif"
    assert _run(
        manifest, build, sitemap, "--sarif-file", target,
        "--reject-noncanonical-urls", "--reject-unreviewed-urls",
    ) == 1
    results = json.loads(target.read_text(encoding="utf-8"))["runs"][0]["results"]
    sitemap_rows = [row for row in results if row["ruleId"].startswith("sitemap_")]
    assert {row["ruleId"] for row in sitemap_rows} == {
        "sitemap_missing_required", "sitemap_present_excluded",
        "sitemap_present_noncanonical", "sitemap_unreviewed_urls",
    }
    assert len({row["partialFingerprints"]["searcharis/issue/v1"] for row in sitemap_rows}) == 4
    assert capsys.readouterr().out == ""


def test_release_sarif_refuses_input_errors_without_artifact(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    sitemap.write_text("<wrong/>", encoding="utf-8")
    target = tmp_path / "no-report.sarif"
    assert _run(manifest, build, sitemap, "--sarif-file", target) == 2
    assert not target.exists()
    assert "Invalid input" in capsys.readouterr().err


def test_release_sarif_accepts_reviewed_local_source_mappings(
    tmp_path, capsys, monkeypatch,
):
    manifest, build, sitemap = _fixture(tmp_path)
    (build / "index.html").write_text("<html><head></head></html>", encoding="utf-8")
    source = tmp_path / "src" / "home.html"
    source.parent.mkdir()
    source.write_text("<h1>Reviewed source</h1>", encoding="utf-8")
    mapping = tmp_path / "sources.json"
    mapping.write_text(json.dumps({
        "version": 1, "sources": {"index.html": "src/home.html"},
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "mapped.sarif"
    assert _run(
        manifest, build, sitemap, "--sarif-file", target,
        "--sarif-source-map", mapping,
    ) == 1
    results = json.loads(target.read_text(encoding="utf-8"))["runs"][0]["results"]
    html = next(result for result in results if not result["ruleId"].startswith("sitemap_"))
    assert html["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "src/home.html"
    assert html["relatedLocations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "index.html"
    assert capsys.readouterr().out == ""


def test_release_sarif_invalid_source_map_fails_without_writing_artifacts(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    mapping = tmp_path / "sources.json"
    mapping.write_text(json.dumps({
        "version": 1, "sources": {"index.html": "../unsafe.html"},
    }), encoding="utf-8")
    target = tmp_path / "report.sarif"
    paired = tmp_path / "report.json"
    assert _run(
        manifest, build, sitemap, "--sarif-file", target,
        "--sarif-source-map", mapping, "--json-file", paired,
    ) == 2
    assert not target.exists() and not paired.exists()
    assert "Invalid input" in capsys.readouterr().err
