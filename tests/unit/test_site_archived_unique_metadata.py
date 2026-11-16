"""Standalone archived SEO reports preserve opt-in metadata-uniqueness settings."""

import json
import socket
from unittest.mock import patch

import pytest

from searcharis.report_compare import compare_reports
from searcharis.report_compare import main as compare_main
from searcharis.site_ci import verify_manifest
from searcharis.site_cli import main as site_main


def _site(tmp_path, *, duplicate=True):
    root = tmp_path / "dist"
    root.mkdir()
    pages = []
    for name, title, description in (
        ("a", "Product", "An example product"),
        ("b", "Product" if duplicate else "Other",
         "An example product" if duplicate else "Another product"),
    ):
        (root / f"{name}.html").write_text(
            f'<html><head><title>{title}</title>'
            f'<meta name="description" content="{description}"></head></html>',
            encoding="utf-8",
        )
        pages.append({"path": f"{name}.html", "contract": {
            "url": f"https://example.test/{name}", "title_present": True,
        }})
    manifest = {"version": 1, "pages": pages}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return root, manifest, manifest_path


@pytest.mark.parametrize(
    "flag,marker,code",
    [
        ("--check-unique-titles", "unique_titles", "title_duplicate"),
        ("--check-unique-descriptions", "unique_descriptions", "description_duplicate"),
    ],
)
def test_standalone_site_cli_has_opt_in_uniqueness_check(
    tmp_path, capsys, flag, marker, code,
):
    root, _, manifest_path = _site(tmp_path)
    args = [str(manifest_path), str(root), "--json"]
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network")):
        assert site_main(args) == 0
        default = json.loads(capsys.readouterr().out)
        assert marker not in default
        assert site_main([*args, flag]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "fail"
    assert report[marker] is True
    assert [issue["code"] for page in report["pages"] for issue in page["issues"]] == [
        code, code,
    ]


@pytest.mark.parametrize("option", ["check_unique_titles", "check_unique_descriptions"])
@pytest.mark.parametrize("reverse", [False, True])
def test_archive_rejects_differently_enabled_uniqueness_even_when_green(
    tmp_path, option, reverse,
):
    root, manifest, _ = _site(tmp_path, duplicate=False)
    enabled = verify_manifest(manifest, root, **{option: True})
    disabled = verify_manifest(manifest, root)
    assert enabled["status"] == disabled["status"] == "pass"
    assert compare_reports(enabled, enabled)["status"] == "pass"
    a, b = (disabled, enabled) if reverse else (enabled, disabled)
    with pytest.raises(ValueError, match="different check options"):
        compare_reports(a, b)


@pytest.mark.parametrize("marker,bad", [
    ("unique_titles", False),
    ("unique_titles", "true"),
    ("unique_descriptions", {}),
    ("unique_descriptions", 1),
])
def test_archive_rejects_malformed_uniqueness_provenance(marker, bad):
    report = {
        "status": "pass",
        "summary": {"checked": 1, "passed": 1, "failed": 0},
        "coverage": {
            "checked": 1, "discovered": 1, "unlisted": [],
            "require_full_coverage": False,
        },
        "pages": [{"path": "index.html", "checked_url": "https://example.test/",
                   "status": "pass", "issues": []}],
        marker: bad,
    }
    with pytest.raises(ValueError, match="invalid " + marker):
        compare_reports(report, report)


def test_new_duplicate_is_regression_but_inherited_duplicate_is_not(tmp_path):
    root, manifest, _ = _site(tmp_path, duplicate=False)
    baseline = verify_manifest(manifest, root, check_unique_titles=True)
    (root / "b.html").write_text(
        '<html><head><title>Product</title>'
        '<meta name="description" content="Another product"></head></html>',
        encoding="utf-8",
    )
    candidate = verify_manifest(manifest, root, check_unique_titles=True)
    assert compare_reports(baseline, candidate)["summary"]["new_issues"] == 2
    assert compare_reports(baseline, candidate)["status"] == "regression"
    assert compare_reports(candidate, candidate)["status"] == "pass"


def test_saved_report_cli_refuses_mismatched_unique_policy(tmp_path, capsys):
    root, _, manifest_path = _site(tmp_path, duplicate=False)
    args = [str(manifest_path), str(root), "--json"]
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network")):
        assert site_main(args) == 0
        before.write_text(capsys.readouterr().out, encoding="utf-8")
        assert site_main([*args, "--check-unique-titles"]) == 0
        after.write_text(capsys.readouterr().out, encoding="utf-8")
        assert compare_main([str(before), str(after), "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error"
    assert "different check options" in result["error"]


def test_standalone_both_checks_and_archived_comparison(tmp_path, capsys):
    root, _, manifest_path = _site(tmp_path, duplicate=False)
    flags = ["--check-unique-titles", "--check-unique-descriptions"]
    args = [str(manifest_path), str(root), "--json", *flags]
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network")):
        assert site_main(args) == 0
        baseline_path.write_text(capsys.readouterr().out, encoding="utf-8")
        (root / "b.html").write_text(
            '<html><head><title>Product</title>'
            '<meta name="description" content="An example product"></head></html>',
            encoding="utf-8",
        )
        assert site_main(args) == 1
        candidate_path.write_text(capsys.readouterr().out, encoding="utf-8")
        assert compare_main([str(baseline_path), str(candidate_path), "--json"]) == 1
    diff = json.loads(capsys.readouterr().out)
    assert diff["summary"]["new_issues"] == 4
    assert {item["issue"]["code"] for item in diff["new_issues"]} == {
        "title_duplicate", "description_duplicate",
    }
