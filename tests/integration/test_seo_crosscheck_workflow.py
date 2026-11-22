"""End-to-end offline SEO regression gate across interacting reviewed-page policies."""

import json
import socket
from unittest.mock import patch

import pytest

from searcharis.build_compare import main

FLAGS = (
    "--check-reviewed-navigation",
    "--check-reviewed-fragments",
    "--check-unique-titles",
    "--check-unique-descriptions",
    "--check-canonical-consistency",
    "--require-inbound-link", "services/index.html",
)

HOME_URL = "https://example.test/"
SERVICE_URL = "https://example.test/services/"


def _html(title, description, canonical, body):
    return (
        f'<html><head><title>{title}</title>'
        f'<meta name="description" content="{description}">'
        f'<link rel="canonical" href="{canonical}"></head>'
        f"<body><h1>{title}</h1>{body}</body></html>"
    )


def _site(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "pages": [
            {
                "path": "index.html",
                "contract": {
                    "url": HOME_URL,
                    "title_present": True,
                    "description_present": True,
                    "canonical": HOME_URL,
                    "img_missing_alt_max": 0,
                },
            },
            {
                "path": "services/index.html",
                "contract": {
                    "url": SERVICE_URL,
                    "title_present": True,
                    "description_present": True,
                    "canonical": SERVICE_URL,
                },
            },
        ],
    }), encoding="utf-8")
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    baseline_home = _html(
        "Home", "Home description", HOME_URL,
        '<a href="/services/#pricing">Services</a>'
        '<a href="#intro">Intro</a><section id="intro"></section>'
        '<img src="/logo.png">',  # Known inherited missing-alt debt.
    )
    baseline_service = _html(
        "Services", "Services description", SERVICE_URL,
        '<section id="pricing">Plans</section>',
    )
    for root in (baseline, candidate):
        (root / "services").mkdir(parents=True)
        (root / "index.html").write_text(baseline_home, encoding="utf-8")
        (root / "services" / "index.html").write_text(
            baseline_service, encoding="utf-8",
        )
    return manifest, baseline, candidate


@pytest.mark.parametrize("introduce_regressions", [False, True])
def test_combined_reviewed_page_policies_are_offline_and_regression_only(
    tmp_path, capsys, introduce_regressions,
):
    manifest, baseline, candidate = _site(tmp_path)
    if introduce_regressions:
        (candidate / "index.html").write_text(_html(
            "Home", "Home description", HOME_URL,
            '<a href="/unreviewed/">Other</a><a href="#lost">Lost</a>'
            '<img src="/logo.png">',
        ), encoding="utf-8")
        (candidate / "services" / "index.html").write_text(_html(
            "Home", "Home description", HOME_URL,
            "<section>Plans</section>",
        ), encoding="utf-8")

    with patch.object(
        socket.socket, "connect",
        side_effect=AssertionError("Offline gate must not fetch a URL"),
    ):
        exit_code = main([
            str(manifest), str(baseline), str(candidate), *FLAGS, "--json",
        ])
    report = json.loads(capsys.readouterr().out)

    # Existing, explicitly-known debt must never turn a green incremental
    # release gate red; independent new cross-page regressions must block it.
    assert report["summary"]["existing_issues"] == 1
    if not introduce_regressions:
        assert exit_code == 0
        assert report["status"] == "pass"
        assert report["summary"]["new_issues"] == 0
        return

    assert exit_code == 1
    assert report["status"] == "regression"
    codes = {item["issue"]["code"] for item in report["new_issues"]}
    assert {
        "navigation_target_unreviewed",
        "navigation_fragment_missing",
        "title_duplicate",
        "description_duplicate",
        "reviewed_inbound_link_missing",
    } <= codes
    assert any("canonical" in code for code in codes)
