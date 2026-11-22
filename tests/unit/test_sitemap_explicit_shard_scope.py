"""Regression: explicit sitemap shards must share an effective site authority."""

from html import escape

import pytest

from searcharis.sitemap_ci import verify_sitemap_manifest


def _sitemap(path, url):
    path.write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{escape(url)}</loc></url></urlset>",
        encoding="utf-8",
    )
    return path


def _manifest():
    return {
        "version": 1,
        "pages": [
            {"path": "index.html", "contract": {
                "url": "https://example.test/", "indexable": True,
            }},
        ],
    }


@pytest.mark.parametrize("other_url", [
    "http://example.test/other",
    "https://example.test:8443/other",
])
def test_explicit_shards_reject_mixed_scheme_or_effective_port(tmp_path, other_url):
    first = _sitemap(tmp_path / "first.xml", "https://example.test/")
    second = _sitemap(tmp_path / "second.xml", other_url)

    # A site gate must not green-light different schemes or authorities merely
    # because the two local XML shards happen to share the hostname.
    with pytest.raises(ValueError, match="scheme, host, and effective port"):
        verify_sitemap_manifest(_manifest(), [first, second])


def test_explicit_shards_accept_equivalent_host_and_default_port(tmp_path):
    first = _sitemap(tmp_path / "first.xml", "https://example.test/")
    second = _sitemap(tmp_path / "second.xml", "https://EXAMPLE.test:443/other")

    result = verify_sitemap_manifest(_manifest(), [first, second])
    assert result["status"] == "pass"
    assert result["summary"]["sitemap_urls"] == 2
