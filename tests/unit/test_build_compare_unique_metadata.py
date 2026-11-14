"""Regression-only duplicate metadata checks on reviewed local static builds."""

import json
import socket
from unittest.mock import patch

import pytest

from searcharis.build_compare import main


def _fixture(tmp_path, *, old=("A", "B"), new=("A", "A"),
             field="title", other_origin=False, noindex_b=False):
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    pages = []
    for index, name in enumerate(("a", "b")):
        for root, values in ((baseline, old), (candidate, new)):
            title = values[index] if field == "title" else name.upper()
            description = values[index] if field == "description" else name.upper()
            robots = '<meta name="robots" content="noindex">' if noindex_b and name == "b" else ""
            (root / f"{name}.html").write_text(
                f'<html><head><title>{title}</title>'
                f'<meta name="description" content="{description}">{robots}</head></html>',
                encoding="utf-8",
            )
        host = "other.test" if other_origin and name == "b" else "example.test"
        pages.append({
            "path": f"{name}.html",
            "contract": {"url": f"https://{host}/{name}", "indexable": not (
                noindex_b and name == "b"
            )},
        })
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps({"version": 1, "pages": pages}), encoding="utf-8")
    return [str(manifest_file), str(baseline), str(candidate)]


@pytest.mark.parametrize(
    ("field", "flag", "code"),
    [
        ("title", "--check-unique-titles", "title_duplicate"),
        ("description", "--check-unique-descriptions", "description_duplicate"),
    ],
)
def test_new_duplicate_metadata_is_opt_in_and_regression_only(
    tmp_path, capsys, field, flag, code,
):
    args = _fixture(tmp_path, field=field)
    assert main([*args, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["new_issues"] == 0
    assert main([*args, flag, "--json"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["new_issues"] == 2
    assert {item["issue"]["code"] for item in output["new_issues"]} == {code}


@pytest.mark.parametrize(
    ("field", "flag"),
    [("title", "--check-unique-titles"), ("description", "--check-unique-descriptions")],
)
def test_inherited_and_resolved_duplicates_do_not_block(tmp_path, capsys, field, flag):
    args = _fixture(tmp_path, old=("A", "A"), new=("A", "A"), field=field)
    assert main([*args, flag, "--json"]) == 0
    inherited = json.loads(capsys.readouterr().out)
    assert inherited["summary"]["new_issues"] == 0
    assert inherited["summary"]["existing_issues"] == 2
    candidate = tmp_path / "candidate" / "b.html"
    candidate.write_text(
        '<html><head><title>B</title><meta name="description" content="B"></head></html>',
        encoding="utf-8",
    )
    assert main([*args, flag, "--json"]) == 0
    resolved = json.loads(capsys.readouterr().out)
    assert resolved["summary"]["new_issues"] == 0
    assert resolved["summary"]["resolved_issues"] == 2


@pytest.mark.parametrize(
    ("field", "flag"),
    [("title", "--check-unique-titles"), ("description", "--check-unique-descriptions")],
)
@pytest.mark.parametrize("control", ["other_origin", "noindex_b"])
def test_uniqueness_respects_origin_and_reviewed_indexability(
    tmp_path, capsys, field, flag, control,
):
    args = _fixture(tmp_path, field=field, **{control: True})
    assert main([*args, flag, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["new_issues"] == 0


def test_both_uniqueness_checks_flow_to_files_offline(tmp_path, capsys):
    args = _fixture(tmp_path, field="title")
    for root in ("baseline", "candidate"):
        for name, description in (("a", "A" if root == "baseline" else "Same"),
                                  ("b", "B" if root == "baseline" else "Same")):
            file = tmp_path / root / f"{name}.html"
            html = file.read_text(encoding="utf-8")
            file.write_text(
                html.replace(f'content="{name.upper()}"', f'content="{description}"'),
                encoding="utf-8",
            )
    report = tmp_path / "comparison.json"
    sarif = tmp_path / "comparison.sarif"
    with patch.object(socket.socket, "connect", side_effect=AssertionError("URL fetch")):
        assert main([*args, "--check-unique-titles", "--check-unique-descriptions",
                     "--json-file", str(report), "--sarif-file", str(sarif)]) == 1
    assert capsys.readouterr().out == ""
    findings = json.loads(report.read_text(encoding="utf-8"))
    assert {item["issue"]["code"] for item in findings["new_issues"]} == {
        "title_duplicate", "description_duplicate",
    }
    sarif_codes = {item["ruleId"] for item in json.loads(
        sarif.read_text(encoding="utf-8"))["runs"][0]["results"]}
    assert sarif_codes == {"title_duplicate", "description_duplicate"}
