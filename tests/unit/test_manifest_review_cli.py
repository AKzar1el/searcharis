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


def test_manifest_review_markdown_file_shows_unapproved_additions(tmp_path, capsys, monkeypatch):
    build, baseline, proposed, _, added = _fixtures(tmp_path)
    target = tmp_path / "approval.md"

    def reject_network(*args, **kwargs):
        raise AssertionError("Approval summaries cannot fetch public URLs")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    main = importlib.import_module("searcharis.manifest_review_cli").main
    assert main([str(baseline), str(proposed), str(build), "--markdown-file", str(target)]) == 0
    output = capsys.readouterr()
    assert output.out == output.err == ""
    raw = target.read_bytes()
    assert raw.startswith(b"# Searcharis SEO manifest approval") and b"\x00" not in raw
    assert not raw.startswith(b"\xef\xbb\xbf")
    report = raw.decode("utf-8")
    assert "**Result: PASS**" in report
    assert "1 previously approved" in report
    assert "UNREVIEWED" in report
    assert "manual review" in report.lower()
    assert added["path"] in report


def test_manifest_review_markdown_file_shows_blocking_drift_and_no_false_approval(tmp_path, capsys):
    build, baseline, proposed, old, added = _fixtures(tmp_path)
    proposed.write_text(
        json.dumps({"version": 1, "pages": [
            dict(old, contract={**old["contract"], "h1_min": 2}), added,
        ]}), encoding="utf-8",
    )
    target = tmp_path / "drift.md"
    main = importlib.import_module("searcharis.manifest_review_cli").main
    assert main([
        str(baseline), str(proposed), str(build),
        "--markdown-file", str(target), "--github-annotations",
    ]) == 1
    assert "::error title=SEO approval integrity::" in capsys.readouterr().out
    report = target.read_text(encoding="utf-8")
    assert "**Result: FAIL**" in report
    assert "approved SEO assertions changed" in report
    assert "approval" in report.lower()
    assert "UNREVIEWED new pages" not in report


def test_manifest_review_markdown_is_bounded_and_html_escaped():
    module = importlib.import_module("searcharis.manifest_review_cli")
    report = {
        "status": "pass", "approved_pages": 1, "new_pages": 12,
        "review_required": True,
        "unreviewed_paths": ["<script>&\"\n" + str(i) for i in range(12)],
    }
    result = module._approval_markdown(report)
    assert "<script>" not in result and "&lt;script&gt;" in result
    assert "&amp;" in result and "\\n" in result
    assert "more unreviewed" in result
    assert "11" not in result and "10" not in result.split("more unreviewed", 1)[1]
    assert result.count("&lt;script&gt;") == 10


@pytest.mark.parametrize("unsafe", ["existing", "inside_build", "missing_parent", "symlink"])
def test_manifest_review_markdown_rejects_unsafe_destinations(tmp_path, capsys, unsafe):
    build, baseline, proposed, _, _ = _fixtures(tmp_path)
    target = tmp_path / "approval.md"
    if unsafe == "existing":
        target.write_text("do not overwrite", encoding="utf-8")
    elif unsafe == "inside_build":
        target = build / "approval.md"
    elif unsafe == "missing_parent":
        target = tmp_path / "missing" / "approval.md"
    else:
        destination = tmp_path / "protected.md"
        destination.write_text("do not overwrite", encoding="utf-8")
        try:
            target.symlink_to(destination)
        except (OSError, NotImplementedError) as error:
            pytest.skip(f"symlinks unavailable: {error}")
    main = importlib.import_module("searcharis.manifest_review_cli").main
    assert main([str(baseline), str(proposed), str(build), "--markdown-file", str(target)]) == 2
    assert "Invalid input" in capsys.readouterr().err
    if unsafe in {"existing", "symlink"}:
        assert target.read_text(encoding="utf-8") == "do not overwrite"
    else:
        assert not target.exists()


def test_manifest_review_markdown_invalid_manifest_writes_no_artifact(tmp_path, capsys):
    build, baseline, proposed, _, _ = _fixtures(tmp_path)
    proposed.write_text('{"version":1,"version":1,"pages":[]}', encoding="utf-8")
    target = tmp_path / "approval.md"
    main = importlib.import_module("searcharis.manifest_review_cli").main
    assert main([str(baseline), str(proposed), str(build), "--markdown-file", str(target)]) == 2
    assert "Invalid input" in capsys.readouterr().err
    assert not target.exists()


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


def test_approval_review_opt_in_github_annotations_for_failures(tmp_path, capsys):
    build, baseline, proposed, old, added = _fixtures(tmp_path)
    main = importlib.import_module("searcharis.manifest_review_cli").main

    # An appended page is still unreviewed, but it is not an integrity failure.
    assert main([str(baseline), str(proposed), str(build), "--github-annotations"]) == 0
    assert "::error" not in capsys.readouterr().out

    changed = dict(old, contract={**old["contract"], "h1_min": 0})
    proposed.write_text(
        json.dumps({"version": 1, "pages": [changed, added]}), encoding="utf-8"
    )
    assert main([str(baseline), str(proposed), str(build), "--github-annotations"]) == 1
    output = capsys.readouterr()
    assert "FAIL manifest approval integrity" in output.out
    assert output.out.count("::error title=SEO approval integrity::") == 1
    assert "approved SEO assertions changed" in output.out
    assert output.err == ""

    proposed.unlink()
    assert main([str(baseline), str(proposed), str(build), "--github-annotations"]) == 2
    output = capsys.readouterr()
    assert "Invalid input" in output.err
    assert output.out.count("::error title=SEO approval input::") == 1


def test_approval_github_annotation_escapes_untrusted_control_text():
    module = importlib.import_module("searcharis.manifest_review_cli")
    annotation = module._github_approval_error("bad%::title\r\n::error title=forged::text", kind="integrity")
    assert annotation.startswith("::error title=SEO approval integrity::")
    assert annotation.count("::error") == 1
    assert "%25" in annotation and "%0D" in annotation and "%0A" in annotation
    assert "\r" not in annotation and "\n" not in annotation
    assert "title=forged::" not in annotation
    assert len(annotation) < 400


def test_approval_annotations_cannot_mix_with_structured_stdout(tmp_path):
    build, baseline, proposed, _, _ = _fixtures(tmp_path)
    main = importlib.import_module("searcharis.manifest_review_cli").main
    with pytest.raises(SystemExit) as error:
        main([str(baseline), str(proposed), str(build), "--github-annotations", "--json"])
    assert error.value.code == 2


def test_approval_annotations_compatible_with_portable_json_file(tmp_path, capsys):
    build, baseline, proposed, old, _ = _fixtures(tmp_path)
    main = importlib.import_module("searcharis.manifest_review_cli").main
    altered = dict(old, contract={**old["contract"], "h1_min": 0})
    proposed.write_text(json.dumps({"version": 1, "pages": [altered]}), encoding="utf-8")
    target = tmp_path / "review.json"
    assert main([
        str(baseline), str(proposed), str(build), "--json-file", str(target),
        "--github-annotations",
    ]) == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.count("::error title=SEO approval integrity::") == 1
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "fail"
