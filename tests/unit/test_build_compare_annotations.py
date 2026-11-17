"""Opt-in, bounded GitHub Actions diagnostics for offline build regressions."""

import json
import socket

import pytest

from searcharis.build_compare import main


def _fixture(tmp_path, *, baseline_title="Good", candidate_title=""):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    for root, title in ((baseline, baseline_title), (candidate, candidate_title)):
        (root / "index.html").write_text(
            f"<html><head><title>{title}</title></head></html>", encoding="utf-8",
        )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "pages": [{
            "path": "index.html",
            "contract": {"url": "https://seo-ci.invalid/", "title_present": True},
        }],
    }), encoding="utf-8")
    return manifest, baseline, candidate


def _run(manifest, baseline, candidate, *args):
    return main([str(manifest), str(baseline), str(candidate), *map(str, args)])


def test_annotations_emit_only_new_blocking_regressions_offline(tmp_path, capsys, monkeypatch):
    manifest, baseline, candidate = _fixture(tmp_path)

    def reject_network(*_args, **_kwargs):
        raise AssertionError("offline comparison attempted network access")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    assert _run(manifest, baseline, candidate, "--github-annotations") == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.startswith("::error title=SEO new issue::")
    assert "title_presence" in captured.out
    assert "index.html" in captured.out
    assert captured.out.count("::error") == 1


def test_annotations_ignore_inherited_debt(tmp_path, capsys):
    manifest, baseline, candidate = _fixture(
        tmp_path, baseline_title="", candidate_title="",
    )
    assert _run(manifest, baseline, candidate, "--github-annotations") == 0
    assert capsys.readouterr().out == ""


def test_unlisted_html_is_annotated_only_when_gated(tmp_path, capsys):
    manifest, baseline, candidate = _fixture(
        tmp_path, baseline_title="Good", candidate_title="Good",
    )
    (candidate / "extra.html").write_text("<html></html>", encoding="utf-8")
    assert _run(manifest, baseline, candidate, "--github-annotations") == 0
    assert capsys.readouterr().out == ""
    assert _run(
        manifest, baseline, candidate,
        "--fail-on-new-unlisted", "--github-annotations",
    ) == 1
    output = capsys.readouterr().out
    assert output.startswith("::error title=SEO new unlisted HTML::")
    assert "extra.html" in output


def test_annotations_have_category_fair_limit_and_opt_in_unverified_gate():
    from searcharis.report_compare import format_github_annotations

    result = {
        "new_issues": [{
            "path": f"new-{i}.html", "checked_url": "https://seo-ci.invalid/",
            "issue": {"code": "title_presence", "expected": True, "actual": False},
        } for i in range(40)],
        "removed_pages": [{"path": "gone.html", "checked_url": "https://seo-ci.invalid/gone"}],
        "new_unlisted": ["extra.html"],
        "new_unverified_targets": [{
            "check": "canonical_consistency", "path": "index.html",
            "target": "https://seo-ci.invalid/unreviewed",
        }],
    }
    ungated = format_github_annotations(result)
    assert len(ungated) == 20
    assert any("gone.html" in line for line in ungated)
    assert not any("extra.html" in line or "unreviewed" in line for line in ungated)

    gated = format_github_annotations(
        result, gate_new_unlisted=True, gate_new_unverified_targets=True,
    )
    assert len(gated) == 20
    assert any("gone.html" in line for line in gated)
    assert any("extra.html" in line for line in gated)
    assert any("unreviewed" in line for line in gated)


def test_annotations_escape_untrusted_values_and_bound_message_size():
    from searcharis.report_compare import format_github_annotations

    hostile = "%\n::error title=FORGED::bad\r"
    result = {
        "new_issues": [{
            "path": hostile, "checked_url": "https://seo-ci.invalid/",
            "issue": {"code": hostile, "expected": "x" * 8000, "actual": hostile},
        }],
        "removed_pages": [], "new_unlisted": [], "new_unverified_targets": [],
    }
    (line,) = format_github_annotations(result)
    assert line.startswith("::error title=SEO new issue::")
    assert line.count("::error title=") == 1
    assert "\n" not in line and "\r" not in line
    assert "%25" in line
    assert len(line) < 600


def test_annotations_reject_file_output_mode(tmp_path):
    manifest, baseline, candidate = _fixture(tmp_path)
    report = tmp_path / "comparison.json"
    with pytest.raises(SystemExit) as failure:
        _run(
            manifest, baseline, candidate, "--github-annotations", "--json-file", report,
        )
    assert failure.value.code == 2
    assert not report.exists()


def test_annotations_invalid_inputs_do_not_emit_workflow_commands(tmp_path, capsys):
    manifest, baseline, candidate = _fixture(tmp_path)
    assert _run(manifest, baseline, candidate / "missing", "--github-annotations") == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Invalid input" in captured.err
