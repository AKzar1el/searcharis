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


@pytest.mark.parametrize("first_title", ["", "   "])
def test_later_duplicate_title_cannot_rescue_empty_first_title(first_title):
    html = (
        f"<html><head><title>{first_title}</title><title>Fallback title</title>"
        "</head><body></body></html>"
    )
    result = verify_contract({"url": URL, "title_present": True}, html)
    assert result["status"] == "fail"
    assert {issue["code"] for issue in result["issues"]} == {"title_presence"}


def test_later_blank_duplicate_title_does_not_replace_first_head_title():
    html = "<html><head><title>Primary</title><title></title></head><body></body></html>"
    result = verify_contract({"url": URL, "title_present": True}, html)
    assert result["status"] == "pass"


def test_cli_rejects_duplicate_title_false_pass(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    html = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "title_present": True}))
    html.write_text(
        "<html><head><title></title><title>Fallback</title></head><body></body></html>"
    )
    assert main([str(contract), str(html), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["issues"][0]["code"] == "title_presence"


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


def test_nested_jsonld_entities_satisfy_declared_types():
    structured_data = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [{
            "@type": "Question",
            "acceptedAnswer": {"@type": "Answer", "text": "Available"},
        }],
    }
    html = (
        '<html><head><script type="application/ld+json">'
        + json.dumps(structured_data)
        + '</script></head><body></body></html>'
    )
    contract = {"url": URL, "jsonld_types": ["FAQPage", "Question", "Answer"]}
    assert verify_contract(contract, html)["status"] == "pass"


def test_jsonld_context_type_mapping_is_not_an_entity():
    structured_data = {
        "@context": {"author": {"@type": "Person"}},
        "@type": "Article",
    }
    html = (
        '<html><head><script type="application/ld+json">'
        + json.dumps(structured_data)
        + '</script></head><body></body></html>'
    )
    result = verify_contract({"url": URL, "jsonld_types": ["Person"]}, html)
    assert {issue["code"] for issue in result["issues"]} == {"jsonld_type_missing"}


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


def test_body_only_head_metadata_cannot_satisfy_contract():
    html = '''<html><head></head><body>
    <title>Not a head title</title>
    <link rel="canonical" href="/en/">
    <link rel="alternate" hreflang="en" href="/en/">
    <link rel="alternate" hreflang="sl" href="/sl/">
    <meta name="robots" content="noindex">
    <h1>Visible heading</h1>
    <script type="application/ld+json">{"@type":"WebPage"}</script>
    </body></html>'''
    result = verify_contract(
        {**CONTRACT, "indexable": False}, html,
        include_hreflang=True, include_canonical=True,
    )
    assert {issue["code"] for issue in result["issues"]} == {
        "title_presence", "canonical", "indexability_meta", "hreflang:en", "hreflang:sl",
    }
    assert result["observed_canonical"] == []
    assert result["observed_hreflang"] == {}


def test_body_metadata_does_not_poison_valid_head_or_body_jsonld():
    html = HTML.replace(
        '<script type="application/ld+json">{"@graph":[{"@type":"WebPage"}]}</script>',
        "",
    ).replace(
        "<h1>Welcome</h1>",
        '''<title>Duplicate body title</title>
        <link rel="canonical" href="/incorrect/">
        <link rel="alternate" hreflang="en" href="/incorrect/">
        <meta name="robots" content="noindex">
        <h1>Welcome</h1>
        <script type="application/ld+json">{"@type":"WebPage"}</script>''',
    )
    result = verify_contract(CONTRACT, html, include_hreflang=True, include_canonical=True)
    assert result["status"] == "pass"
    assert result["observed_canonical"] == [URL]
    assert result["observed_hreflang"] == {
        "en": [URL], "sl": ["https://example.com/sl/"],
    }


def test_document_base_href_resolves_relative_canonical_and_hreflang():
    html = '''<html><head><title>Page</title>
    <base href="https://assets.example.test/pages/">
    <link rel="canonical" href="article.html">
    <link rel="alternate" hreflang="en" href="article.html">
    </head><body></body></html>'''
    target = "https://assets.example.test/pages/article.html"
    result = verify_contract(
        {"url": URL, "canonical": target, "hreflang": {"en": target}},
        html, include_canonical=True, include_hreflang=True,
    )
    assert result["status"] == "pass"
    assert result["observed_canonical"] == [target]
    assert result["observed_hreflang"] == {"en": [target]}


def test_document_base_prevents_false_pass_with_relative_canonical():
    html = '''<html><head><base href="https://other.example.test/subdir/">
    <link rel="canonical" href="article.html"></head><body></body></html>'''
    result = verify_contract(
        {"url": URL, "canonical": "https://example.com/en/article.html"}, html,
    )
    assert result["status"] == "fail"
    assert result["issues"][0]["actual"] == ["https://other.example.test/subdir/article.html"]


def test_first_head_base_wins_and_body_template_bases_are_ignored():
    html = '''<html><head><template><base href="https://wrong.example.test/"></template>
    <base target="_blank"><base href="/nested/"><base href="/ignored/">
    <link rel="canonical" href="child.html"></head><body>
    <base href="/wrong/"></body></html>'''
    result = verify_contract(
        {"url": URL, "canonical": "https://example.com/nested/child.html"}, html,
        include_canonical=True,
    )
    assert result["status"] == "pass"
    assert result["observed_canonical"] == ["https://example.com/nested/child.html"]


def test_invalid_document_base_fails_closed():
    html = '''<html><head><base href="javascript:alert(1)">
    <link rel="canonical" href="/en/"></head><body></body></html>'''
    result = verify_contract({"url": URL, "canonical": URL}, html)
    assert {issue["code"] for issue in result["issues"]} == {"document_base_invalid"}


def test_duplicate_html_attributes_use_first_value_for_canonical_and_rel():
    # Browsers ignore duplicate attributes after the first; Python dict(attrs) does not.
    wrong_first = '''<html><head><link rel="canonical" href="/wrong/" href="/en/">
    </head><body></body></html>'''
    result = verify_contract({"url": URL, "canonical": URL}, wrong_first)
    assert {issue["code"] for issue in result["issues"]} == {"canonical"}
    assert result["issues"][0]["actual"] == ["https://example.com/wrong/"]

    correct_first = '''<html><head><link rel="canonical" rel="alternate"
    href="/en/" href="/wrong/"></head><body></body></html>'''
    result = verify_contract({"url": URL, "canonical": URL}, correct_first, include_canonical=True)
    assert result["status"] == "pass"
    assert result["observed_canonical"] == [URL]


def test_duplicate_hreflang_and_robots_values_do_not_hide_first_value():
    html = '''<html><head><link rel="alternate" hreflang="fr" hreflang="sl" href="/fr/">
    <meta name="robots" content="noindex" content="index"></head><body></body></html>'''
    spec = {"url": URL, "hreflang": {"sl": "https://example.com/fr/"}, "indexable": True}
    result = verify_contract(spec, html, include_hreflang=True)
    assert {issue["code"] for issue in result["issues"]} == {"hreflang:sl", "indexability_meta"}
    assert result["observed_hreflang"] == {"fr": ["https://example.com/fr/"]}


def test_duplicate_base_href_uses_first_value():
    html = '''<html><head><base href="/original/" href="/ignored/">
    <link rel="canonical" href="child.html"></head><body></body></html>'''
    result = verify_contract(
        {"url": URL, "canonical": "https://example.com/original/child.html"}, html,
    )
    assert result["status"] == "pass"


def test_blank_canonical_href_cannot_pass_as_self_canonical():
    html = '<html><head><link rel="canonical" href=" "></head><body></body></html>'
    result = verify_contract({"url": URL, "canonical": URL}, html)
    assert result["status"] == "fail"
    assert result["issues"] == [{"code": "canonical", "expected": [URL], "actual": [""]}]


def test_blank_hreflang_href_cannot_pass_as_self_alternate():
    html = '<html><head><link rel="alternate" hreflang="en" href=""></head><body></body></html>'
    result = verify_contract({"url": URL, "hreflang": {"en": URL}}, html)
    assert result["status"] == "fail"
    assert result["issues"] == [{"code": "hreflang:en", "expected": [URL], "actual": [""]}]


@pytest.mark.parametrize("property_name", ["canonical", "hreflang"])
def test_malformed_link_url_is_a_contract_failure_not_an_input_error(property_name):
    malformed = "https://[broken"
    if property_name == "canonical":
        html = f'<html><head><link rel="canonical" href="{malformed}"></head></html>'
        spec = {"url": URL, "canonical": URL}
        expected_code = "canonical"
    else:
        html = (
            '<html><head><link rel="alternate" hreflang="en" '
            f'href="{malformed}"></head></html>'
        )
        spec = {"url": URL, "hreflang": {"en": URL}}
        expected_code = "hreflang:en"
    result = verify_contract(spec, html)
    assert result["status"] == "fail"
    assert result["issues"] == [{
        "code": expected_code, "expected": [URL], "actual": [malformed],
    }]


@pytest.mark.parametrize("property_name", ["canonical", "hreflang"])
def test_broken_link_cannot_pass_when_contract_repeats_its_invalid_url(property_name):
    malformed = "javascript:alert(1)"
    if property_name == "canonical":
        spec = {"url": URL, "canonical": malformed}
        link = f'<link rel="canonical" href="{malformed}">'
    else:
        spec = {"url": URL, "hreflang": {"en": malformed}}
        link = f'<link rel="alternate" hreflang="en" href="{malformed}">'
    report = verify_contract(spec, f"<html><head>{link}</head><body></body></html>")
    assert report["status"] == "fail"
    assert report["issues"][0]["actual"] == [malformed]
