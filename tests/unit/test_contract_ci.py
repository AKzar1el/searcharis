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


@pytest.mark.parametrize("omit_html_and_body", [False, True])
def test_valid_optional_head_start_tag_keeps_seo_assertions(omit_html_and_body):
    # HTML5 permits the head start tag to be omitted; minifiers may also omit
    # html/body tags if their surrounding whitespace is removed.
    if omit_html_and_body:
        html = (HTML.replace("<html><head>", "")
                .replace("</head><body>", "")
                .replace("</body></html>", ""))
    else:
        html = HTML.replace("<head>", "")
    result = verify_contract(CONTRACT, html, include_hreflang=True, include_canonical=True)
    assert result["status"] == "pass", result["issues"]
    assert result["observed_canonical"] == [URL]
    assert result["observed_hreflang"]["en"] == [URL]


def test_implicit_head_closes_at_first_body_content_without_body_start_tag():
    html = ("<!doctype html><html><title>Good</title><h1>Heading</h1>"
            '<link rel="canonical" href="/en/">')
    result = verify_contract({"url": URL, "title_present": True, "canonical": URL}, html)
    assert {issue["code"] for issue in result["issues"]} == {"canonical"}


def test_body_content_before_implicit_head_does_not_create_head_metadata():
    html = "<!doctype html><html><div>Content</div><title>Not in head</title>"
    result = verify_contract({"url": URL, "title_present": True}, html)
    assert "title_presence" in {issue["code"] for issue in result["issues"]}


@pytest.mark.parametrize("opening", ["<!doctype html><html>", "<!doctype html>"])
def test_text_before_omitted_head_keeps_later_metadata_in_body(opening):
    html = (opening + 'Visible content<title>Body title</title>'
            '<link rel="canonical" href="/en/">')
    result = verify_contract({"url": URL, "title_present": True, "canonical": URL}, html)
    assert {issue["code"] for issue in result["issues"]} == {
        "title_presence", "canonical",
    }


def test_ascii_whitespace_before_omitted_head_still_allows_head_metadata():
    html = ('<!doctype html><html> \n\t<title>Head title</title>'
            '<link rel="canonical" href="/en/">')
    assert verify_contract({"url": URL, "title_present": True, "canonical": URL}, html)[
        "status"
    ] == "pass"


@pytest.mark.parametrize("head_start", ["<head>", ""])
def test_visible_text_in_head_closes_metadata_scope(head_start):
    # Non-whitespace text makes the browser implicitly close the head. The
    # following link is not a head canonical, even without a <body> tag.
    html = ('<!doctype html><html>' + head_start + '<title>Real title</title>'
            'Visible content<link rel="canonical" href="/en/">')
    result = verify_contract({"url": URL, "title_present": True, "canonical": URL}, html)
    assert {issue["code"] for issue in result["issues"]} == {"canonical"}


def test_visible_text_in_explicit_head_cannot_supply_later_title():
    html = '<!doctype html><html><head>Visible content<title>Body title</title></head>'
    result = verify_contract({"url": URL, "title_present": True}, html)
    assert {issue["code"] for issue in result["issues"]} == {"title_presence"}


def test_visible_text_after_closed_head_cannot_reopen_head():
    html = ('<!doctype html><html><head><title>First</title></head>'
            'Body text<head><link rel="canonical" href="/en/">')
    result = verify_contract({"url": URL, "canonical": URL}, html)
    assert {issue["code"] for issue in result["issues"]} == {"canonical"}


def test_script_and_style_contents_do_not_implicitly_close_head():
    html = ('<!doctype html><html><head><style>body { display: block }</style>'
            '<script>const text = "Visible text";</script>'
            '<title>Head title</title><link rel="canonical" href="/en/"></head>')
    assert verify_contract({"url": URL, "title_present": True, "canonical": URL}, html)[
        "status"
    ] == "pass"


def test_cli_rejects_head_canonical_after_visible_text(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "canonical": URL}), encoding="utf-8")
    artifact.write_text('<!doctype html><title>Head title</title>Visible text'
                        '<link rel="canonical" href="/en/">', encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 1
    assert {item["code"] for item in json.loads(capsys.readouterr().out)["issues"]} == {
        "canonical",
    }


def test_minified_implicit_head_passes_single_page_cli(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "title_present": True, "canonical": URL}))
    artifact.write_text('<!doctype html><title>Minified</title>'
                        '<link rel="canonical" href="/en/"><h1>Content</h1>')
    assert main([str(contract), str(artifact), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


def test_cli_does_not_accept_body_metadata_after_leading_text(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "title_present": True, "canonical": URL}))
    artifact.write_text('<!doctype html>Visible text<title>Body title</title>'
                        '<link rel="canonical" href="/en/">')
    assert main([str(contract), str(artifact), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert {issue["code"] for issue in report["issues"]} == {
        "title_presence", "canonical",
    }


def test_textarea_example_markup_cannot_satisfy_headings_or_jsonld():
    html = (
        '<html><head><title>Examples</title></head><body><textarea>'
        '<h1>Example only</h1>'
        '<script type="application/ld+json">{"@type":"FAQPage"}</script>'
        '</textarea></body></html>'
    )
    result = verify_contract({"url": URL, "h1_min": 1, "jsonld_types": ["FAQPage"]}, html)
    assert {issue["code"] for issue in result["issues"]} == {
        "h1_min", "jsonld_type_missing",
    }


def test_textarea_example_does_not_hide_real_following_heading():
    html = (
        '<html><head></head><body><textarea>'
        '<template><h1>Not a heading</h1></template>'
        '<script>Literal example with no closing script tag'
        '</textarea><h1>Real heading</h1></body></html>'
    )
    assert verify_contract({"url": URL, "h1_min": 1, "h1_max": 1}, html)[
        "status"
    ] == "pass"


def test_textarea_example_cannot_rescue_heading_cli(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "h1_min": 1}), encoding="utf-8")
    artifact.write_text(
        '<html><head></head><body><textarea><h1>Example only</h1></textarea></body></html>',
        encoding="utf-8",
    )
    assert main([str(contract), str(artifact), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert {issue["code"] for issue in report["issues"]} == {"h1_min"}


@pytest.mark.parametrize("rawtext_tag", ["iframe", "xmp", "noembed", "noframes"])
def test_rawtext_embedded_examples_cannot_satisfy_heading_or_jsonld(rawtext_tag):
    html = (
        '<html><head><title>Example</title></head><body>'
        f'<{rawtext_tag}><h1>Not a real heading</h1>'
        '<script type="application/ld+json">{"@type":"FAQPage"}</script>'
        f'</{rawtext_tag}></body></html>'
    )
    result = verify_contract({"url": URL, "h1_min": 1, "jsonld_types": ["FAQPage"]}, html)
    assert {issue["code"] for issue in result["issues"]} == {
        "h1_min", "jsonld_type_missing",
    }


def test_rawtext_example_does_not_hide_following_heading():
    html = (
        '<html><head></head><body><iframe>'
        '<script>Literal example with no closing script tag'
        '</iframe><h1>Real heading</h1></body></html>'
    )
    assert verify_contract({"url": URL, "h1_min": 1, "h1_max": 1}, html)[
        "status"
    ] == "pass"


def test_rawtext_example_cannot_rescue_heading_cli(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "h1_min": 1}), encoding="utf-8")
    artifact.write_text(
        '<html><head></head><body><iframe><h1>Not a heading</h1></iframe></body></html>',
        encoding="utf-8",
    )
    assert main([str(contract), str(artifact), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert {issue["code"] for issue in report["issues"]} == {"h1_min"}


def test_plaintext_examples_cannot_supply_heading_or_jsonld():
    html = (
        '<html><head><title>Example</title></head><body><plaintext>'
        '<h1>Text example only</h1>'
        '<script type="application/ld+json">{"@type":"FAQPage"}</script>'
        '</body></html>'
    )
    result = verify_contract({"url": URL, "h1_min": 1, "jsonld_types": ["FAQPage"]}, html)
    assert {issue["code"] for issue in result["issues"]} == {
        "h1_min", "jsonld_type_missing",
    }


def test_plaintext_cannot_be_closed_by_literal_end_tag():
    html = (
        '<html><head></head><body><plaintext>Example'
        '</plaintext><h1>Still text, not an element</h1></body></html>'
    )
    result = verify_contract({"url": URL, "h1_min": 1}, html)
    assert {issue["code"] for issue in result["issues"]} == {"h1_min"}


def test_plaintext_inside_template_still_consumes_remaining_html():
    html = (
        '<html><head></head><body><template><plaintext>Example'
        '</template><h1>Still plain text</h1></body></html>'
    )
    result = verify_contract({"url": URL, "h1_min": 1}, html)
    assert {issue["code"] for issue in result["issues"]} == {"h1_min"}


def test_plaintext_does_not_hide_real_earlier_heading():
    html = '<html><head></head><body><h1>Real heading</h1><plaintext><h1>Text</h1>'
    result = verify_contract({"url": URL, "h1_min": 1, "h1_max": 1}, html)
    assert result["status"] == "pass", result["issues"]


def test_plaintext_example_cannot_rescue_heading_cli(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "h1_min": 1}), encoding="utf-8")
    artifact.write_text(
        '<html><head></head><body><plaintext><h1>Only text</h1></body></html>',
        encoding="utf-8",
    )
    assert main([str(contract), str(artifact), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert {issue["code"] for issue in report["issues"]} == {"h1_min"}


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


def test_title_rcdata_examples_cannot_satisfy_h1_or_jsonld():
    html = (
        '<html><head><title>Literal <h1>heading</h1> and '
        '<script type="application/ld+json">{"@type":"FAQPage"}</script>'
        '</title></head><body></body></html>'
    )
    report = verify_contract({"url": URL, "title_present": True, "h1_min": 1,
                              "jsonld_types": ["FAQPage"]}, html)
    assert {issue["code"] for issue in report["issues"]} == {
        "h1_min", "jsonld_type_missing",
    }


def test_duplicate_or_body_title_rcdata_examples_cannot_supply_h1():
    html = (
        '<html><head><title>First</title><title>Example <h1>fake</h1></title>'
        '</head><body><title>More <h1>fake</h1></title></body></html>'
    )
    report = verify_contract({"url": URL, "h1_min": 1}, html)
    assert {issue["code"] for issue in report["issues"]} == {"h1_min"}


def test_title_rcdata_does_not_swallow_following_real_h1_or_head_links():
    html = (
        '<html><head><title>Literal <script>example without closing script'
        '</title><link rel="canonical" href="/en/"></head>'
        '<body><h1>Real heading</h1></body></html>'
    )
    report = verify_contract({"url": URL, "canonical": URL, "h1_min": 1,
                              "h1_max": 1}, html)
    assert report["status"] == "pass", report["issues"]


def test_cli_title_rcdata_example_does_not_pass_h1(tmp_path, capsys):
    contract = tmp_path / "expected.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "h1_min": 1}), encoding="utf-8")
    artifact.write_text(
        '<html><head><title>Example <h1>fake heading</h1></title></head>'
        '<body></body></html>', encoding="utf-8",
    )
    assert main([str(contract), str(artifact), "--json"]) == 1
    assert {issue["code"] for issue in json.loads(capsys.readouterr().out)["issues"]} == {
        "h1_min",
    }


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


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonstandard_jsonld_numeric_constant_cannot_pass_schema_assertion(constant):
    # Python json.loads accepts these identifiers, but none are legal JSON values.
    invalid = f'{{"@type":"WebPage","rank":{constant}}}'
    html = HTML.replace('{"@graph":[{"@type":"WebPage"}]}', invalid)
    result = verify_contract(CONTRACT, html)
    assert {"jsonld_invalid", "jsonld_type_missing"} <= {
        item["code"] for item in result["issues"]
    }


def test_nonstandard_jsonld_number_fails_single_page_cli(tmp_path, capsys):
    contract_path = tmp_path / "expected.json"
    html_path = tmp_path / "index.html"
    contract_path.write_text(json.dumps({"url": URL, "jsonld_types": ["WebPage"]}), encoding="utf-8")
    html_path.write_text(
        '<html><head><script type="application/ld+json">'
        '{"@type":"WebPage","rank":NaN}</script></head><body></body></html>',
        encoding="utf-8",
    )
    assert main([str(contract_path), str(html_path), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "fail"
    assert {item["code"] for item in report["issues"]} == {
        "jsonld_invalid", "jsonld_type_missing",
    }


@pytest.mark.parametrize("structured_data", [
    '{"@type":"Thing","@type":"WebPage"}',
    '{"@type":"WebPage","name":"First","name":"Second"}',
    '{"@type":"WebPage","author":{"@type":"Person","@type":"Organization"}}',
])
def test_duplicate_jsonld_object_keys_cannot_satisfy_schema_assertions(structured_data):
    # Python's default JSON decoder silently takes the last duplicate key.
    html = HTML.replace('{"@graph":[{"@type":"WebPage"}]}', structured_data)
    result = verify_contract(CONTRACT, html)
    assert {item["code"] for item in result["issues"]} == {
        "jsonld_invalid", "jsonld_type_missing",
    }


def test_duplicate_jsonld_keys_fail_single_page_cli(tmp_path, capsys):
    contract_path = tmp_path / "expected.json"
    html_path = tmp_path / "index.html"
    contract_path.write_text(json.dumps({"url": URL, "jsonld_types": ["WebPage"]}), encoding="utf-8")
    html_path.write_text(
        '<html><head><script type="application/ld+json">'
        '{"@type":"Other","@type":"WebPage"}</script></head><body></body></html>',
        encoding="utf-8",
    )
    assert main([str(contract_path), str(html_path), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert {item["code"] for item in report["issues"]} == {
        "jsonld_invalid", "jsonld_type_missing",
    }


def test_valid_jsonld_numeric_values_remain_accepted():
    html = HTML.replace(
        '{"@graph":[{"@type":"WebPage"}]}',
        '{"@type":"WebPage","rating":1.5,"position":-12,"fraction":2e-3}',
    )
    assert verify_contract(CONTRACT, html)["status"] == "pass"


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


@pytest.mark.parametrize("types", [[], ["   "], ["WebPage", " \t "]])
def test_jsonld_contract_must_assert_at_least_one_nonblank_type(types):
    # A no-op type list must not make a build without structured data pass CI.
    html = '<html><head><title>No schema</title></head><body></body></html>'
    with pytest.raises(ValueError, match="jsonld_types"):
        verify_contract({"url": URL, "jsonld_types": types}, html)


def test_cli_rejects_noop_jsonld_types_as_invalid_contract(tmp_path, capsys):
    contract = tmp_path / "expected.json"
    html = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "jsonld_types": []}), encoding="utf-8")
    html.write_text('<html><head><title>No schema</title></head><body></body></html>', encoding="utf-8")

    assert main([str(contract), str(html), "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "error"
    assert "jsonld_types" in report["error"]


def test_unsupported_check_is_rejected():
    with pytest.raises(ValueError, match="Unsupported"):
        verify_contract({"url": URL, "magic": True}, HTML)


def test_invalid_bounds_and_url_rejected():
    for contract in ({"url": URL, "h1_min": -1}, {"url": URL, "h1_min": 2, "h1_max": 1}, {"url": "file:///etc/passwd", "title_present": True}):
        with pytest.raises(ValueError):
            verify_contract(contract, HTML)


@pytest.mark.parametrize("bad_url", [
    "https://example.com:99999/en/",
    "https://example.com:invalid/en/",
    "https://example.com/path with space",
])
def test_invalid_contract_url_cannot_pass_title_only_check(bad_url):
    with pytest.raises(ValueError, match="HTTP\\(S\\) URL"):
        verify_contract({"url": bad_url, "title_present": True}, HTML)


def test_non_default_valid_contract_port_is_accepted():
    result = verify_contract({"url": "https://example.com:8443/en/", "title_present": True}, HTML)
    assert result["status"] == "pass"


@pytest.mark.parametrize("bad_url", [
    "/en/",
    "javascript:alert(1)",
    "https://example.com:99999/en/",
    "https://example.com/en/with space",
])
@pytest.mark.parametrize("field", ["canonical", "hreflang"])
def test_invalid_expected_seo_link_url_is_contract_input_error(field, bad_url):
    value = bad_url if field == "canonical" else {"en": bad_url}
    with pytest.raises(ValueError, match="absolute HTTP\\(S\\) URL"):
        verify_contract({"url": URL, field: value}, HTML)


def test_valid_nondefault_port_expected_seo_urls_remain_supported():
    port_url = "https://example.com:8443/en/"
    html = HTML.replace('href="/en/"', f'href="{port_url}"')
    spec = {"url": URL, "canonical": port_url, "hreflang": {"en": port_url}}
    assert verify_contract(spec, html)["status"] == "pass"


def test_single_page_cli_rejects_invalid_expected_canonical(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    html = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "canonical": "javascript:alert(1)"}))
    html.write_text(HTML)

    assert main([str(contract), str(html), "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "error"
    assert "canonical" in report["error"]


def test_single_page_cli_rejects_invalid_contract_url(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    html = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": "https://example.com:99999/en/", "title_present": True}))
    html.write_text(HTML)

    assert main([str(contract), str(html), "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "error"
    assert "HTTP(S) URL" in report["error"]


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
    assert "Invalid input:" in capsys.readouterr().err


@pytest.mark.parametrize("failure", ["malformed_json", "invalid_contract", "missing_html"])
def test_json_cli_input_errors_are_machine_readable(tmp_path, capsys, failure):
    contract = tmp_path / "expected.json"
    artifact = tmp_path / "page.html"
    contracts = {
        "malformed_json": "{not json",
        "invalid_contract": json.dumps({"url": URL, "jsonld_types": []}),
        "missing_html": json.dumps({"url": URL, "title_present": True}),
    }
    contract.write_text(contracts[failure], encoding="utf-8")
    if failure != "missing_html":
        artifact.write_text(HTML, encoding="utf-8")

    assert main([str(contract), str(artifact), "--json"]) == 2
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["status"] == "error"
    assert isinstance(report["error"], str) and report["error"]
    assert output.err == ""


@pytest.mark.parametrize("contract_text", [
    ('{"url":"https://example.com/en/",'
     '"canonical":"https://example.com/wrong/",'
     '"canonical":"https://example.com/en/"}'),
    ('{"url":"https://example.com/en/",'
     '"hreflang":{"en":"https://example.com/wrong/",'
     '"en":"https://example.com/en/"}}'),
])
def test_single_page_cli_rejects_duplicate_contract_json_keys(tmp_path, capsys, contract_text):
    contract = tmp_path / "expected.json"
    html = tmp_path / "index.html"
    contract.write_text(contract_text, encoding="utf-8")
    html.write_text(HTML, encoding="utf-8")

    assert main([str(contract), str(html), "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "error"
    assert "Duplicate contract JSON key" in report["error"]


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
def test_invalid_observed_url_cannot_be_laundered_by_same_invalid_expectation(property_name):
    malformed = "javascript:alert(1)"
    if property_name == "canonical":
        spec = {"url": URL, "canonical": malformed}
        link = f'<link rel="canonical" href="{malformed}">'
    else:
        spec = {"url": URL, "hreflang": {"en": malformed}}
        link = f'<link rel="alternate" hreflang="en" href="{malformed}">'
    with pytest.raises(ValueError, match="HTTP\\(S\\) URL"):
        verify_contract(spec, f"<html><head>{link}</head><body></body></html>")
