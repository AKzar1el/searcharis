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


@pytest.mark.parametrize("expected,markup", [
    ("Proper page", HTML),
    ("Proper page", HTML.replace("Proper page", " Proper  \n\tpage ")),
    ("Tea & Coffee", HTML.replace("Proper page", "Tea &amp; Coffee")),
    ("Proper page", HTML.replace("<title>Proper page</title>",
                                 "<template><title>Fake title</title></template>"
                                 "<title>Proper page</title>")),
])
def test_title_equals_accepts_normalized_first_active_head_title(expected, markup):
    result = verify_contract({"url": URL, "title_equals": expected}, markup)
    assert result["status"] == "pass", result["issues"]


@pytest.mark.parametrize("markup,actual", [
    (HTML.replace("Proper page", "Wrong title"), "Wrong title"),
    (HTML.replace("<title>Proper page</title>", ""), ""),
    (HTML.replace("<title>Proper page</title>",
                  "<title>Wrong title</title><title>Proper page</title>"), "Wrong title"),
    (HTML.replace("<title>Proper page</title>", "")
         .replace("<h1>Welcome</h1>", "<title>Proper page</title><h1>Welcome</h1>"), ""),
])
def test_title_equals_detects_title_regressions_even_when_presence_passes(markup, actual):
    result = verify_contract({"url": URL, "title_present": bool(actual),
                              "title_equals": "Proper page"}, markup)
    assert {issue["code"] for issue in result["issues"]} == {"title_equals"}
    assert result["issues"][0] == {
        "code": "title_equals", "expected": "Proper page", "actual": actual,
    }


@pytest.mark.parametrize("value", [None, False, 1, "", "  \t\n", [], {}])
def test_title_equals_rejects_missing_or_invalid_expected_text(value):
    with pytest.raises(ValueError, match="title_equals"):
        verify_contract({"url": URL, "title_equals": value}, HTML)


def test_title_equals_cli_reports_mismatch(tmp_path, capsys):
    contract_path = tmp_path / "expected.json"
    html_path = tmp_path / "index.html"
    contract_path.write_text(json.dumps({"url": URL, "title_equals": "Expected page"}),
                             encoding="utf-8")
    html_path.write_text(HTML, encoding="utf-8")
    assert main([str(contract_path), str(html_path), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"] == [{
        "code": "title_equals", "expected": "Expected page", "actual": "Proper page",
    }]


@pytest.mark.parametrize("expected,markup", [
    ("Welcome", HTML),
    ("Welcome", HTML.replace("<h1>Welcome</h1>", "<h1>  Welcome  </h1>")),
    ("Tea & Coffee", HTML.replace("<h1>Welcome</h1>", "<h1>Tea &amp; Coffee</h1>")),
    ("Welcome back", HTML.replace("<h1>Welcome</h1>", "<h1>Welcome <em>back</em></h1>")),
    ("Welcome", HTML.replace("<h1>Welcome</h1>",
                             "<template><h1>Ghost</h1></template>"
                             "<noscript><h1>Ghost</h1></noscript><h1>Welcome</h1>")),
    ("Welcome", HTML.replace("<h1>Welcome</h1>",
                             "<h1>Wel<script>ignored()</script>come</h1>")),
    ("Welcome", HTML.replace("<h1>Welcome</h1>", "<h1/>Welcome</h1>")),
    ("Welcome", '<!doctype html><h1>Welcome</h1>'),
    ("Welcome", '<!doctype html><h1>Welcome<h2>Section</h2>'),
    ("Welcome", '<!doctype html><h1>Welcome<h3>Section</h3><p>Body text</p>'),
    ("Welcome", '<!doctype html><h1>Welcome<h6>Section</h6><h1>Next heading</h1>'),
])
def test_h1_text_equals_accepts_first_visible_heading_text(expected, markup):
    result = verify_contract({"url": URL, "h1_text_equals": expected}, markup)
    assert result["status"] == "pass", result["issues"]


@pytest.mark.parametrize("markup,actual", [
    (HTML.replace("Welcome", "Wrong heading"), "Wrong heading"),
    (HTML.replace("<h1>Welcome</h1>", ""), ""),
    (HTML.replace("<h1>Welcome</h1>", "<h1></h1><h1>Welcome</h1>"), ""),
    (HTML.replace("<h1>Welcome</h1>", "<h1>Wrong heading</h1><h1>Welcome</h1>"),
     "Wrong heading"),
    (HTML.replace("<h1>Welcome</h1>", "<h1>Welcome <span>elsewhere</span></h1>"),
     "Welcome elsewhere"),
    (HTML.replace("<h1>Welcome</h1>", "<template><h1>Welcome</h1></template>"), ""),
])
def test_h1_text_equals_flags_missing_blank_or_changed_first_h1(markup, actual):
    result = verify_contract({"url": URL, "h1_text_equals": "Welcome"}, markup)
    assert result["issues"] == [{
        "code": "h1_text_equals", "expected": "Welcome", "actual": actual,
    }]


@pytest.mark.parametrize("value", [None, False, 1, "", "  \t\n", [], {}])
def test_h1_text_equals_rejects_invalid_expected_text(value):
    with pytest.raises(ValueError, match="h1_text_equals"):
        verify_contract({"url": URL, "h1_text_equals": value}, HTML)


def test_h1_text_equals_cli_json_reports_mismatch(tmp_path, capsys):
    contract_path = tmp_path / "expected.json"
    html_path = tmp_path / "index.html"
    contract_path.write_text(json.dumps({"url": URL, "h1_text_equals": "Expected heading"}),
                             encoding="utf-8")
    html_path.write_text(HTML, encoding="utf-8")
    assert main([str(contract_path), str(html_path), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"] == [{
        "code": "h1_text_equals", "expected": "Expected heading", "actual": "Welcome",
    }]


def test_h1_text_equals_cli_ignores_following_implicit_heading(tmp_path, capsys):
    contract_path = tmp_path / "expected.json"
    html_path = tmp_path / "index.html"
    contract_path.write_text(json.dumps({"url": URL, "h1_text_equals": "Welcome"}),
                             encoding="utf-8")
    html_path.write_text("<!doctype html><h1>Welcome<h2>Subheading</h2>", encoding="utf-8")
    assert main([str(contract_path), str(html_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["issues"] == []


@pytest.mark.parametrize("viewport", [
    '<meta name="viewport" content="width=device-width">',
    '<meta NAME="VIEWPORT" content="initial-scale=1, width = DEVICE-WIDTH">',
    '<meta name="viewport" content="width=device-width, viewport-fit=cover">',
])
def test_device_width_viewport_contract_accepts_active_head_metadata(viewport):
    html = HTML.replace("</head>", viewport + "</head>")
    result = verify_contract({"url": URL, "viewport_device_width": True}, html)
    assert result["status"] == "pass", result["issues"]


@pytest.mark.parametrize("viewport", [
    "",
    '<meta name="viewport">',
    '<meta name="viewport" content="initial-scale=1">',
    '<meta name="viewport" content="width=980">',
    '<meta name="viewport" content="data-width=device-width">',
    '<meta name="viewport" content="width=device-width-ish">',
    '<meta name="viewport" content="width=980,width=device-width">',
])
def test_device_width_viewport_contract_flags_missing_or_wrong_width(viewport):
    html = HTML.replace("</head>", viewport + "</head>")
    result = verify_contract({"url": URL, "viewport_device_width": True}, html)
    assert {issue["code"] for issue in result["issues"]} == {"viewport_device_width"}


def test_device_width_viewport_first_head_tag_wins_and_body_cannot_rescue():
    html = (HTML.replace("</head>", '<meta name="viewport" content="width=980">'
                         '<meta name="viewport" content="width=device-width"></head>')
            .replace("<h1>Welcome</h1>", '<meta name="viewport" content="width=device-width">'
                     '<h1>Welcome</h1>'))
    result = verify_contract({"url": URL, "viewport_device_width": True}, html)
    assert {issue["code"] for issue in result["issues"]} == {"viewport_device_width"}


def test_device_width_viewport_ignores_inert_template_and_body_only_metadata():
    html = (HTML.replace("</head>", '<template><meta name="viewport" '
                         'content="width=device-width"></template></head>')
            .replace("<h1>Welcome</h1>", '<meta name="viewport" content="width=device-width">'
                     '<h1>Welcome</h1>'))
    result = verify_contract({"url": URL, "viewport_device_width": True}, html)
    assert {issue["code"] for issue in result["issues"]} == {"viewport_device_width"}


def test_device_width_viewport_in_implicit_head_and_opt_in_false():
    html = '<!doctype html><meta name="viewport" content="width=device-width"><h1>Page</h1>'
    assert verify_contract({"url": URL, "viewport_device_width": True}, html)["status"] == "pass"
    assert verify_contract({"url": URL, "viewport_device_width": False}, HTML)["status"] == "pass"


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_device_width_viewport_contract_rejects_nonboolean_config(value):
    with pytest.raises(ValueError, match="viewport_device_width"):
        verify_contract({"url": URL, "viewport_device_width": value}, HTML)


def test_device_width_viewport_cli_returns_json_finding(tmp_path, capsys):
    contract_path = tmp_path / "expected.json"
    html_path = tmp_path / "index.html"
    contract_path.write_text(json.dumps({"url": URL, "viewport_device_width": True}), encoding="utf-8")
    html_path.write_text(HTML, encoding="utf-8")
    assert main([str(contract_path), str(html_path), "--json"]) == 1
    assert {issue["code"] for issue in json.loads(capsys.readouterr().out)["issues"]} == {
        "viewport_device_width",
    }


@pytest.mark.parametrize("tag,content", [
    ('<meta http-equiv="refresh" content="0; url=/new/">', "0; url=/new/"),
    ('<meta HTTP-EQUIV="Refresh" content="5; URL=https://example.com/new">',
     "5; URL=https://example.com/new"),
    ('<meta http-equiv=" refresh " content="60">', "60"),
    ('<meta http-equiv="refresh">', ""),
    ('<meta http-equiv="refresh" content="">', ""),
])
def test_meta_refresh_presence_catches_active_head_directives(tag, content):
    html = HTML.replace("</head>", tag + "</head>")
    assert verify_contract({"url": URL, "meta_refresh_present": True}, html)["status"] == "pass"
    report = verify_contract({"url": URL, "meta_refresh_present": False}, html)
    assert report["issues"] == [{
        "code": "meta_refresh_presence", "expected": False, "actual": [content],
    }]


@pytest.mark.parametrize("markup", [
    '<meta name="refresh" content="0; url=/new/">',
    '<meta http-equiv="x-refresh" content="0; url=/new/">',
    '<template><meta http-equiv="refresh" content="0; url=/new/"></template>',
    '<noscript><meta http-equiv="refresh" content="0; url=/new/"></noscript>',
    '<title>Example <meta http-equiv="refresh" content="0"></title>',
    '<meta http-equiv="content-type" http-equiv="refresh" content="0">',
])
def test_meta_refresh_presence_ignores_non_directives_and_inert_head_markup(markup):
    html = HTML.replace("</head>", markup + "</head>")
    assert verify_contract({"url": URL, "meta_refresh_present": False}, html)["status"] == "pass"


def test_meta_refresh_presence_respects_implicit_head_and_excludes_body():
    implicit = '<!doctype html><meta http-equiv="refresh" content="0; url=/new"><h1>Page</h1>'
    body_only = HTML.replace("<h1>Welcome</h1>",
                             '<meta http-equiv="refresh" content="0; url=/new"><h1>Welcome</h1>')
    assert verify_contract({"url": URL, "meta_refresh_present": True}, implicit)["status"] == "pass"
    assert verify_contract({"url": URL, "meta_refresh_present": False}, body_only)["status"] == "pass"
    assert verify_contract({"url": URL, "meta_refresh_present": True}, body_only)["issues"] == [{
        "code": "meta_refresh_presence", "expected": True, "actual": [],
    }]


def test_meta_refresh_presence_reports_multiple_head_directives_in_order():
    html = HTML.replace("</head>",
                        '<meta http-equiv="refresh" content="0; url=/one">'
                        '<meta http-equiv="refresh" content="10; url=/two"></head>')
    assert verify_contract({"url": URL, "meta_refresh_present": False}, html)["issues"] == [{
        "code": "meta_refresh_presence", "expected": False,
        "actual": ["0; url=/one", "10; url=/two"],
    }]


def test_meta_refresh_presence_uses_first_duplicate_http_equiv_attribute():
    html = HTML.replace("</head>",
                        '<meta http-equiv="refresh" http-equiv="content-type" content="0"></head>')
    assert verify_contract({"url": URL, "meta_refresh_present": True}, html)["status"] == "pass"


@pytest.mark.parametrize("value", [None, 0, 1, "false", [], {}])
def test_meta_refresh_presence_rejects_nonboolean_contract(value):
    with pytest.raises(ValueError, match="meta_refresh_present"):
        verify_contract({"url": URL, "meta_refresh_present": value}, HTML)


def test_meta_refresh_presence_cli_json_reports_unexpected_redirect(tmp_path, capsys):
    contract_path = tmp_path / "expected.json"
    html_path = tmp_path / "index.html"
    contract_path.write_text(json.dumps({"url": URL, "meta_refresh_present": False}),
                             encoding="utf-8")
    html_path.write_text(HTML.replace("</head>",
                         '<meta http-equiv="refresh" content="0; url=/new/"></head>'),
                         encoding="utf-8")
    assert main([str(contract_path), str(html_path), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"] == [{
        "code": "meta_refresh_presence", "expected": False, "actual": ["0; url=/new/"],
    }]


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


@pytest.mark.parametrize("opening", [
    "<!doctype html><html><body>",
    "<!doctype html><body>",
    "<!doctype html><html><main>",
    "<html><body>",
])
def test_omitted_empty_head_is_valid_before_body_elements(opening):
    # Browsers insert an empty head even if the first explicit element is body content.
    html = opening + "<h1>Visible</h1>"
    report = verify_contract({"url": URL, "title_present": False, "h1_min": 1}, html)
    assert report["status"] == "pass", report["issues"]


def test_omitted_empty_head_does_not_adopt_late_body_canonical():
    html = ('<!doctype html><html><body><h1>Visible</h1>'
            '<link rel="canonical" href="/en/">')
    report = verify_contract({"url": URL, "h1_min": 1, "canonical": URL}, html)
    assert {issue["code"] for issue in report["issues"]} == {"canonical"}


def test_omitted_empty_head_passes_single_page_cli(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "h1_min": 1}), encoding="utf-8")
    artifact.write_text("<!doctype html><html><body><h1>Page</h1>", encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


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


def test_select_menu_heading_is_part_of_modern_dom():
    # The living HTML standard uses in-body parsing for customizable selects.
    html = ('<html><head><title>Product</title></head><body>'
            '<select><option>One</option><h1>In the DOM</h1></select>'
            '</body></html>')
    result = verify_contract({"url": URL, "h1_min": 1}, html)
    assert result["status"] == "pass"


def test_modern_select_nested_heading_counts_as_dom_heading():
    # WHATWG 2025+ customizable selects use the in-body insertion mode.
    html = ('<html><head><title>Product</title></head><body>'
            '<select><div><h1>Picker heading</h1></div></select></body></html>')
    assert verify_contract({"url": URL, "h1_min": 1}, html)["status"] == "pass"


def test_modern_select_heading_does_not_hide_extra_h1_from_maximum():
    html = ('<html><head><title>Product</title></head><body>'
            '<select><div><h1>Picker heading</h1></div></select>'
            '<h1>Page heading</h1></body></html>')
    report = verify_contract({"url": URL, "h1_max": 1}, html)
    assert {issue["code"] for issue in report["issues"]} == {"h1_max"}
    assert report["issues"][0]["actual"] == 2


def test_heading_inside_and_after_select_both_count():
    html = ('<html><head><title>Product</title></head><body>'
            '<select><option><h1>Picker heading</h1></option></select>'
            '<h1>Real heading</h1></body></html>')
    result = verify_contract({"url": URL, "h1_min": 2, "h1_max": 2}, html)
    assert result["status"] == "pass", result["issues"]


def test_self_closing_select_slash_does_not_hide_nested_heading():
    html = ('<html><head><title>Product</title></head><body>'
            '<select/><h1>Nested heading</h1></select>'
            '<h1>Actual heading</h1></body></html>')
    assert verify_contract({"url": URL, "h1_min": 2, "h1_max": 2}, html)[
        "status"
    ] == "pass"


@pytest.mark.parametrize("following_tag", ["<select>", "<input>", "<textarea></textarea>"])
def test_select_heading_count_with_following_controls(following_tag):
    prefix = ('<html><head><title>Product</title></head><body>'
              '<select><option><h1>Picker heading</h1></option>')
    if following_tag == "<select>":
        # A second select closes the original; its own start tag is ignored.
        tail = '<select><h1>Real heading</h1>'
    elif following_tag == "<input>":
        tail = '<input><h1>Real heading</h1>'
    else:
        tail = '<textarea><h1>Textarea text</h1></textarea><h1>Real heading</h1>'
    result = verify_contract({"url": URL, "h1_min": 2, "h1_max": 2}, prefix + tail)
    assert result["status"] == "pass", result["issues"]


def test_select_only_heading_passes_contract_cli(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "h1_min": 1}), encoding="utf-8")
    artifact.write_text(
        '<html><head><title>Product</title></head><body>'
        '<select><h1>Parsed heading</h1></select></body></html>',
        encoding="utf-8",
    )
    assert main([str(contract), str(artifact), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


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


@pytest.mark.parametrize(
    ("assertion", "hidden_markup", "expected_code"),
    [
        ({"canonical": URL}, '<link rel="canonical" href="/en/">', "canonical"),
        ({"hreflang": {"en": URL}},
         '<link rel="alternate" hreflang="en" href="/en/">', "hreflang:en"),
        ({"title_present": True}, "<title>Fallback title</title>", "title_presence"),
        ({"indexable": False}, '<meta name="robots" content="noindex">', "indexability_meta"),
    ],
)
def test_head_noscript_does_not_supply_active_metadata(assertion, hidden_markup, expected_code):
    # With HTML scripting enabled, noscript contents are raw text, not DOM metadata.
    html = f"<html><head><noscript>{hidden_markup}</noscript></head><body></body></html>"
    report = verify_contract({"url": URL, **assertion}, html)
    assert {issue["code"] for issue in report["issues"]} == {expected_code}


@pytest.mark.parametrize("opening", ["<noscript>", "<noscript/>"])
def test_body_noscript_markup_cannot_supply_h1_or_jsonld(opening):
    html = (
        f'<html><head></head><body>{opening}<h1>Fallback heading</h1>'
        '<script type="application/ld+json">{"@type":"FAQPage"}</script>'
        '</noscript></body></html>'
    )
    report = verify_contract({"url": URL, "h1_min": 1, "jsonld_types": ["FAQPage"]}, html)
    assert {issue["code"] for issue in report["issues"]} == {
        "h1_min", "jsonld_type_missing",
    }


def test_noscript_raw_text_does_not_hide_following_active_seo():
    html = ('<html><head><noscript><link rel="canonical" href="/bad/"></noscript>'
            '<link rel="canonical" href="/en/"></head>'
            '<body><noscript><h1>Fallback</h1></noscript><h1>Real</h1></body></html>')
    report = verify_contract({"url": URL, "canonical": URL, "h1_min": 1, "h1_max": 1}, html)
    assert report["status"] == "pass", report["issues"]


def test_noscript_fake_heading_fails_offline_cli(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "h1_min": 1}), encoding="utf-8")
    artifact.write_text(
        "<html><head></head><body><noscript><h1>Fallback</h1></noscript></body></html>",
        encoding="utf-8",
    )
    assert main([str(contract), str(artifact), "--json"]) == 1
    assert {issue["code"] for issue in json.loads(capsys.readouterr().out)["issues"]} == {
        "h1_min",
    }


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


@pytest.mark.parametrize("tag", ["template", "textarea", "title", "iframe", "script", "style"])
def test_nonvoid_trailing_slash_does_not_make_example_heading_active(tag):
    # HTML ignores /> on non-void HTML tags; the apparent H1 is still text
    # or inert template content until the matching closing tag.
    html = (f'<html><head></head><body><{tag}/><h1>Example only</h1>'
            f'</{tag}></body></html>')
    report = verify_contract({"url": URL, "h1_min": 1}, html)
    assert {issue["code"] for issue in report["issues"]} == {"h1_min"}


def test_self_closing_template_does_not_expose_jsonld():
    html = ('<html><head></head><body><template/>'
            '<script type="application/ld+json">{"@type":"FAQPage"}</script>'
            '</template></body></html>')
    report = verify_contract({"url": URL, "jsonld_types": ["FAQPage"]}, html)
    assert {issue["code"] for issue in report["issues"]} == {"jsonld_type_missing"}


def test_nonvoid_script_trailing_slash_keeps_jsonld_content():
    html = ('<html><head></head><body><script type="application/ld+json"/>'
            '{"@type":"FAQPage"}</script></body></html>')
    report = verify_contract({"url": URL, "jsonld_types": ["FAQPage"]}, html)
    assert report["status"] == "pass", report["issues"]


def test_nonvoid_head_trailing_slash_keeps_later_canonical_in_head():
    html = ('<!doctype html><html><head/><link rel="canonical" href="/en/">'
            '</head><body><h1>Real heading</h1></body></html>')
    report = verify_contract({"url": URL, "canonical": URL}, html)
    assert report["status"] == "pass", report["issues"]


def test_void_link_trailing_slash_still_counts_canonical():
    html = ('<!doctype html><html><head><link rel="canonical" href="/en/"/>'
            '</head><body></body></html>')
    assert verify_contract({"url": URL, "canonical": URL}, html)["status"] == "pass"


def test_nonvoid_template_trailing_slash_fails_offline_cli(tmp_path, capsys):
    contract = tmp_path / "expected.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "h1_min": 1}), encoding="utf-8")
    artifact.write_text(
        '<html><head></head><body><template/><h1>Example</h1></template></body></html>',
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


@pytest.mark.parametrize("depth", [256, 6000, 12000])
def test_excessively_nested_jsonld_fails_as_finding_instead_of_crashing(depth):
    nested = "[" * depth + '{"@type":"WebPage"}' + "]" * depth
    html = HTML.replace('{"@graph":[{"@type":"WebPage"}]}', nested)
    report = verify_contract(CONTRACT, html)
    assert report["status"] == "fail"
    assert {issue["code"] for issue in report["issues"]} == {
        "jsonld_invalid", "jsonld_type_missing",
    }


def test_nested_jsonld_within_parser_limit_still_satisfies_type():
    nested = "[" * 255 + '{"@type":"WebPage"}' + "]" * 255
    html = HTML.replace('{"@graph":[{"@type":"WebPage"}]}', nested)
    assert verify_contract(CONTRACT, html)["status"] == "pass"


def test_jsonld_brackets_and_escaped_quotes_in_strings_are_not_counted_as_nesting():
    payload = json.dumps({
        "@type": "WebPage",
        "name": "[" * 500 + 'escaped \\" quotation ' + "}" * 500,
    })
    html = HTML.replace('{"@graph":[{"@type":"WebPage"}]}', payload)
    assert verify_contract(CONTRACT, html)["status"] == "pass"


def test_deeply_nested_jsonld_is_machine_readable_cli_failure(tmp_path, capsys):
    contract_path = tmp_path / "expected.json"
    html_path = tmp_path / "index.html"
    nested = "[" * 6000 + '{"@type":"WebPage"}' + "]" * 6000
    contract_path.write_text(json.dumps({"url": URL, "jsonld_types": ["WebPage"]}), encoding="utf-8")
    html_path.write_text(
        '<html><head><script type="application/ld+json">' + nested
        + '</script></head><body></body></html>',
        encoding="utf-8",
    )
    assert main([str(contract_path), str(html_path), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert {issue["code"] for issue in report["issues"]} == {
        "jsonld_invalid", "jsonld_type_missing",
    }


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


@pytest.mark.parametrize("number", ["1e9999", "-1e9999", "2.5e10000"])
@pytest.mark.parametrize("nested", [False, True])
def test_overflowing_jsonld_float_cannot_pass_schema_assertion(number, nested):
    # json.loads accepts JSON exponents that overflow a Python float to infinity.
    payload = (
        f'{{"@type":"WebPage","metric":{{"items":[{number}]}}}}'
        if nested else f'{{"@type":"WebPage","metric":{number}}}'
    )
    html = HTML.replace('{"@graph":[{"@type":"WebPage"}]}', payload)
    result = verify_contract(CONTRACT, html)
    assert {issue["code"] for issue in result["issues"]} == {
        "jsonld_invalid", "jsonld_type_missing",
    }


def test_overflowing_jsonld_float_fails_single_page_cli(tmp_path, capsys):
    contract_path = tmp_path / "expected.json"
    html_path = tmp_path / "index.html"
    contract_path.write_text(json.dumps({"url": URL, "jsonld_types": ["WebPage"]}), encoding="utf-8")
    html_path.write_text(
        '<html><head><script type="application/ld+json">'
        '{"@type":"WebPage","metric":1e9999}</script></head><body></body></html>',
        encoding="utf-8",
    )
    assert main([str(contract_path), str(html_path), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert {issue["code"] for issue in report["issues"]} == {
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
        '{"@type":"WebPage","rating":1.5,"position":-12,"fraction":2e-3,"large":1e308}',
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


def test_jsonld_json_literal_content_does_not_supply_entity_types():
    structured_data = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "additionalProperty": {
            "@value": {"@type": "FAQPage", "items": [{"@type": "Answer"}]},
            "@type": "@json",
        },
    }
    html = (
        '<html><head><script type="application/ld+json">'
        + json.dumps(structured_data)
        + '</script></head><body></body></html>'
    )
    missing = verify_contract({"url": URL, "jsonld_types": ["FAQPage", "Answer"]}, html)
    assert [issue["code"] for issue in missing["issues"]] == [
        "jsonld_type_missing", "jsonld_type_missing",
    ]
    assert verify_contract({"url": URL, "jsonld_types": ["Dataset"]}, html)[
        "status"
    ] == "pass"


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
    r"https://example.com\en/",
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
    r"https://example.com\en/",
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


@pytest.mark.parametrize("tag", ["title", "textarea", "xmp", "iframe", "noembed", "noframes"])
@pytest.mark.parametrize("assertion", ["h1", "jsonld"])
def test_raw_text_inside_template_cannot_end_template_early(tag, assertion):
    # A literal </template> inside RCDATA/RAWTEXT is text, not an end tag.
    ghost = ('<h1>Ghost heading</h1>' if assertion == "h1" else
             '<script type="application/ld+json">{"@type":"FAQPage"}</script>')
    html = ('<html><head></head><body><template>'
            f'<{tag}>Code example </template> still text</{tag}>'
            f'{ghost}</template></body></html>')
    contract = ({"url": URL, "h1_min": 1} if assertion == "h1" else
                {"url": URL, "jsonld_types": ["FAQPage"]})
    report = verify_contract(contract, html)
    expected_code = "h1_min" if assertion == "h1" else "jsonld_type_missing"
    assert expected_code in {issue["code"] for issue in report["issues"]}


def test_template_raw_text_closure_preserves_real_heading_after_template():
    html = ('<html><head></head><body><template><xmp>Example </template>'
            '</xmp><h1>Inert</h1></template><h1>Visible</h1></body></html>')
    assert verify_contract({"url": URL, "h1_min": 1, "h1_max": 1}, html)[
        "status"
    ] == "pass"


def test_template_raw_text_cannot_expose_fake_head_title():
    html = ('<html><head><template><textarea>Literal </template>'
            '</textarea><title>Inert</title></template></head><body></body></html>')
    report = verify_contract({"url": URL, "title_present": True}, html)
    assert "title_presence" in {issue["code"] for issue in report["issues"]}


def test_template_raw_text_false_heading_is_reported_by_json_cli(tmp_path, capsys):
    contract = tmp_path / "expected.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "h1_min": 1}), encoding="utf-8")
    artifact.write_text(
        '<html><head></head><body><template><iframe>Literal </template>'
        '</iframe><h1>Ghost</h1></template></body></html>',
        encoding="utf-8",
    )
    assert main([str(contract), str(artifact), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert "h1_min" in {issue["code"] for issue in report["issues"]}


def test_body_only_head_metadata_cannot_satisfy_contract():
    html = '''<html><head></head><body>
    <title>Not a head title</title>
    <link rel="canonical" href="/en/">
    <link rel="alternate" hreflang="en" href="/en/">
    <link rel="alternate" hreflang="sl" href="/sl/">
    <meta name="description" content="Not a head description">
    <meta name="robots" content="noindex">
    <h1>Visible heading</h1>
    <script type="application/ld+json">{"@type":"WebPage"}</script>
    </body></html>'''
    result = verify_contract(
        {**CONTRACT, "indexable": False, "description_present": True}, html,
        include_hreflang=True, include_canonical=True,
    )
    assert {issue["code"] for issue in result["issues"]} == {
        "title_presence", "description_presence", "canonical", "hreflang:en", "hreflang:sl",
    }
    assert result["observed_canonical"] == []
    assert result["observed_hreflang"] == {}


@pytest.mark.parametrize("name", ["robots", "googlebot"])
@pytest.mark.parametrize("directive", ["noindex", "none", "NoInDeX, follow"])
def test_body_robots_meta_restricts_google_indexability(name, directive):
    # Google honors body robots meta even though other head-only SEO signals
    # do not move into the body.
    html = (f'<html><head></head><body><main><meta name="{name}" '
            f'content="{directive}"><h1>Visible</h1></main></body></html>')
    assert verify_contract({"url": URL, "indexable": False}, html)["status"] == "pass"
    report = verify_contract({"url": URL, "indexable": True}, html)
    assert report["issues"] == [{
        "code": "indexability_meta", "expected": True, "actual": False,
    }]


@pytest.mark.parametrize("wrapper", ["template", "noscript"])
def test_body_robots_meta_in_inert_content_is_not_active(wrapper):
    html = (f'<html><head></head><body><{wrapper}>'
            '<meta name="robots" content="noindex">'
            f'</{wrapper}><h1>Visible</h1></body></html>')
    assert verify_contract({"url": URL, "indexable": True}, html)["status"] == "pass"


def test_body_robots_noindex_fails_single_page_json_cli(tmp_path, capsys):
    contract = tmp_path / "expected.json"
    artifact = tmp_path / "page.html"
    contract.write_text(json.dumps({"url": URL, "indexable": True}), encoding="utf-8")
    artifact.write_text(
        '<html><head></head><body><meta name="robots" content="noindex"></body></html>',
        encoding="utf-8",
    )
    assert main([str(contract), str(artifact), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["issues"] == [{
        "code": "indexability_meta", "expected": True, "actual": False,
    }]


def test_body_metadata_preserves_head_links_but_robots_meta_restricts_indexing():
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
    assert result["issues"] == [{
        "code": "indexability_meta", "expected": True, "actual": False,
    }]
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


@pytest.mark.parametrize("base", ["javascript:alert(1)", r"https://example.com\wrong/"])
def test_invalid_document_base_fails_closed(base):
    html = (f'<html><head><base href="{base}">'
            '<link rel="canonical" href="/en/"></head><body></body></html>')
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


@pytest.mark.parametrize("links,expected,observed", [
    ('<link rel="canonical" href="/en/">', True, None),
    ('<link REL="CANONICAL" href="https://example.com/other">', True, None),
    ('<link rel="canonical" href="//example.com/en/">', True, None),
    ('', True, []),
    ('<link rel="canonical" href="">', True, [""]),
    ('<link rel="canonical" href="javascript:alert(1)">', True, ["javascript:alert(1)"]),
    ('<link rel="canonical" href="https://[broken">', True, ["https://[broken"]),
    ('<link rel="canonical" href="/en/"><link rel="canonical" href="/other">',
     True, [URL, "https://example.com/other"]),
    ('', False, None),
    ('<link rel="canonical" href="/en/">', False, [URL]),
    ('<link rel="canonical" href=" ">', False, [""]),
])
def test_canonical_presence_requires_one_valid_target_or_no_active_target(links, expected, observed):
    html = f"<html><head>{links}</head><body><h1>Page</h1></body></html>"
    report = verify_contract({"url": URL, "canonical_present": expected}, html)
    if observed is None:
        assert report["status"] == "pass", report["issues"]
    else:
        assert report["issues"] == [{
            "code": "canonical_presence", "expected": expected, "actual": observed,
        }]


@pytest.mark.parametrize("markup", [
    '<template><link rel="canonical" href="/en/"></template>',
    '<noscript><link rel="canonical" href="/en/"></noscript>',
    '</head><body><link rel="canonical" href="/en/">',
])
def test_canonical_presence_does_not_use_inert_or_body_links(markup):
    html = f"<html><head>{markup}</body></html>"
    assert verify_contract({"url": URL, "canonical_present": False}, html)["status"] == "pass"
    result = verify_contract({"url": URL, "canonical_present": True}, html)
    assert {issue["code"] for issue in result["issues"]} == {"canonical_presence"}


def test_canonical_presence_resolves_document_base_without_fetching():
    html = ('<html><head><base href="/fr/">'
            '<link rel="canonical" href="page.html"></head><body></body></html>')
    report = verify_contract({"url": URL, "canonical_present": True}, html)
    assert report["status"] == "pass", report["issues"]


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_canonical_presence_requires_boolean(value):
    with pytest.raises(ValueError, match="canonical_present"):
        verify_contract({"url": URL, "canonical_present": value}, HTML)


def test_canonical_presence_cli_returns_actionable_json(tmp_path, capsys):
    contract_path = tmp_path / "expected.json"
    html_path = tmp_path / "index.html"
    contract_path.write_text(json.dumps({"url": URL, "canonical_present": True}), encoding="utf-8")
    html_path.write_text('<html><head><link rel="canonical" href=""></head></html>',
                         encoding="utf-8")
    assert main([str(contract_path), str(html_path), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"] == [{
        "code": "canonical_presence", "expected": True, "actual": [""],
    }]
    html_path.write_text(HTML, encoding="utf-8")
    assert main([str(contract_path), str(html_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


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


@pytest.mark.parametrize(
    ("metadata", "expected_present", "expected_code"),
    [
        ('<meta name="description" content="A specific page summary">', True, None),
        ('<meta NAME="DESCRIPTION" content="  A summary  ">', True, None),
        ('<meta name="description" content="   ">', True, "description_presence"),
        ('<meta name="description">', True, "description_presence"),
        ("", True, "description_presence"),
        ('<meta name="description" content="Summary">', False, "description_presence"),
    ],
)
def test_opt_in_meta_description_presence(metadata, expected_present, expected_code):
    html = f"<html><head>{metadata}</head><body><h1>Title</h1></body></html>"
    report = verify_contract({"url": URL, "description_present": expected_present}, html)
    assert {issue["code"] for issue in report["issues"]} == (
        {expected_code} if expected_code else set()
    )


def test_description_in_body_or_template_does_not_satisfy_head_contract():
    html = (
        '<html><head><template><meta name="description" content="Example"></template>'
        '</head><body><meta name="description" content="Body only"></body></html>'
    )
    report = verify_contract({"url": URL, "description_present": True}, html)
    assert report["issues"] == [{
        "code": "description_presence", "expected": True, "actual": False,
    }]


def test_first_head_meta_description_wins_over_later_duplicate():
    html = ('<html><head><meta name="description" content=" ">'
            '<meta name="description" content="Valid second"></head></html>')
    report = verify_contract({"url": URL, "description_present": True}, html)
    assert {issue["code"] for issue in report["issues"]} == {"description_presence"}
    assert verify_contract({"url": URL, "description_present": False}, html)["status"] == "pass"


def test_description_presence_requires_boolean():
    with pytest.raises(ValueError, match="'description_present' must be a boolean"):
        verify_contract({"url": URL, "description_present": "true"}, "<html><head></head></html>")


def test_description_presence_contract_cli_json(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "description_present": True}), encoding="utf-8")
    artifact.write_text("<html><head></head><body></body></html>", encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 1
    assert {item["code"] for item in json.loads(capsys.readouterr().out)["issues"]} == {
        "description_presence"
    }
    artifact.write_text(
        '<html><head><meta name="description" content="Page summary"></head></html>',
        encoding="utf-8",
    )
    assert main([str(contract), str(artifact), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


@pytest.mark.parametrize("expected,metadata", [
    ("Specific page summary", '<meta name="description" content="Specific page summary">'),
    ("Specific page summary", '<meta NAME="DESCRIPTION" content=" Specific  page\nsummary ">'),
    ("Tea & Coffee", '<meta name="description" content="Tea &amp; Coffee">'),
    ("Specific page summary", '<template><meta name="description" content="Wrong"></template>'
                              '<meta name="description" content="Specific page summary">'),
])
def test_description_equals_accepts_normalized_first_active_head_description(expected, metadata):
    html = f"<html><head>{metadata}</head><body></body></html>"
    report = verify_contract({"url": URL, "description_equals": expected}, html)
    assert report["status"] == "pass", report["issues"]


@pytest.mark.parametrize("metadata,actual", [
    ('<meta name="description" content="Incorrect summary">', "Incorrect summary"),
    ('<meta name="description" content="specific page summary">', "specific page summary"),
    ('<meta name="description" content="">', ""),
    ('<meta name="description" content="Incorrect first">'
     '<meta name="description" content="Specific page summary">', "Incorrect first"),
    ('<template><meta name="description" content="Specific page summary"></template>', ""),
    ("", ""),
])
def test_description_equals_detects_copy_regressions_even_when_presence_passes(metadata, actual):
    html = f'<html><head>{metadata}</head><body>' \
           '<meta name="description" content="Specific page summary"></body></html>'
    report = verify_contract({"url": URL, "description_equals": "Specific page summary"}, html)
    assert report["issues"] == [{
        "code": "description_equals", "expected": "Specific page summary", "actual": actual,
    }]


@pytest.mark.parametrize("value", [None, False, 1, "", "  \t\n", [], {}])
def test_description_equals_rejects_invalid_expected_text(value):
    with pytest.raises(ValueError, match="description_equals"):
        verify_contract({"url": URL, "description_equals": value}, HTML)


def test_description_equals_cli_reports_mismatch(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "description_equals": "Expected summary"}),
                        encoding="utf-8")
    artifact.write_text('<html><head><meta name="description" content="Actual summary">'
                        '</head></html>', encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"] == [{
        "code": "description_equals", "expected": "Expected summary", "actual": "Actual summary",
    }]


@pytest.mark.parametrize(
    ("html_open", "expected", "actual"),
    [
        ('<html lang="en-US">', "en-us", None),
        ('<html LANG="EN-us">', "en-US", None),
        ('<html lang="sl">', "en", "sl"),
        ('<html lang="">', "en", ""),
        ("<html>", "en", None),
    ],
)
def test_opt_in_html_language_matches_root_attribute(html_open, expected, actual):
    html = f"<!doctype html>{html_open}<head></head><body></body></html>"
    report = verify_contract({"url": URL, "html_lang": expected}, html)
    if actual is None and "lang=" in html_open.lower():
        assert report["status"] == "pass", report["issues"]
    else:
        assert report["issues"] == [{
            "code": "html_lang", "expected": expected, "actual": actual,
        }]


def test_html_language_uses_first_duplicate_attribute():
    html = '<html lang="sl" lang="en"><head></head><body></body></html>'
    report = verify_contract({"url": URL, "html_lang": "en"}, html)
    assert report["issues"] == [{"code": "html_lang", "expected": "en", "actual": "sl"}]


def test_html_language_ignores_nested_or_late_html_tags():
    html = ('<!doctype html><head><template><html lang="en"></html></template>'
            '</head><body><html lang="en"></html></body>')
    report = verify_contract({"url": URL, "html_lang": "en"}, html)
    assert report["issues"] == [{"code": "html_lang", "expected": "en", "actual": None}]


@pytest.mark.parametrize("invalid", ["", "  ", " en", "en ", 42, True, None])
def test_html_language_contract_rejects_invalid_expected_value(invalid):
    with pytest.raises(ValueError, match="'html_lang'"):
        verify_contract({"url": URL, "html_lang": invalid}, '<html lang="en"><head></head>')


def test_html_language_contract_cli_json(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "html_lang": "sl"}), encoding="utf-8")
    artifact.write_text('<html lang="en"><head></head><body></body></html>', encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"] == [
        {"code": "html_lang", "expected": "sl", "actual": "en"},
    ]
    artifact.write_text('<html lang="SL"><head></head></html>', encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


@pytest.mark.parametrize(
    ("images", "limit", "missing"),
    [
        ('<img src="/hero.webp">', 0, 1),
        ('<img src="/hero.webp" alt="">', 0, None),
        ('<img alt="Product photo" src="/hero.webp">', 0, None),
        ('<img alt src="/decorative.svg">', 0, None),
        ('<picture><source srcset="/hero.webp"><img src="/hero.jpg"></picture>', 0, 1),
        ('<img src="/a.webp"><img src="/b.webp" alt="">', 1, None),
        ('<img src="/a.webp"><img src="/b.webp">', 1, 2),
    ],
)
def test_opt_in_image_alt_attribute_coverage(images, limit, missing):
    html = f"<html><head></head><body>{images}</body></html>"
    report = verify_contract({"url": URL, "img_missing_alt_max": limit}, html)
    expected = (
        [{"code": "img_missing_alt_max", "expected": limit, "actual": missing}]
        if missing is not None else []
    )
    assert report["issues"] == expected


def test_image_alt_coverage_ignores_inert_examples_and_non_image_sources():
    html = (
        "<html><head></head><body>"
        '<template><img src="/template.webp"></template>'
        '<noscript><img src="/fallback.webp"></noscript>'
        '<textarea><img src="/literal.webp"></textarea>'
        '<script>const sample = "<img src=/fake.webp>";</script>'
        '<picture><source srcset="/responsive.webp"><img src="/real.webp" alt=""></picture>'
        "</body></html>"
    )
    assert verify_contract({"url": URL, "img_missing_alt_max": 0}, html)["status"] == "pass"


def test_image_alt_coverage_first_duplicate_alt_attribute_wins():
    html = '<html><head></head><body><img alt="" alt="Ignored" src="/a.webp"></body></html>'
    assert verify_contract({"url": URL, "img_missing_alt_max": 0}, html)["status"] == "pass"


@pytest.mark.parametrize("invalid", [-1, True, 0.5, "0", None])
def test_image_alt_coverage_rejects_invalid_threshold(invalid):
    with pytest.raises(ValueError, match="'img_missing_alt_max'"):
        verify_contract({"url": URL, "img_missing_alt_max": invalid}, "<html><head></head>")


def test_image_alt_coverage_cli_json_reports_missing_attribute(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "img_missing_alt_max": 0}), encoding="utf-8")
    artifact.write_text(
        '<html><head></head><body><img src="/hero.webp"></body></html>', encoding="utf-8",
    )
    assert main([str(contract), str(artifact), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"] == [
        {"code": "img_missing_alt_max", "expected": 0, "actual": 1},
    ]
    artifact.write_text(
        '<html><head></head><body><img alt="" src="/hero.webp"></body></html>',
        encoding="utf-8",
    )
    assert main([str(contract), str(artifact), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


@pytest.mark.parametrize("expected,metadata", [
    ("Search audit dashboard", '<meta property="og:title" content="Search audit dashboard">'),
    ("Search audit dashboard", '<meta PROPERTY="og:title" content=" Search  audit\n dashboard ">'),
    ("Tea & Coffee", '<meta property="og:title" content="Tea &amp; Coffee">'),
    ("Search audit dashboard", '<template><meta property="og:title" content="Wrong"></template>'
                               '<meta property="og:title" content="Search audit dashboard">'),
])
def test_og_title_equals_matches_first_active_head_open_graph_title(expected, metadata):
    html = f"<html><head>{metadata}</head><body></body></html>"
    result = verify_contract({"url": URL, "og_title_equals": expected}, html)
    assert result["status"] == "pass", result["issues"]


@pytest.mark.parametrize("metadata,actual", [
    ('<meta property="og:title" content="Wrong title">', "Wrong title"),
    ('<meta property="og:title" content="search audit dashboard">', "search audit dashboard"),
    ('<meta property="og:title">', ""),
    ('<meta property="og:title" content="">'
     '<meta property="og:title" content="Search audit dashboard">', ""),
    ('<template><meta property="og:title" content="Search audit dashboard"></template>', ""),
    ('<meta name="og:title" content="Search audit dashboard">', ""),
    ("", ""),
])
def test_og_title_equals_detects_social_preview_regressions(metadata, actual):
    html = (f"<html><head><title>Search audit dashboard</title>{metadata}</head>"
            '<body><meta property="og:title" content="Search audit dashboard"></body></html>')
    result = verify_contract({"url": URL, "og_title_equals": "Search audit dashboard"}, html)
    assert result["issues"] == [{
        "code": "og_title_equals", "expected": "Search audit dashboard", "actual": actual,
    }]


def test_og_title_equals_works_with_implicit_head_and_duplicate_attributes():
    html = ('<!doctype html><meta property="og:title" content="Wrong" '
            'content="Search audit dashboard"><h1>Page</h1>')
    result = verify_contract({"url": URL, "og_title_equals": "Search audit dashboard"}, html)
    assert result["issues"] == [{
        "code": "og_title_equals", "expected": "Search audit dashboard", "actual": "Wrong",
    }]


@pytest.mark.parametrize("value", [None, False, 1, "", "  \t\n", [], {}])
def test_og_title_equals_rejects_invalid_expected_text(value):
    with pytest.raises(ValueError, match="og_title_equals"):
        verify_contract({"url": URL, "og_title_equals": value}, HTML)


def test_og_title_equals_cli_reports_json_failure_and_success(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "og_title_equals": "Expected share title"}),
                        encoding="utf-8")
    artifact.write_text('<html><head><meta property="og:title" content="Old share title">'
                        '</head></html>', encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"] == [{
        "code": "og_title_equals", "expected": "Expected share title", "actual": "Old share title",
    }]
    artifact.write_text('<html><head><meta property="og:title" '
                        'content="Expected share title"></head></html>', encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


@pytest.mark.parametrize("expected,metadata", [
    ("A quick site audit", '<meta property="og:description" content="A quick site audit">'),
    ("A quick site audit", '<meta PROPERTY="og:description" content=" A quick  site\n audit ">'),
    ("Tea & Coffee", '<meta property="og:description" content="Tea &amp; Coffee">'),
    ("A quick site audit", '<template><meta property="og:description" content="Wrong"></template>'
                           '<meta property="og:description" content="A quick site audit">'),
])
def test_og_description_equals_matches_first_active_head_description(expected, metadata):
    html = f"<html><head>{metadata}</head><body></body></html>"
    result = verify_contract({"url": URL, "og_description_equals": expected}, html)
    assert result["status"] == "pass", result["issues"]


@pytest.mark.parametrize("metadata,actual", [
    ('<meta property="og:description" content="Old summary">', "Old summary"),
    ('<meta property="og:description" content="a quick site audit">', "a quick site audit"),
    ('<meta property="og:description">', ""),
    ('<meta property="og:description" content="">'
     '<meta property="og:description" content="A quick site audit">', ""),
    ('<template><meta property="og:description" content="A quick site audit"></template>', ""),
    ('<meta name="og:description" content="A quick site audit">', ""),
    ('<noscript><meta property="og:description" content="A quick site audit"></noscript>', ""),
    ("", ""),
])
def test_og_description_equals_detects_social_summary_regressions(metadata, actual):
    html = (f'<html><head><meta name="description" content="A quick site audit">{metadata}</head>'
            '<body><meta property="og:description" content="A quick site audit"></body></html>')
    result = verify_contract({"url": URL, "og_description_equals": "A quick site audit"}, html)
    assert result["issues"] == [{
        "code": "og_description_equals", "expected": "A quick site audit", "actual": actual,
    }]


def test_og_description_equals_implicit_head_keeps_first_duplicate_attribute():
    html = ('<!doctype html><meta property="og:description" content="Old" '
            'content="A quick site audit"><h1>Page</h1>')
    result = verify_contract({"url": URL, "og_description_equals": "A quick site audit"}, html)
    assert result["issues"] == [{
        "code": "og_description_equals", "expected": "A quick site audit", "actual": "Old",
    }]


@pytest.mark.parametrize("value", [None, False, 1, "", "  \t\n", [], {}])
def test_og_description_equals_rejects_invalid_expected_text(value):
    with pytest.raises(ValueError, match="og_description_equals"):
        verify_contract({"url": URL, "og_description_equals": value}, HTML)


def test_og_description_equals_cli_reports_json_failure_and_success(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "og_description_equals": "Expected social summary"}),
                        encoding="utf-8")
    artifact.write_text('<html><head><meta property="og:description" content="Old summary">'
                        '</head></html>', encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"] == [{
        "code": "og_description_equals", "expected": "Expected social summary", "actual": "Old summary",
    }]
    artifact.write_text('<html><head><meta property="og:description" '
                        'content="Expected social summary"></head></html>', encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


@pytest.mark.parametrize("metadata", [
    '<meta property="og:url" content="https://example.com/story">',
    '<meta PROPERTY="og:url" content=" https://example.com/story ">',
    '<meta property="og:url" content="https://example.com/story">'
    '<meta property="og:url" content="https://example.com/other">',
    '<template><meta property="og:url" content="https://example.com/other"></template>'
    '<meta property="og:url" content="https://example.com/story">',
])
def test_og_url_equals_accepts_first_active_head_identity(metadata):
    html = f"<html><head>{metadata}</head><body></body></html>"
    result = verify_contract({"url": URL, "og_url_equals": "https://example.com/story"}, html)
    assert result["status"] == "pass", result["issues"]


@pytest.mark.parametrize("metadata,actual", [
    ("", None),
    ('<meta property="og:url">', ""),
    ('<meta property="og:url" content="">', ""),
    ('<meta property="og:url" content="/story">', "/story"),
    ('<meta property="og:url" content="https://example.com/other">',
     "https://example.com/other"),
    ('<meta property="og:url" content="https://example.com/story?utm_source=ads">',
     "https://example.com/story?utm_source=ads"),
    ('<meta property="og:url" content="">'
     '<meta property="og:url" content="https://example.com/story">', ""),
    ('<meta property="og:url" content="https://example.com/other">'
     '<meta property="og:url" content="https://example.com/story">',
     "https://example.com/other"),
    ('<meta name="og:url" content="https://example.com/story">', None),
    ('<template><meta property="og:url" content="https://example.com/story">'
     '</template>', None),
    ('<noscript><meta property="og:url" content="https://example.com/story">'
     '</noscript>', None),
])
def test_og_url_equals_detects_missing_or_changed_identity(metadata, actual):
    html = (f"<html><head>{metadata}</head>"
            '<body><meta property="og:url" content="https://example.com/story"></body></html>')
    result = verify_contract({"url": URL, "og_url_equals": "https://example.com/story"}, html)
    assert result["issues"] == [{
        "code": "og_url_equals", "expected": "https://example.com/story", "actual": actual,
    }]


def test_og_url_equals_decodes_entities_and_keeps_first_duplicate_content():
    html = ('<!doctype html><meta property="og:url" '
            'content="https://example.com/story?a=1&amp;b=2" '
            'content="https://example.com/other"><h1>Page</h1>')
    result = verify_contract({
        "url": URL, "og_url_equals": "https://example.com/story?a=1&b=2",
    }, html)
    assert result["status"] == "pass", result["issues"]


@pytest.mark.parametrize("value", [
    None, False, 1, "", "  ", "/story", "//example.com/story", "javascript:alert(1)",
    "https://example.com:99999/story", "https://user@example.com/story", [], {},
])
def test_og_url_equals_rejects_invalid_expected_url(value):
    with pytest.raises(ValueError, match="og_url_equals"):
        verify_contract({"url": URL, "og_url_equals": value}, HTML)


def test_og_url_equals_cli_json_reports_mismatch_and_pass(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "og_url_equals": "https://example.com/story"}),
                        encoding="utf-8")
    artifact.write_text('<html><head><meta property="og:url" '
                        'content="https://example.com/other"></head></html>', encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"] == [{
        "code": "og_url_equals", "expected": "https://example.com/story",
        "actual": "https://example.com/other",
    }]
    artifact.write_text('<html><head><meta property="og:url" '
                        'content="https://example.com/story"></head></html>', encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


@pytest.mark.parametrize("metadata", [
    '<meta property="og:image" content="https://cdn.example.com/hero.jpg">',
    '<meta property="og:image:url" content="https://cdn.example.com/hero.jpg">',
    '<meta PROPERTY="og:image" content=" https://cdn.example.com/hero.jpg ">',
    '<meta property="og:image" content="https://cdn.example.com/hero.jpg">'
    '<meta property="og:image" content="/broken-relative.jpg">',
    '<meta property="og:image" content="https://cdn.example.com/hero.jpg?a=1&amp;b=2">',
    '<template><meta property="og:image" content="/invalid.jpg"></template>'
    '<meta property="og:image" content="https://cdn.example.com/hero.jpg">',
])
def test_og_image_presence_accepts_valid_first_head_image_url(metadata):
    html = f"<html><head>{metadata}</head><body></body></html>"
    result = verify_contract({"url": URL, "og_image_present": True}, html)
    assert result["status"] == "pass", result["issues"]


@pytest.mark.parametrize("metadata,actual", [
    ("", None),
    ('<meta property="og:image">', ""),
    ('<meta property="og:image" content="  ">', ""),
    ('<meta property="og:image" content="/relative.jpg">', "/relative.jpg"),
    ('<meta property="og:image" content="//cdn.example.com/hero.jpg">',
     "//cdn.example.com/hero.jpg"),
    ('<meta property="og:image" content="javascript:alert(1)">', "javascript:alert(1)"),
    ('<meta property="og:image" content="https://cdn.example.com:99999/a.jpg">',
     "https://cdn.example.com:99999/a.jpg"),
    ('<meta property="og:image" content="https://user:pw@cdn.example.com/a.jpg">',
     "https://user:pw@cdn.example.com/a.jpg"),
    ('<meta property="og:image" content="/wrong.jpg">'
     '<meta property="og:image" content="https://cdn.example.com/hero.jpg">', "/wrong.jpg"),
    ('<meta property="og:image:url" content="/wrong.jpg">'
     '<meta property="og:image" content="https://cdn.example.com/hero.jpg">', "/wrong.jpg"),
    ('<meta name="og:image" content="https://cdn.example.com/hero.jpg">', None),
    ('<meta property="og:image:secure_url" content="https://cdn.example.com/hero.jpg">',
     None),
    ('<template><meta property="og:image" content="https://cdn.example.com/hero.jpg">'
     '</template>', None),
    ('<noscript><meta property="og:image" content="https://cdn.example.com/hero.jpg">'
     '</noscript>', None),
])
def test_og_image_presence_rejects_missing_or_bad_first_image(metadata, actual):
    html = (f"<html><head>{metadata}</head>"
            '<body><meta property="og:image" content="https://cdn.example.com/hero.jpg">'
            "</body></html>")
    result = verify_contract({"url": URL, "og_image_present": True}, html)
    assert result["issues"] == [{
        "code": "og_image_presence", "expected": True, "actual": actual,
    }]


def test_og_image_presence_implicit_head_uses_first_duplicate_attribute():
    html = ('<!doctype html><meta property="og:image" content="/wrong.jpg" '
            'content="https://cdn.example.com/hero.jpg"><h1>Page</h1>')
    result = verify_contract({"url": URL, "og_image_present": True}, html)
    assert result["issues"] == [{
        "code": "og_image_presence", "expected": True, "actual": "/wrong.jpg",
    }]


@pytest.mark.parametrize("metadata,expected_status,actual", [
    ("", "pass", None),
    ('<template><meta property="og:image" content="https://cdn.example.com/a.jpg">'
     '</template>', "pass", None),
    ('<meta property="og:image" content="https://cdn.example.com/a.jpg">',
     "fail", "https://cdn.example.com/a.jpg"),
    ('<meta property="og:image" content="">', "fail", ""),
])
def test_og_image_presence_false_requires_no_active_head_image(metadata, expected_status, actual):
    result = verify_contract({"url": URL, "og_image_present": False},
                             f"<html><head>{metadata}</head><body></body></html>")
    assert result["status"] == expected_status
    if expected_status == "fail":
        assert result["issues"] == [{
            "code": "og_image_presence", "expected": False, "actual": actual,
        }]


@pytest.mark.parametrize("value", [None, 1, "true", "", [], {}])
def test_og_image_presence_rejects_non_boolean_contract_values(value):
    with pytest.raises(ValueError, match="og_image_present"):
        verify_contract({"url": URL, "og_image_present": value}, HTML)


def test_og_image_presence_cli_json_failure_and_pass(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "og_image_present": True}), encoding="utf-8")
    artifact.write_text('<html><head><meta property="og:image" content="/relative.jpg">'
                        '</head></html>', encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"] == [{
        "code": "og_image_presence", "expected": True, "actual": "/relative.jpg",
    }]
    artifact.write_text('<html><head><meta property="og:image" '
                        'content="https://cdn.example.com/hero.jpg"></head></html>',
                        encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


@pytest.mark.parametrize("tag", [
    '<meta property="og:image" content="https://cdn.example.com/hero.jpg">',
    '<meta property="og:image:url" content="https://cdn.example.com/hero.jpg">',
    '<meta property="og:image" content=" https://cdn.example.com/hero.jpg ">',
    '<meta property="og:image" content="https://cdn.example.com/hero.jpg">'
    '<meta property="og:image" content="https://cdn.example.com/other.jpg">',
    '<template><meta property="og:image" content="https://cdn.example.com/other.jpg">'
    '</template><meta property="og:image" content="https://cdn.example.com/hero.jpg">',
])
def test_og_image_equals_accepts_first_active_image_url(tag):
    html = f"<html><head>{tag}</head><body></body></html>"
    expected = {"url": URL, "og_image_equals": "https://cdn.example.com/hero.jpg"}
    assert verify_contract(expected, html)["status"] == "pass"


@pytest.mark.parametrize("tag,actual", [
    ("", None),
    ('<meta property="og:image" content="">', ""),
    ('<meta property="og:image" content="/hero.jpg">', "/hero.jpg"),
    ('<meta property="og:image" content="https://cdn.example.com/changed.jpg">',
     "https://cdn.example.com/changed.jpg"),
    ('<meta property="og:image" content="https://cdn.example.com/changed.jpg">'
     '<meta property="og:image:url" content="https://cdn.example.com/hero.jpg">',
     "https://cdn.example.com/changed.jpg"),
    ('<meta name="og:image" content="https://cdn.example.com/hero.jpg">', None),
    ('<template><meta property="og:image" content="https://cdn.example.com/hero.jpg">'
     '</template>', None),
    ('<noscript><meta property="og:image" content="https://cdn.example.com/hero.jpg">'
     '</noscript>', None),
])
def test_og_image_equals_rejects_missing_or_changed_image_url(tag, actual):
    expected_url = "https://cdn.example.com/hero.jpg"
    html = (f"<html><head>{tag}</head>"
            '<body><meta property="og:image" content="https://cdn.example.com/hero.jpg">'
            "</body></html>")
    result = verify_contract({"url": URL, "og_image_equals": expected_url}, html)
    assert result["issues"] == [{
        "code": "og_image_equals", "expected": expected_url, "actual": actual,
    }]


def test_og_image_equals_decodes_html_entities_and_first_duplicate_attribute():
    expected_url = "https://cdn.example.com/hero.jpg?a=1&b=2"
    html = ('<!doctype html><meta property="og:image" '
            'content="https://cdn.example.com/hero.jpg?a=1&amp;b=2" '
            'content="https://cdn.example.com/wrong.jpg"><h1>Page</h1>')
    assert verify_contract({"url": URL, "og_image_equals": expected_url}, html)["status"] == "pass"


@pytest.mark.parametrize("value", [None, False, 1, "", "  ", "/hero.jpg", "//cdn.example.com/a.jpg",
                                    "javascript:alert(1)", "https://bad.example:99999/a.jpg", [], {}])
def test_og_image_equals_rejects_invalid_contract_url(value):
    with pytest.raises(ValueError, match="og_image_equals"):
        verify_contract({"url": URL, "og_image_equals": value}, HTML)


def test_og_image_equals_cli_json_failure_and_pass(tmp_path, capsys):
    expected_url = "https://cdn.example.com/hero.jpg"
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "og_image_equals": expected_url}), encoding="utf-8")
    artifact.write_text('<html><head><meta property="og:image" '
                        'content="https://cdn.example.com/changed.jpg"></head></html>',
                        encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"] == [{
        "code": "og_image_equals", "expected": expected_url,
        "actual": "https://cdn.example.com/changed.jpg",
    }]
    artifact.write_text('<html><head><meta property="og:image" '
                        'content="https://cdn.example.com/hero.jpg"></head></html>', encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


@pytest.mark.parametrize("metadata", [
    ('<meta property="og:image" content="https://cdn.example.com/first.jpg">'
     '<meta property="og:image:alt" content="A blue bicycle">'),
    ('<meta property="og:image:url" content="https://cdn.example.com/first.jpg">'
     '<meta property="og:image:alt" content=" A blue bicycle ">'),
    ('<meta property="og:image" content="https://cdn.example.com/first.jpg">'
     '<meta property="og:image:alt" content="A blue &amp; white bicycle">'),
    ('<meta property="og:image" content="https://cdn.example.com/first.jpg">'
     '<meta property="og:image:alt" content="A blue bicycle">'
     '<meta property="og:image" content="https://cdn.example.com/second.jpg">'),
    ('<template><meta property="og:image:alt" content="Ghost"></template>'
     '<meta property="og:image" content="https://cdn.example.com/first.jpg">'
     '<meta property="og:image:alt" content="A blue bicycle">'),
])
def test_og_image_alt_presence_accepts_first_image_structured_alt(metadata):
    result = verify_contract({"url": URL, "og_image_alt_present": True},
                             f"<html><head>{metadata}</head><body></body></html>")
    assert result["status"] == "pass", result["issues"]


@pytest.mark.parametrize("metadata,actual", [
    ("", None),
    ('<meta property="og:image:alt" content="Orphan">', None),
    ('<meta property="og:image" content="https://cdn.example.com/first.jpg">', None),
    ('<meta property="og:image" content="https://cdn.example.com/first.jpg">'
     '<meta property="og:image:alt">', ""),
    ('<meta property="og:image" content="https://cdn.example.com/first.jpg">'
     '<meta property="og:image:alt" content="  ">', ""),
    ('<meta property="og:image" content="https://cdn.example.com/first.jpg">'
     '<meta property="og:image:alt" content="">'
     '<meta property="og:image:alt" content="Later valid alt">', ""),
    ('<meta property="og:image" content="https://cdn.example.com/first.jpg">'
     '<meta property="og:image" content="https://cdn.example.com/second.jpg">'
     '<meta property="og:image:alt" content="Only second image">', None),
    ('<meta property="og:image" content="https://cdn.example.com/first.jpg">'
     '<template><meta property="og:image:alt" content="Inert"></template>', None),
    ('<meta property="og:image" content="https://cdn.example.com/first.jpg">'
     '<noscript><meta property="og:image:alt" content="Inert"></noscript>', None),
    ('<meta property="og:image" content="https://cdn.example.com/first.jpg">'
     '<meta name="og:image:alt" content="Wrong attribute">', None),
])
def test_og_image_alt_presence_rejects_missing_or_blank_first_alt(metadata, actual):
    html = (f"<html><head>{metadata}</head><body>"
            '<meta property="og:image:alt" content="Body fallback"></body></html>')
    result = verify_contract({"url": URL, "og_image_alt_present": True}, html)
    assert result["issues"] == [{
        "code": "og_image_alt_presence", "expected": True, "actual": actual,
    }]


def test_og_image_alt_presence_implicit_head_and_duplicate_attribute_first_wins():
    html = ('<!doctype html><meta property="og:image" '
            'content="https://cdn.example.com/first.jpg">'
            '<meta property="og:image:alt" content="" content="Later valid alt">'
            '<h1>Visible heading</h1>')
    assert verify_contract({"url": URL, "og_image_alt_present": True}, html)["issues"] == [{
        "code": "og_image_alt_presence", "expected": True, "actual": "",
    }]


@pytest.mark.parametrize("metadata,status,actual", [
    ("", "pass", None),
    ('<meta property="og:image" content="https://cdn.example.com/first.jpg">',
     "pass", None),
    ('<meta property="og:image:alt" content="Orphan">', "pass", None),
    ('<meta property="og:image" content="https://cdn.example.com/first.jpg">'
     '<meta property="og:image:alt" content="Accessible image">',
     "fail", "Accessible image"),
    ('<meta property="og:image" content="https://cdn.example.com/first.jpg">'
     '<meta property="og:image:alt" content="">', "fail", ""),
])
def test_og_image_alt_presence_false_requires_no_first_image_alt(metadata, status, actual):
    result = verify_contract({"url": URL, "og_image_alt_present": False},
                             f"<html><head>{metadata}</head></html>")
    assert result["status"] == status
    if status == "fail":
        assert result["issues"] == [{
            "code": "og_image_alt_presence", "expected": False, "actual": actual,
        }]


@pytest.mark.parametrize("value", [None, 1, "true", "", [], {}])
def test_og_image_alt_presence_rejects_non_boolean_contract_values(value):
    with pytest.raises(ValueError, match="og_image_alt_present"):
        verify_contract({"url": URL, "og_image_alt_present": value}, HTML)


def test_og_image_alt_presence_cli_json_failure_and_pass(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "index.html"
    contract.write_text(json.dumps({"url": URL, "og_image_alt_present": True}), encoding="utf-8")
    artifact.write_text('<html><head><meta property="og:image" '
                        'content="https://cdn.example.com/first.jpg">'
                        '<meta property="og:image:alt" content=""></head></html>',
                        encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"] == [{
        "code": "og_image_alt_presence", "expected": True, "actual": "",
    }]
    artifact.write_text('<html><head><meta property="og:image" '
                        'content="https://cdn.example.com/first.jpg">'
                        '<meta property="og:image:alt" content="A blue bicycle"></head></html>',
                        encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


@pytest.mark.parametrize("root_property", [
    "og:title", "og:description", "og:type", "og:video", "og:locale",
    "article:published_time",
])
def test_og_image_alt_does_not_attach_after_another_root_property(root_property):
    html = ('<html><head><meta property="og:image" '
            'content="https://cdn.example.com/first.jpg">'
            f'<meta property="{root_property}" content="Other root">'
            '<meta property="og:image:alt" content="Misplaced alt"></head></html>')
    result = verify_contract({"url": URL, "og_image_alt_present": True}, html)
    assert result["issues"] == [{
        "code": "og_image_alt_presence", "expected": True, "actual": None,
    }]
    assert verify_contract({"url": URL, "og_image_alt_present": False}, html)["status"] == "pass"


@pytest.mark.parametrize("intermediate", [
    '<meta property="og:image:width" content="1200">',
    '<meta property="og:image:secure_url" content="https://cdn.example.com/first.jpg">',
    '<meta name="description" content="Unrelated HTML metadata">',
    '<template><meta property="og:title" content="Inert root"></template>',
    '<noscript><meta property="og:video" content="Inert root"></noscript>',
    '<meta property="og:image:alt" content="First alt">',
])
def test_og_image_alt_still_attaches_without_intervening_root(intermediate):
    html = ('<html><head><meta property="og:image" '
            'content="https://cdn.example.com/first.jpg">'
            f'{intermediate}'
            '<meta property="og:image:alt" content="Later alt"></head></html>')
    assert verify_contract({"url": URL, "og_image_alt_present": True}, html)["status"] == "pass"


def test_og_image_alt_after_new_root_fails_offline_cli_json(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "page.html"
    contract.write_text(json.dumps({"url": URL, "og_image_alt_present": True}), encoding="utf-8")
    artifact.write_text('<html><head><meta property="og:image" '
                        'content="https://cdn.example.com/first.jpg">'
                        '<meta property="og:title" content="Another root">'
                        '<meta property="og:image:alt" content="Misplaced alt"></head></html>',
                        encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"] == [{
        "code": "og_image_alt_presence", "expected": True, "actual": None,
    }]


@pytest.mark.parametrize("metadata,expected", [
    ("", True),
    ('<meta name="robots" content="all">', True),
    ('<meta name="robots" content="index, follow">', True),
    ('<meta name="robots" content="noindex">', True),
    ('<meta name="robots" content="nofollow">', False),
    ('<meta name="robots" content="NoFoLlOw">', False),
    ('<meta name="robots" content="noindex, nofollow">', False),
    ('<meta name="robots" content="none">', False),
    ('<meta name="googlebot" content="nofollow">', False),
    ('<meta name="robots" content="follow"><meta name="googlebot" content="nofollow">', False),
    ('<meta name="robots" content="nofollow"><meta name="googlebot" content="follow">', False),
    ('<meta name="robots" content="noindex"><meta name="robots" content="nofollow">', False),
    ('<meta name="bingbot" content="nofollow">', True),
    ('<template><meta name="robots" content="nofollow"></template>', True),
    ('<noscript><meta name="robots" content="nofollow"></noscript>', True),
    ('<meta name="robots" content="x-nofollow, notfollow">', True),
])
def test_followable_contract_checks_google_robots_link_follow_policy(metadata, expected):
    html = f"<html><head>{metadata}</head><body><a href='/x'>Link</a></body></html>"
    report = verify_contract({"url": URL, "followable": True}, html)
    assert report["status"] == ("pass" if expected else "fail")
    assert report["issues"] == ([] if expected else [{
        "code": "followability_meta", "expected": True, "actual": False,
    }])


def test_followable_contract_honors_body_robots_but_not_inert_or_name_only_tags():
    html = ('<html><head></head><body>'
            '<template><meta name="robots" content="nofollow"></template>'
            '<meta name="robots" content="nofollow">'
            '<meta name="x-robots-tag" content="follow">'
            '</body></html>')
    report = verify_contract({"url": URL, "followable": True, "indexable": True}, html)
    assert report["issues"] == [{
        "code": "followability_meta", "expected": True, "actual": False,
    }]
    assert verify_contract({"url": URL, "followable": False}, html)["status"] == "pass"


@pytest.mark.parametrize("value", [None, 0, 1, "false", "", [], {}])
def test_followable_contract_rejects_non_boolean_values(value):
    with pytest.raises(ValueError, match="followable"):
        verify_contract({"url": URL, "followable": value}, HTML)


@pytest.mark.parametrize("restriction", ["nofollow", "none"])
def test_followable_false_requires_active_restriction(restriction):
    html = f'<html><head><meta name="robots" content="{restriction}"></head></html>'
    assert verify_contract({"url": URL, "followable": False}, html)["status"] == "pass"
    assert verify_contract({"url": URL, "followable": False}, HTML)["issues"] == [{
        "code": "followability_meta", "expected": False, "actual": True,
    }]


def test_followable_cli_json_reports_failure_and_pass(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "page.html"
    contract.write_text(json.dumps({"url": URL, "followable": True}), encoding="utf-8")
    artifact.write_text('<html><head><meta name="robots" content="nofollow"></head></html>',
                        encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"] == [{
        "code": "followability_meta", "expected": True, "actual": False,
    }]
    artifact.write_text('<html><head><meta name="robots" content="noindex"></head></html>',
                        encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


@pytest.mark.parametrize("card_type", ["summary", "summary_large_image", "app", "player"])
def test_twitter_card_equals_accepts_explicit_first_head_card_type(card_type):
    html = ('<html><head><meta NAME="TWITTER:CARD" '
            f'content=" {card_type} "></head><body></body></html>')
    result = verify_contract({"url": URL, "twitter_card_equals": card_type}, html)
    assert result["status"] == "pass", result["issues"]


@pytest.mark.parametrize("metadata,actual", [
    ('<meta name="twitter:card" content="summary">', "summary"),
    ('<meta name="twitter:card">', ""),
    ('<meta name="twitter:card" content="">'
     '<meta name="twitter:card" content="summary_large_image">', ""),
    ('<meta name="twitter:card" content="summary" content="summary_large_image">', "summary"),
    ('<meta property="twitter:card" content="summary_large_image">', None),
    ('<template><meta name="twitter:card" content="summary_large_image"></template>', None),
    ('<noscript><meta name="twitter:card" content="summary_large_image"></noscript>', None),
    ("", None),
])
def test_twitter_card_equals_rejects_missing_or_changed_first_card_type(metadata, actual):
    html = (f"<html><head>{metadata}</head><body>"
            '<meta name="twitter:card" content="summary_large_image"></body></html>')
    result = verify_contract({"url": URL, "twitter_card_equals": "summary_large_image"}, html)
    assert result["issues"] == [{
        "code": "twitter_card_equals", "expected": "summary_large_image", "actual": actual,
    }]


def test_twitter_card_equals_implicit_head_ignores_inert_and_later_duplicates():
    html = ('<!doctype html><template><meta name="twitter:card" content="summary">'
            '</template><meta name="twitter:card" content="summary_large_image">'
            '<meta name="twitter:card" content="summary"><h1>Page</h1>')
    assert verify_contract(
        {"url": URL, "twitter_card_equals": "summary_large_image"}, html,
    )["status"] == "pass"


@pytest.mark.parametrize("value", [None, False, 1, "", "  ", [], {},
                                   "summary-large-image", "SUMMARY", "unknown"])
def test_twitter_card_equals_rejects_invalid_expected_type(value):
    with pytest.raises(ValueError, match="twitter_card_equals"):
        verify_contract({"url": URL, "twitter_card_equals": value}, HTML)


def test_twitter_card_equals_cli_json_reports_regression_and_pass(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "page.html"
    contract.write_text(json.dumps({"url": URL, "twitter_card_equals": "summary_large_image"}),
                        encoding="utf-8")
    artifact.write_text('<html><head><meta name="twitter:card" content="summary"></head></html>',
                        encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"] == [{
        "code": "twitter_card_equals", "expected": "summary_large_image", "actual": "summary",
    }]
    artifact.write_text('<html><head><meta name="twitter:card" '
                        'content="summary_large_image"></head></html>', encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


@pytest.mark.parametrize("metadata,expected", [
    ('<meta name="twitter:title" content="Agency SEO release">', "Agency SEO release"),
    ('<meta NAME="TWITTER:TITLE" content="  Agency   SEO release  ">', "Agency SEO release"),
    ('<meta name="twitter:title" content="Tom &amp; Jerry">', "Tom & Jerry"),
])
def test_twitter_title_equals_matches_first_head_preview_title(metadata, expected):
    html = f"<html><head>{metadata}</head><body><h1>Other title</h1></body></html>"
    result = verify_contract({"url": URL, "twitter_title_equals": expected}, html)
    assert result["status"] == "pass", result["issues"]


@pytest.mark.parametrize("metadata,actual", [
    ('<meta name="twitter:title" content="Old title">', "Old title"),
    ('<meta name="twitter:title">', ""),
    ('<meta name="twitter:title" content="">'
     '<meta name="twitter:title" content="Agency SEO release">', ""),
    ('<meta name="twitter:title" content="Old" content="Agency SEO release">', "Old"),
    ('<meta property="twitter:title" content="Agency SEO release">', ""),
    ('<meta property="og:title" content="Agency SEO release">', ""),
    ('<template><meta name="twitter:title" content="Agency SEO release"></template>', ""),
    ('<noscript><meta name="twitter:title" content="Agency SEO release"></noscript>', ""),
    ("", ""),
])
def test_twitter_title_equals_catches_missing_changed_or_inert_metadata(metadata, actual):
    html = (f"<html><head>{metadata}</head><body>"
            '<meta name="twitter:title" content="Agency SEO release"></body></html>')
    result = verify_contract({"url": URL, "twitter_title_equals": "Agency SEO release"}, html)
    assert result["issues"] == [{
        "code": "twitter_title_equals", "expected": "Agency SEO release", "actual": actual,
    }]


def test_twitter_title_equals_implicit_head_ignores_inert_and_duplicate_tags():
    html = ('<!doctype html><template><meta name="twitter:title" content="Ghost"></template>'
            '<meta name="twitter:title" content="Agency SEO release">'
            '<meta name="twitter:title" content="Later"><h1>Page</h1>')
    assert verify_contract(
        {"url": URL, "twitter_title_equals": "Agency SEO release"}, html,
    )["status"] == "pass"


@pytest.mark.parametrize("value", [None, False, 1, "", " \t ", [], {}])
def test_twitter_title_equals_rejects_invalid_expected_text(value):
    with pytest.raises(ValueError, match="twitter_title_equals"):
        verify_contract({"url": URL, "twitter_title_equals": value}, HTML)


def test_twitter_title_equals_cli_json_reports_failure_and_pass(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "page.html"
    contract.write_text(json.dumps({"url": URL, "twitter_title_equals": "New campaign"}), encoding="utf-8")
    artifact.write_text('<html><head><meta name="twitter:title" content="Old campaign"></head></html>',
                        encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"] == [{
        "code": "twitter_title_equals", "expected": "New campaign", "actual": "Old campaign",
    }]
    artifact.write_text('<html><head><meta name="twitter:title" content="New campaign"></head></html>',
                        encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


@pytest.mark.parametrize("metadata,expected", [
    ('<meta name="twitter:description" content="Local SEO release checks">',
     "Local SEO release checks"),
    ('<meta NAME="TWITTER:DESCRIPTION" content="  Local   SEO release checks  ">',
     "Local SEO release checks"),
    ('<meta name="twitter:description" content="R&amp;D update">', "R&D update"),
])
def test_twitter_description_equals_matches_explicit_first_head_summary(metadata, expected):
    html = (f"<html><head>{metadata}"
            '<meta property="og:description" content="Other social summary">'
            '</head><body><p>Unrelated content</p></body></html>')
    result = verify_contract({"url": URL, "twitter_description_equals": expected}, html)
    assert result["status"] == "pass", result["issues"]


@pytest.mark.parametrize("metadata,actual", [
    ('<meta name="twitter:description" content="Outdated summary">', "Outdated summary"),
    ('<meta name="twitter:description">', ""),
    ('<meta name="twitter:description" content="">'
     '<meta name="twitter:description" content="New summary">', ""),
    ('<meta name="twitter:description" content="Old" content="New summary">', "Old"),
    ('<meta property="twitter:description" content="New summary">', ""),
    ('<meta property="og:description" content="New summary">', ""),
    ('<meta name="description" content="New summary">', ""),
    ('<template><meta name="twitter:description" content="New summary"></template>', ""),
    ('<noscript><meta name="twitter:description" content="New summary"></noscript>', ""),
    ("", ""),
])
def test_twitter_description_equals_detects_missing_changed_or_inert_metadata(metadata, actual):
    html = (f"<html><head>{metadata}</head><body>"
            '<meta name="twitter:description" content="New summary"></body></html>')
    result = verify_contract({"url": URL, "twitter_description_equals": "New summary"}, html)
    assert result["issues"] == [{
        "code": "twitter_description_equals", "expected": "New summary", "actual": actual,
    }]


def test_twitter_description_equals_implicit_head_first_metadata_wins():
    html = ('<!doctype html><template><meta name="twitter:description" content="Ghost"></template>'
            '<meta name="twitter:description" content="New summary">'
            '<meta name="twitter:description" content="Later"><h1>Page</h1>')
    assert verify_contract(
        {"url": URL, "twitter_description_equals": "New summary"}, html,
    )["status"] == "pass"


@pytest.mark.parametrize("value", [None, False, 1, "", " \t ", [], {}])
def test_twitter_description_equals_rejects_invalid_expected_text(value):
    with pytest.raises(ValueError, match="twitter_description_equals"):
        verify_contract({"url": URL, "twitter_description_equals": value}, HTML)


def test_twitter_description_equals_cli_json_reports_failure_then_pass(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    artifact = tmp_path / "page.html"
    contract.write_text(json.dumps({"url": URL, "twitter_description_equals": "New campaign"}),
                        encoding="utf-8")
    artifact.write_text('<html><head><meta name="twitter:description" '
                        'content="Old campaign"></head></html>', encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"] == [{
        "code": "twitter_description_equals", "expected": "New campaign", "actual": "Old campaign",
    }]
    artifact.write_text('<html><head><meta name="twitter:description" '
                        'content="New campaign"></head></html>', encoding="utf-8")
    assert main([str(contract), str(artifact), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"
