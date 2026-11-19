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


def test_structured_review_reports_every_unreviewed_page_without_approving_it(
    tmp_path, capsys, monkeypatch
):
    build, baseline, proposed, old, _ = _fixtures(tmp_path)
    additions = []
    for number in range(12):
        path = f"next-{number:02d}.html"
        (build / path).write_text("<title>Next</title>", encoding="utf-8")
        additions.append(_reviewed(path, f"https://example.com/next-{number:02d}"))
    proposed.write_text(json.dumps({"version": 1, "pages": [old, *additions]}), encoding="utf-8")

    def reject_network(*args, **kwargs):
        raise AssertionError("Offline review JSON must never fetch public URLs")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    main = importlib.import_module("searcharis.manifest_review_cli").main
    assert main([str(baseline), str(proposed), str(build), "--json"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "status": "pass",
        "approved_pages": 1,
        "new_pages": 12,
        "review_required": True,
        "unreviewed_paths": [entry["path"] for entry in additions],
    }


def test_structured_review_reports_no_new_approvals(tmp_path, capsys):
    build, baseline, proposed, old, _ = _fixtures(tmp_path)
    proposed.write_text(json.dumps({"version": 1, "pages": [old]}), encoding="utf-8")
    main = importlib.import_module("searcharis.manifest_review_cli").main
    assert main([str(baseline), str(proposed), str(build), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "status": "pass",
        "approved_pages": 1,
        "new_pages": 0,
        "review_required": False,
        "unreviewed_paths": [],
    }


def test_structured_review_preserves_rejection_and_input_error_exit_codes(tmp_path, capsys):
    build, baseline, proposed, old, added = _fixtures(tmp_path)
    main = importlib.import_module("searcharis.manifest_review_cli").main

    weakened = dict(old, contract={**old["contract"], "h1_min": 2})
    proposed.write_text(
        json.dumps({"version": 1, "pages": [weakened, added]}), encoding="utf-8"
    )
    assert main([str(baseline), str(proposed), str(build), "--json"]) == 1
    result = capsys.readouterr()
    assert result.err == ""
    blocked = json.loads(result.out)
    assert blocked["status"] == "fail"
    assert "approved SEO assertions changed" in blocked["reason"]

    proposed.write_text('{"version": 1, "version": 1, "pages": []}', encoding="utf-8")
    assert main([str(baseline), str(proposed), str(build), "--json"]) == 2
    result = capsys.readouterr()
    assert result.err == ""
    rejected = json.loads(result.out)
    assert rejected["status"] == "error"
    assert "Duplicate manifest JSON key" in rejected["error"]


@pytest.mark.parametrize("weakened", [False, True])
def test_manifest_review_json_file_is_portable_ci_artifact(tmp_path, capsys, monkeypatch, weakened):
    build, baseline, proposed, old, added = _fixtures(tmp_path)
    if weakened:
        changed = dict(old, contract={**old["contract"], "h1_min": 2})
        proposed.write_text(
            json.dumps({"version": 1, "pages": [changed, added]}), encoding="utf-8"
        )

    def reject_network(*args, **kwargs):
        raise AssertionError("Manifest approval artifacts must not fetch contract URLs")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    main = importlib.import_module("searcharis.manifest_review_cli").main
    target = tmp_path / "review.json"
    code = main([str(baseline), str(proposed), str(build), "--json-file", str(target)])
    output = capsys.readouterr()
    assert code == (1 if weakened else 0)
    assert output.out == output.err == ""
    raw = target.read_bytes()
    assert raw.startswith(b"{") and b"\x00" not in raw and not raw.startswith(b"\xef\xbb\xbf")
    report = json.loads(raw.decode("utf-8"))
    assert report["status"] == ("fail" if weakened else "pass")
    if weakened:
        assert "approved SEO assertions changed" in report["reason"]
    else:
        assert report["review_required"] is True
        assert report["unreviewed_paths"] == [added["path"]]


def test_manifest_review_json_file_rejects_existing_and_invalid_input(tmp_path, capsys):
    build, baseline, proposed, _, _ = _fixtures(tmp_path)
    main = importlib.import_module("searcharis.manifest_review_cli").main
    target = tmp_path / "review.json"
    target.write_text("keep existing", encoding="utf-8")
    assert main([str(baseline), str(proposed), str(build), "--json-file", str(target)]) == 2
    assert target.read_text(encoding="utf-8") == "keep existing"
    assert "Invalid input" in capsys.readouterr().err
    target.unlink()
    proposed.write_text('{"version": 1, "version": 1, "pages": []}', encoding="utf-8")
    assert main([str(baseline), str(proposed), str(build), "--json-file", str(target)]) == 2
    assert not target.exists()
    assert "Invalid input" in capsys.readouterr().err


@pytest.mark.parametrize("destination", ["inside_build", "missing_parent", "symlink"])
def test_manifest_review_json_file_rejects_unsafe_destinations(tmp_path, capsys, destination):
    build, baseline, proposed, _, _ = _fixtures(tmp_path)
    target = tmp_path / "review.json"
    if destination == "inside_build":
        target = build / "review.json"
    elif destination == "missing_parent":
        target = tmp_path / "missing" / "review.json"
    else:
        target.write_text("preserve", encoding="utf-8")
        link = tmp_path / "linked.json"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError) as error:
            pytest.skip(f"symlinks unavailable: {error}")
        target = link
    main = importlib.import_module("searcharis.manifest_review_cli").main
    assert main([str(baseline), str(proposed), str(build), "--json-file", str(target)]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "Invalid input" in output.err
    if destination == "inside_build":
        assert not target.exists()
    elif destination == "symlink":
        assert target.read_text(encoding="utf-8") == "preserve"


def test_manifest_review_interrupted_json_file_is_cleaned_up(tmp_path, capsys, monkeypatch):
    build, baseline, proposed, _, _ = _fixtures(tmp_path)
    target = tmp_path / "review.json"
    original_open = Path.open

    class BrokenFile:
        def __init__(self, path, **kwargs):
            self.file = original_open(path, "x", **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.file.__exit__(*args)

        def fileno(self):
            return self.file.fileno()

        def write(self, content):
            self.file.write(content[:8])
            self.file.flush()
            raise OSError("simulated interrupted approval report")

    def broken_open(path, mode="r", **kwargs):
        if path == target and mode == "x":
            return BrokenFile(path, **kwargs)
        return original_open(path, mode, **kwargs)

    monkeypatch.setattr(Path, "open", broken_open)
    main = importlib.import_module("searcharis.manifest_review_cli").main
    assert main([str(baseline), str(proposed), str(build), "--json-file", str(target)]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "simulated interrupted" in output.err
    assert not target.exists()
