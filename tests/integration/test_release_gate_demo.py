"""Keep the checked-in compiled Vite + sitemap release example runnable."""

import json
import shutil
import socket
from pathlib import Path

from searcharis.release_cli import main as release_gate

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "examples" / "seo-contract" / "release-demo"
BUILD = ROOT / "examples" / "seo-contract" / "generator-fixtures" / "vite" / "artifacts"
STRICT = ("--require-full-coverage", "--require-indexability-coverage")


def _offline(monkeypatch):
    def deny(*_args, **_kwargs):
        raise AssertionError("Offline release demo attempted to use the network")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)


def _check(build, sitemap, *options):
    return release_gate([
        str(DEMO / "manifest.json"), str(build), str(sitemap),
        *STRICT, "--check-canonical-consistency", *options,
    ])


def test_compiled_vite_release_demo_passes_both_strict_gates(capsys, monkeypatch):
    _offline(monkeypatch)
    assert _check(BUILD, DEMO / "sitemap.xml", "--json") == 0
    output = capsys.readouterr()
    assert output.err == ""
    report = json.loads(output.out)
    assert report["status"] == "pass"
    assert report["site"]["summary"] == {"checked": 2, "passed": 2, "failed": 0}
    assert report["site"]["coverage"]["unlisted"] == []
    assert report["sitemap"]["summary"] == {
        "required": 2, "excluded": 0, "unasserted_pages": 0, "sitemap_urls": 2,
    }


def test_release_demo_blocks_built_html_noindex_regression(
    tmp_path, capsys, monkeypatch,
):
    _offline(monkeypatch)
    candidate = tmp_path / "dist"
    shutil.copytree(BUILD, candidate)
    page = candidate / "pricing" / "index.html"
    html = page.read_text(encoding="utf-8")
    assert 'content="index,follow"' in html
    page.write_text(
        html.replace('content="index,follow"', 'content="noindex"'),
        encoding="utf-8",
    )
    assert _check(candidate, DEMO / "sitemap.xml", "--json") == 1
    report = json.loads(capsys.readouterr().out)
    assert report["site"]["status"] == "fail"
    assert report["sitemap"]["status"] == "pass"
    assert report["site"]["summary"]["failed"] == 1


def test_release_demo_blocks_missing_sitemap_url(tmp_path, capsys, monkeypatch):
    _offline(monkeypatch)
    shard = tmp_path / "sitemap.xml"
    xml = (DEMO / "sitemap.xml").read_text(encoding="utf-8")
    assert "<loc>https://fixtures.invalid/pricing/</loc>" in xml
    shard.write_text(
        xml.replace(
            "<url><loc>https://fixtures.invalid/pricing/</loc></url>", ""
        ),
        encoding="utf-8",
    )
    assert _check(BUILD, shard, "--json") == 1
    report = json.loads(capsys.readouterr().out)
    assert report["site"]["status"] == "pass"
    assert report["sitemap"]["status"] == "fail"
    assert report["sitemap"]["missing_required"] == [
        "https://fixtures.invalid/pricing/"
    ]


def test_release_demo_portable_json_file_is_create_only(
    tmp_path, capsys, monkeypatch,
):
    _offline(monkeypatch)
    target = tmp_path / "seo-release.json"
    assert _check(BUILD, DEMO / "sitemap.xml", "--json-file", str(target)) == 0
    assert capsys.readouterr().out == ""
    payload = target.read_bytes()
    assert b"\x00" not in payload
    assert json.loads(payload.decode("utf-8"))["status"] == "pass"
    assert _check(BUILD, DEMO / "sitemap.xml", "--json-file", str(target)) == 2
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "pass"
