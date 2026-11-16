"""Regression-only offline sitemap index gates for static-build SEO CI."""

import json
import os
import socket
from html import escape
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from searcharis.comparison_markdown import build_sitemap_comparison_markdown
from searcharis.sitemap_compare import main

_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def _manifest(tmp_path):
    path = tmp_path / "reviewed.json"
    path.write_text(json.dumps({
        "version": 1,
        "pages": [
            {"path": "index.html", "contract": {
                "url": "https://example.test/", "indexable": True,
            }},
            {"path": "about.html", "contract": {
                "url": "https://example.test/about", "indexable": True,
            }},
            {"path": "draft.html", "contract": {
                "url": "https://example.test/draft", "indexable": False,
            }},
        ],
    }), encoding="utf-8")
    return path


def _snapshot(tmp_path, name, *urls):
    build = tmp_path / name
    build.mkdir()
    shard = build / "sitemap-0.xml"
    shard.write_text(
        f'<urlset xmlns="{_NS}">'
        + "".join(f"<url><loc>{escape(url)}</loc></url>" for url in urls)
        + "</urlset>", encoding="utf-8",
    )
    index = build / "sitemap-index.xml"
    index.write_text(
        f'<sitemapindex xmlns="{_NS}"><sitemap>'
        '<loc>https://example.test/sitemap-0.xml</loc>'
        '</sitemap></sitemapindex>', encoding="utf-8",
    )
    return index


def _run(tmp_path, baseline_urls, candidate_urls, capsys, *options):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", *baseline_urls)
    candidate = _snapshot(tmp_path, "candidate", *candidate_urls)
    code = main([str(manifest), str(baseline), str(candidate), "--json", *options])
    return code, json.loads(capsys.readouterr().out)


def test_inherited_missing_indexable_url_does_not_block(tmp_path, capsys, monkeypatch):
    def deny_network(*args, **kwargs):
        raise AssertionError("Offline sitemap comparison must never open a socket")
    monkeypatch.setattr(socket, "socket", deny_network)
    code, report = _run(
        tmp_path, ["https://example.test/"], ["https://example.test/"], capsys,
    )
    assert code == 0
    assert report["status"] == "pass"
    assert report["inherited"]["missing_required"] == ["https://example.test/about"]
    assert report["new"]["missing_required"] == []


def test_new_missing_indexable_url_blocks(tmp_path, capsys):
    code, report = _run(
        tmp_path, ["https://example.test/", "https://example.test/about"],
        ["https://example.test/"], capsys,
    )
    assert code == 1
    assert report["status"] == "regression"
    assert report["new"]["missing_required"] == ["https://example.test/about"]
    assert report["summary"]["new"] == 1


def test_new_noindex_url_blocks_without_inherited_debt(tmp_path, capsys):
    code, report = _run(
        tmp_path, ["https://example.test/"],
        ["https://example.test/", "https://example.test/draft"], capsys,
    )
    assert code == 1
    assert report["new"]["present_excluded"] == ["https://example.test/draft"]
    assert report["inherited"]["missing_required"] == ["https://example.test/about"]


def test_resolved_sitemap_issues_do_not_block(tmp_path, capsys):
    code, report = _run(
        tmp_path, ["https://example.test/", "https://example.test/draft"],
        ["https://example.test/", "https://example.test/about"], capsys,
    )
    assert code == 0
    assert report["summary"]["resolved"] == 2
    assert report["new"]["present_excluded"] == []


def test_unreviewed_sitemap_urls_are_opt_in(tmp_path, capsys):
    extra = "https://example.test/new-page"
    code, report = _run(
        tmp_path, ["https://example.test/"],
        ["https://example.test/", extra], capsys,
    )
    assert code == 0
    assert "unreviewed_urls" not in report["new"]


def test_new_unreviewed_sitemap_url_is_optional_blocker(tmp_path, capsys):
    extra = "https://example.test/new-page"
    code, report = _run(
        tmp_path, ["https://example.test/"],
        ["https://example.test/", extra], capsys, "--reject-unreviewed-urls",
    )
    assert code == 1
    assert report["new"]["unreviewed_urls"] == [extra]


def test_equivalent_unreviewed_url_spellings_are_not_regressions(tmp_path, capsys):
    code, report = _run(
        tmp_path,
        ["https://example.test/", "https://EXAMPLE.test/%7E"],
        ["https://example.test/", "https://example.test/~"],
        capsys, "--reject-unreviewed-urls",
    )
    assert code == 0
    assert report["new"]["unreviewed_urls"] == []
    assert report["inherited"]["unreviewed_urls"] == ["https://example.test/~"]


def test_new_reviewed_noncanonical_url_is_optional_blocker(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["pages"].append({
        "path": "alias.html",
        "contract": {"url": "https://example.test/alias", "indexable": True,
                     "canonical": "https://example.test/"},
    })
    manifest.write_text(json.dumps(data), encoding="utf-8")
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(
        tmp_path, "candidate", "https://example.test/", "https://example.test/alias",
    )
    assert main([str(manifest), str(baseline), str(candidate),
                 "--reject-noncanonical-urls", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["new"]["present_noncanonical"] == ["https://example.test/alias"]


def test_malformed_candidate_fails_closed(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    (candidate.parent / "sitemap-0.xml").write_text("<urlset", encoding="utf-8")
    assert main([str(manifest), str(baseline), str(candidate), "--json"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["status"] == "error"
    assert "sitemap-0.xml" in error["error"]


def test_same_snapshot_cannot_compare_to_itself(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    index = _snapshot(tmp_path, "build", "https://example.test/")
    assert main([str(manifest), str(index), str(index), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_different_public_sites_are_not_comparable(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    candidate.write_text(
        candidate.read_text(encoding="utf-8").replace(
            "https://example.test/sitemap-0.xml", "https://other.test/sitemap-0.xml",
        ), encoding="utf-8",
    )
    assert main([str(manifest), str(baseline), str(candidate), "--json"]) == 2
    assert "same site" in json.loads(capsys.readouterr().out)["error"]


def test_shared_hardlinked_build_artifact_rejected(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    target = candidate.parent / "sitemap-0.xml"
    target.unlink()
    try:
        os.link(baseline.parent / "sitemap-0.xml", target)
    except OSError as error:
        pytest.skip(f"Hardlinks unavailable on test filesystem: {error}")
    assert main([str(manifest), str(baseline), str(candidate), "--json"]) == 2
    assert "hard-linked" in json.loads(capsys.readouterr().out)["error"]


def test_human_output_bounds_url_details(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    base = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/draft")
    assert main([str(manifest), str(base), str(candidate)]) == 1
    human = capsys.readouterr().out
    assert "REGRESSION" in human
    assert "https://example.test/draft" in human


@pytest.mark.parametrize("has_regression,exit_code", [(False, 0), (True, 1)])
def test_json_file_exports_complete_comparison_without_stdout(
    tmp_path, capsys, has_regression, exit_code,
):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    urls = ["https://example.test/"]
    if has_regression:
        urls.append("https://example.test/draft")
    candidate = _snapshot(tmp_path, "candidate", *urls)
    artifact = tmp_path / "comparison.json"
    assert main([
        str(manifest), str(baseline), str(candidate), "--json-file", str(artifact),
    ]) == exit_code
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""
    assert artifact.read_bytes().endswith(b"\n")
    report = json.loads(artifact.read_text(encoding="utf-8"))
    assert report["status"] == ("regression" if has_regression else "pass")
    assert report["summary"]["new"] == int(has_regression)
    assert report["new"]["present_excluded"] == (
        ["https://example.test/draft"] if has_regression else []
    )


def test_json_file_refuses_existing_destination_without_overwrite(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    artifact = tmp_path / "comparison.json"
    artifact.write_bytes(b"important existing data")
    assert main([
        str(manifest), str(baseline), str(candidate), "--json-file", str(artifact),
    ]) == 2
    assert artifact.read_bytes() == b"important existing data"
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("root", ["baseline", "candidate"])
def test_json_file_never_mutates_either_build_root(tmp_path, capsys, root):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    directory = tmp_path / root / "reports"
    directory.mkdir()
    artifact = directory / "comparison.json"
    assert main([
        str(manifest), str(baseline), str(candidate), "--json-file", str(artifact),
    ]) == 2
    assert not artifact.exists()
    assert capsys.readouterr().out == ""


def test_json_file_input_error_does_not_create_artifact(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    (candidate.parent / "sitemap-0.xml").write_text("<urlset", encoding="utf-8")
    artifact = tmp_path / "comparison.json"
    assert main([
        str(manifest), str(baseline), str(candidate), "--json-file", str(artifact),
    ]) == 2
    assert not artifact.exists()
    assert "sitemap-0.xml" in capsys.readouterr().err


def test_json_file_requires_existing_parent_directory(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    artifact = tmp_path / "absent" / "comparison.json"
    assert main([
        str(manifest), str(baseline), str(candidate), "--json-file", str(artifact),
    ]) == 2
    assert not artifact.exists()
    assert "existing directory" in capsys.readouterr().err


def test_json_file_cleans_up_new_artifact_after_serialization_error(
    tmp_path, capsys, monkeypatch,
):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    artifact = tmp_path / "comparison.json"
    real_dumps = json.dumps

    def failed_dump(value, *args, **kwargs):
        if isinstance(value, dict) and "new" in value:
            raise OSError("simulated output failure")
        return real_dumps(value, *args, **kwargs)

    monkeypatch.setattr("searcharis.sitemap_compare.json.dumps", failed_dump)
    assert main([
        str(manifest), str(baseline), str(candidate), "--json-file", str(artifact),
    ]) == 2
    assert not artifact.exists()
    assert "simulated output failure" in capsys.readouterr().err


def test_json_file_cannot_be_combined_with_json_stdout(tmp_path):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    with pytest.raises(SystemExit, match="2"):
        main([
            str(manifest), str(baseline), str(candidate),
            "--json", "--json-file", str(tmp_path / "comparison.json"),
        ])


def test_junit_stdout_tolerates_inherited_debt(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    assert main([str(manifest), str(baseline), str(candidate), "--junit"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    suite = ET.fromstring(output.out)
    assert suite.attrib["failures"] == "0"
    assert suite.attrib["tests"] == "1"
    assert suite.find("testcase/failure") is None


def test_junit_stdout_reports_only_new_blocking_urls(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/", "https://example.test/about")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/", "https://example.test/draft")
    assert main([str(manifest), str(baseline), str(candidate), "--junit"]) == 1
    suite = ET.fromstring(capsys.readouterr().out)
    assert suite.attrib["failures"] == "2"
    failures = suite.findall("testcase/failure")
    assert len(failures) == 2
    assert any("missing_required" in failure.text and "about" in failure.text for failure in failures)
    assert any("present_excluded" in failure.text and "draft" in failure.text for failure in failures)


def test_junit_opt_in_unreviewed_url_is_xml_escaped_and_blocking(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    extra = "https://example.test/new?from=one&other=two"
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/", extra)
    assert main([
        str(manifest), str(baseline), str(candidate),
        "--reject-unreviewed-urls", "--junit",
    ]) == 1
    output = capsys.readouterr().out
    assert "&amp;" in output
    suite = ET.fromstring(output)
    assert suite.attrib["failures"] == "1"
    assert extra in suite.find("testcase/failure").text


@pytest.mark.parametrize("regression,code", [(False, 0), (True, 1)])
def test_junit_file_creates_portable_xml_without_stdout(tmp_path, capsys, regression, code):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    urls = ["https://example.test/"]
    if regression:
        urls.append("https://example.test/draft")
    candidate = _snapshot(tmp_path, "candidate", *urls)
    artifact = tmp_path / "sitemap-comparison.xml"
    assert main([str(manifest), str(baseline), str(candidate), "--junit-file", str(artifact)]) == code
    output = capsys.readouterr()
    assert output.out == output.err == ""
    assert artifact.read_bytes().startswith(b"<?xml")
    suite = ET.parse(artifact).getroot()
    assert suite.attrib["failures"] == str(int(regression))
    assert suite.attrib["tests"] == "1"


def test_junit_file_refuses_existing_destination(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    artifact = tmp_path / "comparison.xml"
    artifact.write_bytes(b"existing results")
    assert main([str(manifest), str(baseline), str(candidate), "--junit-file", str(artifact)]) == 2
    assert artifact.read_bytes() == b"existing results"
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("directory", ["baseline", "candidate"])
def test_junit_file_cannot_modify_build_inputs(tmp_path, capsys, directory):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    artifact = tmp_path / directory / "comparison.xml"
    assert main([str(manifest), str(baseline), str(candidate), "--junit-file", str(artifact)]) == 2
    assert not artifact.exists()
    assert capsys.readouterr().out == ""


def test_junit_file_invalid_xml_input_creates_no_artifact(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    (candidate.parent / "sitemap-0.xml").write_text("<urlset", encoding="utf-8")
    artifact = tmp_path / "comparison.xml"
    assert main([str(manifest), str(baseline), str(candidate), "--junit-file", str(artifact)]) == 2
    assert not artifact.exists()
    assert "sitemap-0.xml" in capsys.readouterr().err


def test_junit_file_removes_new_partial_artifact_on_serialization_error(tmp_path, capsys, monkeypatch):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    artifact = tmp_path / "comparison.xml"

    def broken_junit(_comparison):
        raise OSError("injected XML write failure")

    monkeypatch.setattr("searcharis.sitemap_compare.build_sitemap_comparison_junit", broken_junit, raising=False)
    assert main([str(manifest), str(baseline), str(candidate), "--junit-file", str(artifact)]) == 2
    assert not artifact.exists()
    assert "injected XML write failure" in capsys.readouterr().err


def test_junit_file_is_exclusive_with_json_stdout(tmp_path):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    with pytest.raises(SystemExit, match="2"):
        main([str(manifest), str(baseline), str(candidate), "--json", "--junit-file", str(tmp_path / "comparison.xml")])


def test_markdown_stdout_shows_inherited_and_new_without_claiming_clean_site(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(
        tmp_path, "candidate", "https://example.test/", "https://example.test/draft",
    )
    assert main([str(manifest), str(baseline), str(candidate), "--markdown"]) == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert "# Searcharis SEO sitemap comparison" in output.out
    assert "1 new" in output.out.lower()
    assert "1 inherited" in output.out.lower()
    assert "https://example.test/draft" in output.out
    assert "https://example.test/about" not in output.out
    assert "indexing" in output.out.lower()


@pytest.mark.parametrize("regression,code", [(False, 0), (True, 1)])
def test_markdown_file_is_portable_and_respects_exit_code(tmp_path, capsys, regression, code):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    urls = ["https://example.test/"]
    if regression:
        urls.append("https://example.test/draft")
    candidate = _snapshot(tmp_path, "candidate", *urls)
    artifact = tmp_path / "review.md"
    assert main([str(manifest), str(baseline), str(candidate), "--markdown-file", str(artifact)]) == code
    assert capsys.readouterr().out == ""
    payload = artifact.read_bytes()
    assert payload.startswith(b"# Searcharis SEO sitemap comparison\n")
    assert payload.endswith(b"\n")
    assert ("https://example.test/draft" in payload.decode("utf-8")) is regression


def test_markdown_escapes_unreviewed_urls_and_exposes_optional_gate(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    url = "https://example.test/new?<img=1&x=2"
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/", url)
    assert main([
        str(manifest), str(baseline), str(candidate), "--reject-unreviewed-urls", "--markdown",
    ]) == 1
    markdown = capsys.readouterr().out
    assert "&lt;img" in markdown
    assert "&amp;" in markdown
    assert "<img" not in markdown
    assert "unreviewed_urls" in markdown


def test_markdown_file_refuses_existing_destination(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    artifact = tmp_path / "review.md"
    artifact.write_text("preserve this", encoding="utf-8")
    assert main([str(manifest), str(baseline), str(candidate), "--markdown-file", str(artifact)]) == 2
    assert artifact.read_text(encoding="utf-8") == "preserve this"
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("root", ["baseline", "candidate"])
def test_markdown_file_cannot_write_into_either_build(tmp_path, capsys, root):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    artifact = tmp_path / root / "review.md"
    assert main([str(manifest), str(baseline), str(candidate), "--markdown-file", str(artifact)]) == 2
    assert not artifact.exists()
    assert capsys.readouterr().out == ""


def test_markdown_file_invalid_input_does_not_create_artifact(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    (candidate.parent / "sitemap-0.xml").write_text("<urlset", encoding="utf-8")
    artifact = tmp_path / "review.md"
    assert main([str(manifest), str(baseline), str(candidate), "--markdown-file", str(artifact)]) == 2
    assert not artifact.exists()
    assert "sitemap-0.xml" in capsys.readouterr().err


def test_markdown_file_removes_own_partial_file_on_serialization_error(
    tmp_path, capsys, monkeypatch,
):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    artifact = tmp_path / "review.md"

    def broken_markdown(_result):
        raise OSError("injected Markdown serialization failure")

    monkeypatch.setattr(
        "searcharis.sitemap_compare.build_sitemap_comparison_markdown",
        broken_markdown, raising=False,
    )
    assert main([str(manifest), str(baseline), str(candidate), "--markdown-file", str(artifact)]) == 2
    assert not artifact.exists()
    assert "injected Markdown" in capsys.readouterr().err


def test_markdown_file_is_exclusive_with_junit_stdout(tmp_path):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    with pytest.raises(SystemExit, match="2"):
        main([
            str(manifest), str(baseline), str(candidate),
            "--junit", "--markdown-file", str(tmp_path / "review.md"),
        ])


def test_markdown_file_requires_existing_parent(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    artifact = tmp_path / "missing-dir" / "review.md"
    assert main([str(manifest), str(baseline), str(candidate), "--markdown-file", str(artifact)]) == 2
    assert not artifact.exists()
    assert "existing directory" in capsys.readouterr().err


@pytest.mark.parametrize("regression,code", [(False, 0), (True, 1)])
def test_sitemap_comparison_writes_json_junit_and_markdown_in_one_pass(
    tmp_path, capsys, monkeypatch, regression, code,
):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    urls = ["https://example.test/"]
    if regression:
        urls.append("https://example.test/draft")
    candidate = _snapshot(tmp_path, "candidate", *urls)
    targets = [tmp_path / "comparison.json", tmp_path / "comparison.xml", tmp_path / "review.md"]

    def deny_network(*args, **kwargs):
        raise AssertionError("Sitemap report creation must remain offline")

    monkeypatch.setattr(socket, "socket", deny_network)
    assert main([
        str(manifest), str(baseline), str(candidate),
        "--json-file", str(targets[0]),
        "--junit-file", str(targets[1]),
        "--markdown-file", str(targets[2]),
    ]) == code
    assert capsys.readouterr().out == ""
    result = json.loads(targets[0].read_text(encoding="utf-8"))
    assert result["summary"]["new"] == int(regression)
    assert ET.parse(targets[1]).getroot().attrib["failures"] == str(int(regression))
    assert "# Searcharis SEO sitemap comparison" in targets[2].read_text(encoding="utf-8")
    assert all(target.read_bytes().endswith(b"\n") for target in targets)


def test_sitemap_sarif_stdout_contains_only_new_regressions(tmp_path, capsys, monkeypatch):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(
        tmp_path, "candidate", "https://example.test/", "https://example.test/draft",
    )

    def deny_network(*args, **kwargs):
        raise AssertionError("SARIF export must remain fully offline")

    monkeypatch.setattr(socket, "socket", deny_network)
    args = [str(manifest), str(baseline), str(candidate), "--sarif"]
    assert main(args) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["version"] == "2.1.0"
    results = report["runs"][0]["results"]
    assert len(results) == 1
    assert results[0]["ruleId"] == "sitemap_present_excluded"
    assert "https://example.test/draft" in results[0]["message"]["text"]
    assert "https://example.test/about" not in results[0]["message"]["text"]
    assert results[0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "reviewed.json"
    assert len(results[0]["partialFingerprints"]["searcharis/issue/v1"]) == 64
    assert main(args) == 1
    assert json.loads(capsys.readouterr().out) == report


def test_sitemap_sarif_stdout_inherited_only_is_clean(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    assert main([str(manifest), str(baseline), str(candidate), "--sarif"]) == 0
    assert json.loads(capsys.readouterr().out)["runs"][0]["results"] == []


def test_github_annotations_only_new_gated_findings_and_offline(tmp_path, capsys, monkeypatch):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(
        tmp_path, "candidate", "https://example.test/", "https://example.test/draft",
    )
    monkeypatch.setattr(socket, "socket", lambda *a, **kw: pytest.fail("Network attempted"))
    assert main([
        str(manifest), str(baseline), str(candidate), "--github-annotations",
    ]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("::error title=Sitemap present_excluded::")
    assert "https://example.test/draft" in lines[0]
    assert "https://example.test/about" not in lines[0]


def test_github_annotations_inherited_debt_has_no_output(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    assert main([
        str(manifest), str(baseline), str(candidate), "--github-annotations",
    ]) == 0
    assert capsys.readouterr().out == ""


def test_github_annotations_escape_untrusted_percent_and_command_text(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    attack = "https://example.test/new?x=%0A::error%20title=forged::payload"
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/", attack)
    assert main([
        str(manifest), str(baseline), str(candidate), "--reject-unreviewed-urls",
        "--github-annotations",
    ]) == 1
    output = capsys.readouterr().out
    assert output.count("::error title=") == 1
    assert "%250A" in output and "%2520" in output
    assert "\n::error" not in output


def test_github_annotations_bounded_fair_and_escapes_literal_controls():
    from searcharis.sitemap_compare import format_github_annotations

    findings = {
        "missing_required": [f"https://example.test/missing/{n}" for n in range(40)],
        "present_excluded": ["https://example.test/noindex"],
        "present_noncanonical": ["https://example.test/canonical"],
        "unreviewed_urls": ["https://example.test/unsafe%\n::error\rmore"],
    }
    lines = format_github_annotations({"new": findings})
    assert len(lines) == 20
    assert {category for category in findings if any(category in line for line in lines)} == set(findings)
    assert all(line.startswith("::error title=Sitemap ") for line in lines)
    assert all("\n" not in line and "\r" not in line for line in lines)
    assert "%25" in lines[-1] or any("%25" in line for line in lines)


def test_github_annotations_cannot_mix_with_file_reports(tmp_path):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    with pytest.raises(SystemExit, match="2"):
        main([
            str(manifest), str(baseline), str(candidate), "--github-annotations",
            "--json-file", str(tmp_path / "out.json"),
        ])


def test_sitemap_sarif_includes_all_four_new_gated_categories(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["pages"].append({
        "path": "alias.html",
        "contract": {
            "url": "https://example.test/alias",
            "canonical": "https://example.test/",
            "indexable": True,
        },
    })
    manifest.write_text(json.dumps(data), encoding="utf-8")
    baseline = _snapshot(
        tmp_path, "baseline", "https://example.test/", "https://example.test/about",
    )
    candidate = _snapshot(
        tmp_path, "candidate", "https://example.test/", "https://example.test/draft",
        "https://example.test/alias", "https://example.test/unreviewed?<test=1&x=2",
    )
    assert main([
        str(manifest), str(baseline), str(candidate),
        "--reject-noncanonical-urls", "--reject-unreviewed-urls", "--sarif",
    ]) == 1
    report = json.loads(capsys.readouterr().out)
    run = report["runs"][0]
    assert {item["ruleId"] for item in run["results"]} == {
        "sitemap_missing_required",
        "sitemap_present_excluded",
        "sitemap_present_noncanonical",
        "sitemap_unreviewed_urls",
    }
    assert len(run["results"]) == 4
    assert all(
        item["level"] == "error" and
        item["locations"][0]["physicalLocation"]["region"]["startLine"] == 1
        for item in run["results"]
    )


def test_sitemap_sarif_stdout_is_exclusive_with_file_reports(tmp_path):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    with pytest.raises(SystemExit, match="2"):
        main([
            str(manifest), str(baseline), str(candidate),
            "--sarif", "--json-file", str(tmp_path / "review.json"),
        ])


def test_sitemap_sarif_file_combines_with_other_reports(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(
        tmp_path, "candidate", "https://example.test/", "https://example.test/draft",
    )
    targets = [tmp_path / name for name in (
        "review.json", "review.xml", "review.md", "review.sarif",
    )]
    assert main([
        str(manifest), str(baseline), str(candidate),
        "--json-file", str(targets[0]), "--junit-file", str(targets[1]),
        "--markdown-file", str(targets[2]), "--sarif-file", str(targets[3]),
    ]) == 1
    assert capsys.readouterr().out == ""
    assert all(path.exists() for path in targets)
    assert json.loads(targets[0].read_text(encoding="utf-8"))["summary"]["new"] == 1
    assert ET.parse(targets[1]).getroot().attrib["failures"] == "1"
    assert "1 new" in targets[2].read_text(encoding="utf-8")
    assert len(json.loads(targets[3].read_text(encoding="utf-8"))["runs"][0]["results"]) == 1


def test_sitemap_sarif_file_refuses_overwrite_without_creating_other_reports(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    protected = tmp_path / "protected.sarif"
    protected.write_bytes(b"other owner")
    json_path = tmp_path / "review.json"
    assert main([
        str(manifest), str(baseline), str(candidate),
        "--json-file", str(json_path), "--sarif-file", str(protected),
    ]) == 2
    assert protected.read_bytes() == b"other owner"
    assert not json_path.exists()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("root", ["baseline", "candidate"])
def test_sitemap_sarif_file_rejects_write_inside_snapshots(tmp_path, capsys, root):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    target = tmp_path / root / "review.sarif"
    assert main([
        str(manifest), str(baseline), str(candidate), "--sarif-file", str(target),
    ]) == 2
    assert not target.exists()
    assert capsys.readouterr().out == ""


def test_multiple_sitemap_outputs_preflight_all_destinations(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    first = tmp_path / "comparison.json"
    invalid = tmp_path / "missing-parent" / "comparison.xml"
    assert main([
        str(manifest), str(baseline), str(candidate),
        "--json-file", str(first), "--junit-file", str(invalid),
    ]) == 2
    assert not first.exists() and not invalid.exists()
    assert "existing directory" in capsys.readouterr().err


def test_multiple_sitemap_outputs_reject_duplicate_and_existing_files(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    first = tmp_path / "comparison.json"
    assert main([
        str(manifest), str(baseline), str(candidate),
        "--json-file", str(first), "--junit-file", str(first),
    ]) == 2
    assert not first.exists()
    assert "distinct" in capsys.readouterr().err

    existing = tmp_path / "existing.xml"
    existing.write_bytes(b"important existing content")
    assert main([
        str(manifest), str(baseline), str(candidate),
        "--json-file", str(first), "--junit-file", str(existing),
    ]) == 2
    assert not first.exists()
    assert existing.read_bytes() == b"important existing content"


def test_multiple_sitemap_outputs_cleanup_after_later_open_failure(
    tmp_path, capsys, monkeypatch,
):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    first = tmp_path / "comparison.json"
    second = tmp_path / "comparison.xml"
    real_open = Path.open

    def fail_second_open(path, mode="r", *args, **kwargs):
        if path == second and mode == "x":
            raise OSError("simulated second-report I/O failure")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_second_open)
    assert main([
        str(manifest), str(baseline), str(candidate),
        "--json-file", str(first), "--junit-file", str(second),
    ]) == 2
    assert not first.exists() and not second.exists()
    assert "simulated second-report I/O failure" in capsys.readouterr().err


def test_multiple_sitemap_outputs_do_not_delete_replaced_report(
    tmp_path, capsys, monkeypatch,
):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    first = tmp_path / "comparison.json"
    second = tmp_path / "comparison.xml"
    replacement = tmp_path / "external-owner.txt"
    replacement.write_text("concurrent owner's data", encoding="utf-8")
    real_open = Path.open

    def replace_then_fail(path, mode="r", *args, **kwargs):
        if path == second and mode == "x":
            os.replace(replacement, first)
            raise OSError("second report failed after replacement")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", replace_then_fail)
    assert main([
        str(manifest), str(baseline), str(candidate),
        "--json-file", str(first), "--junit-file", str(second),
    ]) == 2
    assert first.read_text(encoding="utf-8") == "concurrent owner's data"
    assert not second.exists()
    assert "cleanup skipped" in capsys.readouterr().err


def test_multiple_sitemap_outputs_report_cleanup_denial(tmp_path, capsys, monkeypatch):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    first = tmp_path / "comparison.json"
    second = tmp_path / "comparison.xml"
    real_open = Path.open
    real_unlink = Path.unlink

    def fail_second_open(path, mode="r", *args, **kwargs):
        if path == second and mode == "x":
            raise OSError("second output failed")
        return real_open(path, mode, *args, **kwargs)

    def deny_first_cleanup(path, *args, **kwargs):
        if path == first:
            raise PermissionError("cleanup denied")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_second_open)
    monkeypatch.setattr(Path, "unlink", deny_first_cleanup)
    assert main([
        str(manifest), str(baseline), str(candidate),
        "--json-file", str(first), "--junit-file", str(second),
    ]) == 2
    assert first.exists() and not second.exists()
    assert "partial report cleanup failed" in capsys.readouterr().err


def test_multiple_sitemap_outputs_reject_build_directory_before_writing(
    tmp_path, capsys,
):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    first = tmp_path / "comparison.json"
    forbidden = candidate.parent / "comparison.xml"
    assert main([
        str(manifest), str(baseline), str(candidate),
        "--json-file", str(first), "--junit-file", str(forbidden),
    ]) == 2
    assert not first.exists() and not forbidden.exists()
    assert "outside both sitemap directories" in capsys.readouterr().err


def test_multiple_sitemap_outputs_serialization_error_creates_nothing(
    tmp_path, capsys, monkeypatch,
):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    first = tmp_path / "comparison.json"
    second = tmp_path / "comparison.xml"

    def broken_junit(_result):
        raise OSError("injected serializer error")

    monkeypatch.setattr("searcharis.sitemap_compare.build_sitemap_comparison_junit", broken_junit)
    assert main([
        str(manifest), str(baseline), str(candidate),
        "--json-file", str(first), "--junit-file", str(second),
    ]) == 2
    assert not first.exists() and not second.exists()
    assert "injected serializer error" in capsys.readouterr().err


def test_multiple_sitemap_outputs_preserve_stdout_exclusivity(tmp_path):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    with pytest.raises(SystemExit, match="2"):
        main([
            str(manifest), str(baseline), str(candidate), "--json",
            "--json-file", str(tmp_path / "comparison.json"),
            "--junit-file", str(tmp_path / "comparison.xml"),
        ])


def test_markdown_bounded_findings_preserve_all_new_categories():
    new = {
        "missing_required": [f"https://example.test/missing-{i}" for i in range(30)],
        "present_excluded": ["https://example.test/noindex"],
        "present_noncanonical": ["https://example.test/alias"],
        "unreviewed_urls": ["https://example.test/unreviewed"],
    }
    report = {
        "status": "regression",
        "summary": {
            "new": 33, "inherited": 2, "resolved": 1,
            "baseline_sitemap_urls": 50, "candidate_sitemap_urls": 45,
        },
        "new": new,
    }
    markdown = build_sitemap_comparison_markdown(report)
    assert markdown.count("\n- ") < 30
    assert markdown.count("(missing_required)") == 17
    assert "https://example.test/noindex" in markdown
    assert "https://example.test/alias" in markdown
    assert "https://example.test/unreviewed" in markdown
    assert "13 more new findings" in markdown
