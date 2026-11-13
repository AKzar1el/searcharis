"""Opt-in review of navigational links in a fully reviewed offline build."""

import json
import socket
from unittest.mock import patch

from searcharis.release_cli import main as release_main
from searcharis.site_ci import verify_manifest
from searcharis.site_cli import main as site_main


def _site(tmp_path, home_links: str = '', *, head_extra: str = ''):
    build = tmp_path / 'dist'
    for directory, name, links in (
        ('en', 'Home', home_links),
        ('about', 'About', '<a href="/en/">Home</a>'),
    ):
        artifact = build / directory / 'index.html'
        artifact.parent.mkdir(parents=True)
        artifact.write_text(
            f'<html><head><title>{name}</title>'
            f'{head_extra if directory == "en" else ""}'
            f'</head><body><h1>{name}</h1>{links}</body></html>',
            encoding='utf-8',
        )
    manifest = {'version': 1, 'pages': [
        {'path': f'{directory}/index.html', 'contract': {
            'url': f'https://example.test/{directory}/',
            'title_present': True, 'indexable': True,
        }} for directory in ('en', 'about')
    ]}
    return build, manifest


def test_navigation_opt_in_catches_unreviewed_local_links_but_default_stays_unchanged(tmp_path):
    build, manifest = _site(tmp_path, '<a href="/missing/#section">Missing</a>')
    assert verify_manifest(manifest, build)['status'] == 'pass'
    result = verify_manifest(manifest, build, check_reviewed_navigation=True)
    assert result['status'] == 'fail'
    assert result['summary']['failed'] == 1
    assert result['pages'][1]['issues'] == [{
        'code': 'navigation_target_unreviewed',
        'expected': 'a URL listed in the reviewed manifest',
        'actual': 'https://example.test/missing/',
    }]


def test_navigation_accepts_relative_and_normalized_local_links(tmp_path):
    build, manifest = _site(tmp_path,
        '<a href="../about/#team">About</a>'
        '<a href="https://EXAMPLE.test:443/about/">About again</a>'
        '<a href="?">Self</a><a href="#top">Jump</a>'
        '<a href="mailto:hello@example.test">Email</a>'
        '<a href="https://other.test/out">External</a>'
        '<a href="http://example.test/about/">Different scheme</a>')
    result = verify_manifest(manifest, build, check_reviewed_navigation=True)
    assert result['status'] == 'pass'
    assert result['reviewed_navigation']['checked_links'] >= 2


def test_navigation_ignores_inert_markup_and_deduplicates_missing_targets(tmp_path):
    build, manifest = _site(tmp_path,
        '<template><a href="/ghost/">Ghost</a></template>'
        '<script>"<a href=\'/ghost/\'>"</script>'
        '<textarea><a href="/ghost/">Ghost</a></textarea>'
        '<a href="/missing/">One</a><a href="/missing/#twice">Two</a>')
    result = verify_manifest(manifest, build, check_reviewed_navigation=True)
    assert result['status'] == 'fail'
    issues = result['pages'][1]['issues']
    assert len(issues) == 1
    assert issues[0]['actual'] == 'https://example.test/missing/'


def test_navigation_resolves_first_document_base_and_preserves_query(tmp_path):
    build, manifest = _site(tmp_path,
        '<a href="../about/">About</a><a href="?sort=popular">Query</a>',
        head_extra='<base href="https://example.test/docs/">')
    result = verify_manifest(manifest, build, check_reviewed_navigation=True)
    assert result['status'] == 'fail'
    assert [i['actual'] for i in result['pages'][1]['issues']] == [
        'https://example.test/docs/?sort=popular',
    ]


def test_site_cli_navigation_option_fails_offline_without_network(tmp_path, capsys):
    build, manifest = _site(tmp_path, '<a href="/stale/">Stale</a>')
    manifest_file = tmp_path / 'manifest.json'
    manifest_file.write_text(json.dumps(manifest), encoding='utf-8')
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('network attempted')):
        assert site_main([str(manifest_file), str(build), '--json']) == 0
        capsys.readouterr()
        assert site_main([str(manifest_file), str(build),
                          '--check-reviewed-navigation', '--json']) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['reviewed_navigation']['unreviewed_targets'][0]['target'] == (
        'https://example.test/stale/'
    )


def test_release_cli_navigation_option_blocks_when_sitemap_is_valid(tmp_path, capsys):
    build, manifest = _site(tmp_path, '<a href="/stale/">Stale</a>')
    manifest_file = tmp_path / 'manifest.json'
    manifest_file.write_text(json.dumps(manifest), encoding='utf-8')
    sitemap = tmp_path / 'sitemap.xml'
    sitemap.write_text('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                       '<url><loc>https://example.test/en/</loc></url>'
                       '<url><loc>https://example.test/about/</loc></url>'
                       '</urlset>', encoding='utf-8')
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('network attempted')):
        assert release_main([str(manifest_file), str(build), str(sitemap), '--json']) == 0
        capsys.readouterr()
        assert release_main([str(manifest_file), str(build), str(sitemap),
                             '--check-reviewed-navigation', '--json']) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['site']['status'] == 'fail'
    assert result['sitemap']['status'] == 'pass'


def test_reviewed_fragments_opt_in_catches_broken_same_and_cross_page_jumps(tmp_path):
    build, manifest = _site(
        tmp_path, '<a href="#missing">Jump</a><a href="/about/#team">Team</a>',
    )
    assert verify_manifest(manifest, build)['status'] == 'pass'
    assert verify_manifest(manifest, build, check_reviewed_navigation=True)['status'] == 'pass'
    result = verify_manifest(manifest, build, check_reviewed_fragments=True)
    assert result['status'] == 'fail'
    assert result['reviewed_fragments']['checked_fragments'] == 2
    assert result['reviewed_fragments']['missing_targets'] == [
        {'path': 'en/index.html', 'target': 'https://example.test/about/#team'},
        {'path': 'en/index.html', 'target': 'https://example.test/en/#missing'},
    ]
    assert [issue['code'] for issue in result['pages'][1]['issues']] == [
        'navigation_fragment_missing', 'navigation_fragment_missing',
    ]


def test_reviewed_fragments_accept_id_legacy_name_encoded_fragment_and_base(tmp_path):
    build, manifest = _site(
        tmp_path,
        '<a href="#faq%20%26%20docs">FAQ</a>'
        '<a href="#legacy">Legacy</a>'
        '<a href="#team">Team</a><a href="#raw%20id">Raw encoded ID</a>',
        head_extra='<base href="https://example.test/about/">',
    )
    about = build / 'about' / 'index.html'
    about.write_text(
        about.read_text(encoding='utf-8').replace(
            '<h1>About</h1>',
            '<h1>About</h1><h2 id="team">Team</h2>'
            '<div id="faq &amp; docs"></div><a name="legacy"></a>'
            '<div id="raw%20id"></div>',
        ), encoding='utf-8',
    )
    result = verify_manifest(manifest, build, check_reviewed_fragments=True)
    assert result['status'] == 'pass'
    assert result['reviewed_fragments']['checked_fragments'] == 4
    assert result['reviewed_fragments']['missing_targets'] == []


def test_reviewed_fragments_ignore_inert_ids_and_unreviewed_routes(tmp_path):
    build, manifest = _site(
        tmp_path, '<template><div id="ghost"></div></template>'
        '<script>"<div id=\'ghost\'></div>"</script>'
        '<textarea><div id="ghost"></div></textarea>'
        '<a href="#ghost">Broken</a><a href="#ghost">Duplicate</a>'
        '<a href="#top">Top</a><a href="#">Empty fragment</a>'
        '<a href="#not-here">Broken</a>'
        '<a href="#:~:text=Sample">Browser text fragment</a>'
        '<a href="/unreviewed/#external">Unknown route</a>'
        '<a href="https://elsewhere.test/page/#missing">External</a>',
    )
    result = verify_manifest(manifest, build, check_reviewed_fragments=True)
    assert result['status'] == 'fail'
    assert result['reviewed_fragments']['checked_fragments'] == 3
    assert result['reviewed_fragments']['missing_targets'] == [
        {'path': 'en/index.html', 'target': 'https://example.test/en/#ghost'},
        {'path': 'en/index.html', 'target': 'https://example.test/en/#not-here'},
    ]
    assert result['reviewed_fragments']['unverified_targets'] == [
        {'path': 'en/index.html', 'target': 'https://example.test/unreviewed/#external'},
    ]


def test_reviewed_fragment_flag_is_available_in_both_offline_clis(tmp_path, capsys):
    build, manifest = _site(tmp_path, '<a href="#missing">Broken</a>')
    manifest_file = tmp_path / 'manifest.json'
    manifest_file.write_text(json.dumps(manifest), encoding='utf-8')
    sitemap = tmp_path / 'sitemap.xml'
    sitemap.write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://example.test/en/</loc></url>'
        '<url><loc>https://example.test/about/</loc></url></urlset>',
        encoding='utf-8',
    )
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('network attempted')):
        assert site_main([str(manifest_file), str(build),
                          '--check-reviewed-fragments', '--json']) == 1
        site_report = json.loads(capsys.readouterr().out)
        assert site_report['reviewed_fragments']['missing_targets']
        assert release_main([str(manifest_file), str(build), str(sitemap),
                             '--check-reviewed-fragments', '--json']) == 1
        combined = json.loads(capsys.readouterr().out)
    assert combined['site']['status'] == 'fail'
    assert combined['sitemap']['status'] == 'pass'


def test_reviewed_fragments_reject_ambiguous_active_same_page_ids(tmp_path):
    build, manifest = _site(tmp_path,
        '<a href="#faq">FAQ</a><h2 id="faq">First</h2><section id="faq">Second</section>')
    assert verify_manifest(manifest, build)['status'] == 'pass'
    assert verify_manifest(manifest, build, check_reviewed_navigation=True)['status'] == 'pass'
    report = verify_manifest(manifest, build, check_reviewed_fragments=True)
    assert report['status'] == 'fail'
    assert [issue['code'] for issue in report['pages'][1]['issues']] == [
        'navigation_fragment_ambiguous',
    ]
    assert report['reviewed_fragments']['ambiguous_targets'] == [
        {'path': 'en/index.html', 'target': 'https://example.test/en/#faq'},
    ]


def test_reviewed_fragments_reject_cross_page_duplicate_named_anchors(tmp_path):
    build, manifest = _site(tmp_path, '<a href="/about/#team">Team</a>')
    page = build / 'about' / 'index.html'
    page.write_text(page.read_text(encoding='utf-8').replace(
        '<h1>About</h1>', '<h1>About</h1><a name="team"></a><a name="team"></a>',
    ), encoding='utf-8')
    report = verify_manifest(manifest, build, check_reviewed_fragments=True)
    assert report['status'] == 'fail'
    assert report['reviewed_fragments']['ambiguous_targets'] == [
        {'path': 'en/index.html', 'target': 'https://example.test/about/#team'},
    ]


def test_reviewed_fragments_same_element_id_and_name_is_one_target(tmp_path):
    build, manifest = _site(tmp_path,
        '<a href="#faq">Jump</a><a id="faq" name="faq">Answer</a>'
        '<template><div id="faq"></div></template>')
    report = verify_manifest(manifest, build, check_reviewed_fragments=True)
    assert report['status'] == 'pass'
    assert report['reviewed_fragments']['ambiguous_targets'] == []


def test_reviewed_fragments_raw_fragment_id_takes_precedence_over_decoded(tmp_path):
    build, manifest = _site(tmp_path,
        '<a href="#raw%20id">Jump</a><div id="raw%20id"></div>'
        '<div id="raw id"></div><span id="raw id"></span>')
    report = verify_manifest(manifest, build, check_reviewed_fragments=True)
    assert report['status'] == 'pass'
    assert report['reviewed_fragments']['ambiguous_targets'] == []


def test_reviewed_fragments_only_blocks_duplicate_ids_used_as_jump_targets(tmp_path):
    build, manifest = _site(tmp_path,
        '<div id="unused"></div><section id="unused"></section>'
        '<a href="#safe">Safe jump</a><div id="safe"></div>')
    report = verify_manifest(manifest, build, check_reviewed_fragments=True)
    assert report['status'] == 'pass'
    assert report['reviewed_fragments']['checked_fragments'] == 1
    assert report['reviewed_fragments']['ambiguous_targets'] == []


def test_reviewed_fragments_duplicate_raw_id_is_ambiguous_even_with_decoded_fallback(tmp_path):
    build, manifest = _site(tmp_path,
        '<a href="#raw%20id">Jump</a><div id="raw%20id"></div>'
        '<section id="raw%20id"></section><div id="raw id"></div>')
    report = verify_manifest(manifest, build, check_reviewed_fragments=True)
    assert report['status'] == 'fail'
    assert [issue['code'] for issue in report['pages'][1]['issues']] == [
        'navigation_fragment_ambiguous',
    ]


def test_reviewed_fragment_ambiguity_reaches_both_offline_clis(tmp_path, capsys):
    build, manifest = _site(tmp_path,
        '<a href="#duplicate">Jump</a><div id="duplicate"></div>'
        '<span id="duplicate"></span>')
    manifest_file = tmp_path / 'manifest.json'
    manifest_file.write_text(json.dumps(manifest), encoding='utf-8')
    sitemap = tmp_path / 'sitemap.xml'
    sitemap.write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://example.test/en/</loc></url>'
        '<url><loc>https://example.test/about/</loc></url></urlset>',
        encoding='utf-8',
    )
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('network attempted')):
        assert site_main([str(manifest_file), str(build),
                          '--check-reviewed-fragments', '--json']) == 1
        site_report = json.loads(capsys.readouterr().out)
        assert site_report['pages'][1]['issues'][0]['code'] == 'navigation_fragment_ambiguous'
        assert release_main([str(manifest_file), str(build), str(sitemap),
                             '--check-reviewed-fragments', '--json']) == 1
        combined = json.loads(capsys.readouterr().out)
    assert combined['site']['reviewed_fragments']['ambiguous_targets']
    assert combined['sitemap']['status'] == 'pass'
