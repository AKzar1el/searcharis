"""Offline approval guard for adding pages to reviewed SEO manifests."""

from __future__ import annotations

import importlib
import json
import socket
from pathlib import Path

import pytest


def _reviewed(path: str, url: str, **assertions: object) -> dict:
    return {"path": path, "contract": {"url": url, "title_present": True, **assertions}}


def _fixtures(tmp_path: Path) -> tuple[Path, Path, Path, dict, dict]:
    build = tmp_path / "dist"
    (build / "pricing").mkdir(parents=True)
    (build / "index.html").write_text("<title>Home</title>", encoding="utf-8")
    (build / "pricing" / "index.html").write_text("<title>Pricing</title>", encoding="utf-8")
    old = _reviewed("index.html", "https://example.com/", h1_min=1)
    added = _reviewed("pricing/index.html", "https://example.com/pricing/")
    baseline = tmp_path / "approved.json"
    proposed = tmp_path / "proposed.json"
    baseline.write_text(json.dumps({"version": 1, "pages": [old]}), encoding="utf-8")
    proposed.write_text(json.dumps({"version": 1, "pages": [old, added]}), encoding="utf-8")
    return build, baseline, proposed, old, added


def _main(*paths: Path) -> int:
    main = importlib.import_module("searcharis.manifest_review_cli").main
    return main([str(path) for path in paths])


def test_append_only_manifest_review_passes_without_fetching_urls(tmp_path, monkeypatch, capsys):
    build, baseline, proposed, _, _ = _fixtures(tmp_path)

    def reject_network(*args, **kwargs):
        raise AssertionError("The approval guard must not fetch contract URLs")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    assert _main(baseline, proposed, build) == 0
    output = capsys.readouterr().out
    assert "PASS" in output
    assert "1 new" in output
    assert "pricing/index.html" in output
    assert "manual review" in output.lower()


def test_new_page_review_queue_is_bounded_and_keeps_all_pages_unapproved(tmp_path, capsys):
    build, baseline, proposed, old, _ = _fixtures(tmp_path)
    added = []
    for number in range(12):
        path = f"new-{number:02d}.html"
        (build / path).write_text("<title>Candidate</title>", encoding="utf-8")
        added.append(_reviewed(path, f"https://example.com/new-{number:02d}"))
    proposed.write_text(json.dumps({"version": 1, "pages": [old, *added]}), encoding="utf-8")

    assert _main(baseline, proposed, build) == 0
    output = capsys.readouterr().out
    assert "12 new pages appended" in output
    assert "new-00.html" in output
    assert "new-09.html" in output
    assert "new-10.html" not in output
    assert "2 more" in output
    assert "manual review" in output.lower()


@pytest.mark.parametrize("change", ["weaken", "remove", "reorder", "insert-before"])
def test_guard_blocks_mutated_removed_or_reordered_approved_pages(
    tmp_path, capsys, change
):
    build, baseline, proposed, old, added = _fixtures(tmp_path)
    approved_pages = [old, _reviewed("pricing/index.html", "https://example.com/pricing/")]
    baseline.write_text(json.dumps({"version": 1, "pages": approved_pages}), encoding="utf-8")
    if change == "weaken":
        changed = [dict(old, contract={"url": old["contract"]["url"], "title_present": True}), added]
    elif change == "remove":
        changed = [old]
    elif change == "reorder":
        changed = [added, old]
    else:
        changed = [old, _reviewed("extra.html", "https://example.com/extra/"), added]
        (build / "extra.html").write_text("<title>Extra</title>", encoding="utf-8")
    proposed.write_text(json.dumps({"version": 1, "pages": changed}), encoding="utf-8")
    assert _main(baseline, proposed, build) == 1
    assert "FAIL" in capsys.readouterr().out


def test_guard_accepts_identical_review_without_additions(tmp_path, capsys):
    build, baseline, proposed, old, _ = _fixtures(tmp_path)
    proposed.write_text(json.dumps({"version": 1, "pages": [old]}), encoding="utf-8")
    assert _main(baseline, proposed, build) == 0
    output = capsys.readouterr().out
    assert "0 new" in output
    assert "manual review" not in output.lower()


@pytest.mark.parametrize("invalid", ["duplicate-json-key", "unknown-file", "missing-input"])
def test_guard_rejects_invalid_inputs_without_success_report(tmp_path, capsys, invalid):
    build, baseline, proposed, _, _ = _fixtures(tmp_path)
    if invalid == "duplicate-json-key":
        proposed.write_text('{"version":1,"version":1,"pages":[]}', encoding="utf-8")
    elif invalid == "unknown-file":
        proposed.write_text(
            json.dumps({"version": 1, "pages": [
                _reviewed("missing.html", "https://example.com/missing/")
            ]}),
            encoding="utf-8",
        )
    else:
        proposed = tmp_path / "not-there.json"
    assert _main(baseline, proposed, build) == 2
    captured = capsys.readouterr()
    assert "Invalid input" in captured.err
    assert "PASS" not in captured.out
