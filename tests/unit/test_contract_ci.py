import json

import pytest

from searcharis.contract_ci import verify_contract
from searcharis.contract_cli import main

URL = "https://example.com/en/"
CONTRACT = {
    "url": URL,
    "title_present": True,
    "canonical": URL,
    "indexable": True,
    "h1_min": 1,
    "h1_max": 1,
    "hreflang": {"en": URL, "sl": "https://example.com/sl/"},
    "jsonld_types": ["WebPage"],
}
HTML = '''<!doctype html><html><head>
<title>Proper page</title>
<link rel="canonical" href="/en/">
<link rel="alternate" hreflang="en" href="/en/">
<link rel="alternate" hreflang="sl" href="/sl/">
<script type="application/ld+json">{"@graph":[{"@type":"WebPage"}]}</script>
</head><body><h1>Welcome</h1></body></html>'''


def test_good_page_passes():
    assert verify_contract(CONTRACT, HTML)["status"] == "pass"


def test_missing_title_detected():
    result = verify_contract(CONTRACT, HTML.replace("<title>Proper page</title>", ""))
    assert "title_presence" in {i["code"] for i in result["issues"]}


def test_noindex_and_none_detected():
    for robots in ("noindex,follow", "none"):
        result = verify_contract(CONTRACT, HTML.replace("</head>", f'<meta name="robots" content="{robots}"></head>'))
        assert "indexability_meta" in {i["code"] for i in result["issues"]}


def test_wrong_or_duplicate_canonical_detected():
    for replacement in ('href="/other/"', 'href="/en/"><link rel="canonical" href="/en/"'):
        result = verify_contract(CONTRACT, HTML.replace('href="/en/"', replacement, 1))
        assert "canonical" in {i["code"] for i in result["issues"]}


def test_missing_hreflang_and_h1_detected():
    result = verify_contract(CONTRACT, HTML.replace('hreflang="sl"', 'hreflang="fr"').replace("<h1>", "<h2>").replace("</h1>", "</h2>"))
    assert {"hreflang:sl", "h1_min"} <= {i["code"] for i in result["issues"]}


def test_invalid_and_missing_jsonld_reported():
    result = verify_contract(CONTRACT, HTML.replace('{"@graph":[{"@type":"WebPage"}]}', "{bad json}"))
    assert {"jsonld_invalid", "jsonld_type_missing"} <= {i["code"] for i in result["issues"]}


def test_empty_contract_is_not_silent_pass():
    with pytest.raises(ValueError, match="at least one"):
        verify_contract({"url": URL}, HTML)


def test_unsupported_check_is_rejected():
    with pytest.raises(ValueError, match="Unsupported"):
        verify_contract({"url": URL, "magic": True}, HTML)


def test_invalid_bounds_and_url_rejected():
    for contract in ({"url": URL, "h1_min": -1}, {"url": URL, "h1_min": 2, "h1_max": 1}, {"url": "file:///etc/passwd", "title_present": True}):
        with pytest.raises(ValueError):
            verify_contract(contract, HTML)


def test_cli_returns_correct_status(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    html = tmp_path / "index.html"
    contract.write_text(json.dumps(CONTRACT))
    html.write_text(HTML)
    assert main([str(contract), str(html), "--json"]) == 0
    assert '"status": "pass"' in capsys.readouterr().out
    html.write_text(HTML.replace('href="/en/"', 'href="/wrong/"', 1))
    assert main([str(contract), str(html)]) == 1
    assert "canonical" in capsys.readouterr().out
    contract.write_text('{"url": "oops"}')
    assert main([str(contract), str(html)]) == 2


def test_blank_html_cannot_pass_indexability_only_contract():
    report = verify_contract({"url": URL, "indexable": True}, "")
    assert report["status"] == "fail"
    assert "html_structure" in {i["code"] for i in report["issues"]}


def test_inert_template_content_does_not_satisfy_seo_contract():
    html = '''<html><head><template>
    <title>Invisible title</title><link rel="canonical" href="/en/">
    <link rel="alternate" hreflang="en" href="/en/">
    <link rel="alternate" hreflang="sl" href="/sl/">
    <meta name="robots" content="noindex">
    <script type="application/ld+json">{"@type":"WebPage"}</script>
    </template></head><body><template><h1>Invisible heading</h1></template></body></html>'''
    spec = {**CONTRACT, "indexable": False}
    result = verify_contract(spec, html, include_hreflang=True, include_canonical=True)
    assert {i["code"] for i in result["issues"]} == {
        "title_presence", "canonical", "indexability_meta", "h1_min",
        "hreflang:en", "hreflang:sl", "jsonld_type_missing",
    }
    assert result["observed_canonical"] == []
    assert result["observed_hreflang"] == {}


def test_nested_templates_do_not_add_ghost_links_or_headings():
    html = HTML.replace(
        "</head>",
        '<template><template><link rel="canonical" href="/bad/">'
        '<link rel="alternate" hreflang="en" href="/bad/">'
        '<meta name="robots" content="noindex"></template></template></head>',
    ).replace(
        "<h1>Welcome</h1>",
        "<template><template><h1>Ghost</h1></template></template><h1>Welcome</h1>",
    )
    result = verify_contract(CONTRACT, html, include_hreflang=True, include_canonical=True)
    assert result["status"] == "pass"
    assert result["observed_canonical"] == [URL]
    assert result["observed_hreflang"] == {
        "en": [URL], "sl": ["https://example.com/sl/"],
    }
