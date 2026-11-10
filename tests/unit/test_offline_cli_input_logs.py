"""Offline SEO CLI errors must not forge CI logs with untrusted input text."""

import json

import pytest

from searcharis.contract_cli import main as contract_main
from searcharis.report_compare import main as compare_main
from searcharis.site_cli import main as site_main


@pytest.mark.parametrize("cli", ["contract", "site", "compare"])
@pytest.mark.parametrize("structured", [False, True])
def test_offline_cli_input_error_is_safe_in_human_logs_and_intact_in_json(
    tmp_path, capsys, cli, structured,
):
    injected = "unknown\nFORGED_CI_SUCCESS_" + "X" * 1100
    html = tmp_path / "index.html"
    html.write_text("<html><head><title>OK</title></head></html>", encoding="utf-8")
    if cli == "contract":
        contract = tmp_path / "contract.json"
        contract.write_text(json.dumps({
            "url": "https://example.test/", "title_present": True, injected: True,
        }), encoding="utf-8")
        runner = contract_main
        args = [str(contract), str(html)]
    elif cli == "site":
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps({
            "version": 1,
            "pages": [{"path": "index.html", "contract": {
                "url": "https://example.test/", "title_present": True, injected: True,
            }}],
        }), encoding="utf-8")
        runner = site_main
        args = [str(manifest), str(tmp_path)]
    else:
        baseline = tmp_path / "baseline.json"
        # Duplicate object keys are rejected, but their key names are input data.
        name = json.dumps(injected)
        baseline.write_text("{" + name + ": 1, " + name + ": 2}", encoding="utf-8")
        candidate = tmp_path / "candidate.json"
        candidate.write_text("{}", encoding="utf-8")
        runner = compare_main
        args = [str(baseline), str(candidate)]

    assert runner(args + (["--json"] if structured else [])) == 2
    captured = capsys.readouterr()
    if structured:
        assert captured.err == ""
        result = json.loads(captured.out)
        assert result["status"] == "error"
        assert injected in result["error"]
    else:
        assert captured.out == ""
        assert captured.err.count("\n") == 1
        assert "\\nFORGED_CI_SUCCESS_" in captured.err
        assert len(captured.err) < 450
