"""Optional sitemap comparison policies must match across verified reports."""

import json

import pytest

from searcharis.sitemap_ci import verify_sitemap_manifest
from searcharis.sitemap_compare import compare_sitemap_reports, main

MANIFEST = {
    "version": 1,
    "pages": [{"path": "index.html", "contract": {
        "url": "https://example.test/", "indexable": True,
    }}],
}


def _sitemap(tmp_path, name, *urls):
    path = tmp_path / name
    path.write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{url}</loc></url>" for url in urls)
        + "</urlset>",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("option", [
    "reject_noncanonical_urls", "reject_unreviewed_urls",
])
def test_verified_reports_with_different_optional_checks_are_incompatible(tmp_path, option):
    sitemap = _sitemap(
        tmp_path, "sitemap.xml", "https://example.test/",
        "https://example.test/unreviewed",
    )
    unchecked = verify_sitemap_manifest(MANIFEST, sitemap)
    checked = verify_sitemap_manifest(MANIFEST, sitemap, **{option: True})
    assert unchecked["status"] == "pass"
    if option == "reject_unreviewed_urls":
        assert checked["status"] == "fail"
        assert checked["unreviewed_urls"] == ["https://example.test/unreviewed"]

    for before, after in [(unchecked, checked), (checked, unchecked)]:
        with pytest.raises(ValueError, match="different optional checks"):
            compare_sitemap_reports(before, after)


def test_matching_optional_checks_still_detect_real_sitemap_regressions(tmp_path):
    before = verify_sitemap_manifest(
        MANIFEST, _sitemap(tmp_path, "before.xml", "https://example.test/"),
        reject_unreviewed_urls=True,
    )
    after = verify_sitemap_manifest(
        MANIFEST, _sitemap(tmp_path, "after.xml",
            "https://example.test/", "https://example.test/unreviewed"),
        reject_unreviewed_urls=True,
    )
    result = compare_sitemap_reports(before, after)
    assert result["status"] == "regression"
    assert result["summary"]["new"] == 1
    assert result["new"]["unreviewed_urls"] == ["https://example.test/unreviewed"]


@pytest.mark.parametrize(
    ("strict", "complete", "remove_required", "expected_code", "expected_status"),
    [
        (False, False, False, 0, "pass"),
        (True, False, False, 2, "error"),
        (True, True, False, 0, "pass"),
        (True, True, True, 1, "regression"),
    ],
)
def test_comparison_indexability_coverage(
    tmp_path, capsys, strict, complete, remove_required, expected_code, expected_status,
):
    extra = {"url": "https://example.test/private"}
    if complete:
        extra["indexable"] = False
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "pages": [*MANIFEST["pages"], {"path": "private.html", "contract": extra}],
    }), encoding="utf-8")
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()
    baseline = _sitemap(before, "sitemap.xml", "https://example.test/")
    candidate = _sitemap(after, "sitemap.xml", *(
        () if remove_required else ("https://example.test/",)
    ))
    args = [str(manifest), str(baseline), str(candidate), "--single-sitemap", "--json"]
    if strict:
        args.append("--require-indexability-coverage")
    assert main(args) == expected_code
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == expected_status
    if expected_code == 2:
        assert "lack an explicit indexability assertion" in result["error"]
    elif expected_code == 1:
        assert result["new"]["missing_required"] == ["https://example.test/"]
