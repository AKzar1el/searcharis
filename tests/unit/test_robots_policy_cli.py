"""Reviewed robots.txt output must not silently drift between static builds."""

import hashlib
import json
import socket
from pathlib import Path

import pytest

from searcharis.robots_policy_cli import main


def _fixture(tmp_path):
    build = tmp_path / "dist"
    build.mkdir()
    reviewed = tmp_path / "production-robots.txt"
    content = b"User-agent: *\nAllow: /\nSitemap: https://example.test/sitemap.xml\n"
    reviewed.write_bytes(content)
    (build / "robots.txt").write_bytes(content)
    return reviewed, build


def _run(capsys, reviewed, build, *flags):
    code = main([str(reviewed), str(build), "--json", *flags])
    stream = capsys.readouterr()
    return code, json.loads(stream.out), stream.err


def test_identical_reviewed_robots_policy_passes_offline(tmp_path, capsys, monkeypatch):
    reviewed, build = _fixture(tmp_path)

    def deny_socket(*args, **kwargs):
        raise AssertionError("offline robots policy validation must not open sockets")

    monkeypatch.setattr(socket, "socket", deny_socket)
    code, payload, stderr = _run(capsys, reviewed, build)
    assert code == 0 and stderr == ""
    assert payload["status"] == "pass"
    digest = hashlib.sha256(reviewed.read_bytes()).hexdigest()
    assert payload["expected_sha256"] == payload["observed_sha256"] == digest
    assert payload["artifact"] == "robots.txt"


def test_staging_disallow_all_drift_blocks_release_without_interpreting_rules(
    tmp_path, capsys,
):
    reviewed, build = _fixture(tmp_path)
    (build / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    code, report, stderr = _run(capsys, reviewed, build)
    assert code == 1 and stderr == ""
    assert report["status"] == "fail"
    assert report["expected_sha256"] != report["observed_sha256"]
    assert "Disallow" not in json.dumps(report)


@pytest.mark.parametrize("policy,should_block", [
    ("User-agent: *\nDisallow: /\n", True),
    ("User-agent: Googlebot\nDisallow: /\n", True),
    ("User-agent: *\nDisallow: /$\n", True),
    ("User-agent: Googlebot\nDisallow: /$\n", True),
    ("User-agent: *\nDisallow: /$\nAllow: /\n", True),
    ("User-agent: *\nDisallow: /$\nAllow: /*\n", True),
    ("User-agent: *\nDisallow: /$\nAllow: /$\n", False),
    ("User-agent: *\nDisallow: /$\nAllow: /*$\n", False),
    ("User-agent: *\nDisallow: /$\nUser-agent: Googlebot\nAllow: /\n", False),
    ("user-agent: *\nSitemap: https://example.test/map.xml\n disallow : / # staging\n", True),
    ("User-agent: Bingbot\nDisallow: /\n", False),
    ("User-agent: *\nDisallow: /private/\n", False),
    ("User-agent: *\n# Disallow: /\nAllow: /\n", False),
    ("User-agent: *\nDisallow: /\nAllow: /\n", False),
    ("User-agent: *\nDisallow: /\nAllow: /$\n", False),
    ("User-agent: *\nDisallow: /\nAllow: /*$\n", False),
    ("User-agent: *\nDisallow: /\nAllow: /public/\n", True),
    ("User-agent: *\nDisallow: /\nUser-agent: Googlebot\nAllow: /\n", False),
    ("User-agent: *\nDisallow: /\nUser-agent: Googlebot\nDisallow: /news/\n", False),
    ("User-agent: Googlebot\nDisallow: /\nUser-agent: Googlebot\nAllow: /\n", False),
    ("User-agent: Bingbot\nUser-agent: Googlebot\nDisallow: /\n", True),
    ("User-agent: Googlebot*\nDisallow: /\n", True),
    ("User-agent: Googlebot/1.2\nDisallow: /\n", True),
    ("User-agent: *\nDisallow: /*\n", True),
    ("User-agent: Googlebot\nDisallow: /*\n", True),
    ("User-agent: *\nDisallow: /*\nAllow: /\n", False),
    ("User-agent: *\nDisallow: /*\nAllow: /*\n", False),
    ("User-agent: *\nDisallow: /*\nAllow: /public/\n", True),
    ("User-agent: *\nDisallow: /*\nUser-agent: Googlebot\nAllow: /\n", False),
])
def test_optional_googlebot_root_block_from_identical_reviewed_policy(
    tmp_path, capsys, monkeypatch, policy, should_block,
):
    reviewed, build = _fixture(tmp_path)
    content = policy.encode("utf-8")
    reviewed.write_bytes(content)
    (build / "robots.txt").write_bytes(content)
    monkeypatch.setattr(socket, "socket", lambda *_a, **_kw: (
        (_ for _ in ()).throw(AssertionError("offline guard must not open sockets"))
    ))

    default_code, default, _ = _run(capsys, reviewed, build)
    assert default_code == 0 and default["reason"] is None
    code, result, stderr = _run(capsys, reviewed, build, "--check-root-crawl-block")
    assert stderr == ""
    assert code == (1 if should_block else 0)
    assert result["status"] == ("fail" if should_block else "pass")
    assert result["reason"] == ("root_crawl_block" if should_block else None)
    assert result["expected_sha256"] == result["observed_sha256"]


def test_root_crawl_block_reports_and_preserves_existing_drift_priority(tmp_path, capsys):
    reviewed, build = _fixture(tmp_path)
    content = b"User-agent: *\nDisallow: /\n"
    reviewed.write_bytes(content)
    (build / "robots.txt").write_bytes(content)
    reports = tmp_path / "reports"
    reports.mkdir()
    assert main([
        str(reviewed), str(build), "--check-root-crawl-block",
        "--report-dir", str(reports), "--github-annotations",
    ]) == 1
    output = capsys.readouterr()
    assert "::error" in output.out and "root" in output.out.lower()
    saved = json.loads((reports / "seo-robots.json").read_text(encoding="utf-8"))
    assert saved["reason"] == "root_crawl_block"
    assert "root" in (reports / "seo-robots.xml").read_text(encoding="utf-8").lower()
    assert "root" in (reports / "seo-robots.md").read_text(encoding="utf-8").lower()
    (build / "robots.txt").write_bytes(b"User-agent: *\nAllow: /\n")
    code, result, _ = _run(capsys, reviewed, build, "--check-root-crawl-block")
    assert code == 1 and result["reason"] == "changed"


def test_missing_built_robots_file_is_a_release_mismatch(tmp_path, capsys):
    reviewed, build = _fixture(tmp_path)
    (build / "robots.txt").unlink()
    code, report, stderr = _run(capsys, reviewed, build)
    assert code == 1 and stderr == ""
    assert report["status"] == "fail"
    assert report["observed_sha256"] is None


def test_wrong_case_robots_filename_is_missing_on_all_ci_platforms(tmp_path, capsys):
    reviewed, build = _fixture(tmp_path)
    (build / "robots.txt").rename(build / "Robots.txt")
    code, report, stderr = _run(capsys, reviewed, build)
    assert code == 1 and stderr == ""
    assert report["reason"] == "missing"
    assert report["observed_sha256"] is None


@pytest.mark.parametrize("invalid", ["missing_reviewed", "missing_root", "reference_in_build"])
def test_bad_reference_or_build_root_is_input_error(tmp_path, capsys, invalid):
    reviewed, build = _fixture(tmp_path)
    if invalid == "missing_reviewed":
        reviewed = tmp_path / "absent.txt"
    elif invalid == "missing_root":
        build = tmp_path / "absent-dist"
    else:
        reviewed = build / "robots.txt"
    code, payload, stderr = _run(capsys, reviewed, build)
    assert code == 2 and stderr == ""
    assert payload["status"] == "error"
    assert isinstance(payload["error"], str) and payload["error"]


@pytest.mark.parametrize("which", ["reviewed", "built"])
def test_oversized_policy_is_rejected_before_hashing(tmp_path, capsys, which):
    reviewed, build = _fixture(tmp_path)
    target = reviewed if which == "reviewed" else build / "robots.txt"
    target.write_bytes(b"x" * (512_000 + 1))
    code, payload, stderr = _run(capsys, reviewed, build)
    assert code == 2 and stderr == ""
    assert payload["status"] == "error"
    assert "limit" in payload["error"].lower()


def test_built_robots_symlink_cannot_escape_build(tmp_path, capsys):
    reviewed, build = _fixture(tmp_path)
    target = tmp_path / "external-robots.txt"
    target.write_bytes(reviewed.read_bytes())
    (build / "robots.txt").unlink()
    try:
        (build / "robots.txt").symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks not available in this environment: {exc}")
    code, payload, _ = _run(capsys, reviewed, build)
    assert code == 2
    assert payload["status"] == "error"


def test_plain_output_preserves_exit_code_and_actionable_digest(tmp_path, capsys):
    reviewed, build = _fixture(tmp_path)
    assert main([str(reviewed), str(build)]) == 0
    assert "PASS" in capsys.readouterr().out
    (build / "robots.txt").write_bytes(b"User-agent: *\nDisallow: /\n")
    assert main([str(reviewed), str(build)]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "SHA-256" in out


def test_standalone_robots_github_annotations_are_opt_in_and_offline(
    tmp_path, capsys, monkeypatch,
):
    reviewed, build = _fixture(tmp_path)

    def deny_socket(*args, **kwargs):
        raise AssertionError("robots annotations must not fetch URLs")

    monkeypatch.setattr(socket, "socket", deny_socket)
    args = [str(reviewed), str(build), "--github-annotations"]
    assert main(args) == 0
    assert capsys.readouterr().out == ""
    (build / "robots.txt").write_bytes(b"User-agent: *\nDisallow: /\n")
    assert main(args) == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.count("::error ") == 1
    assert "robots.txt" in output.out and "changed" in output.out
    assert "file=" not in output.out and "line=" not in output.out


def test_standalone_robots_github_annotations_escape_missing_sitemap(
    tmp_path, capsys,
):
    reviewed, build = _fixture(tmp_path)
    missing = "https://example.test/missing%25.xml"
    assert main([
        str(reviewed), str(build), "--github-annotations",
        "--require-robots-sitemap", missing,
    ]) == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.count("::error ") == 1
    assert "missing%2525.xml" in output.out
    assert "Missing reviewed Sitemap" in output.out


def test_standalone_robots_github_annotations_follow_successful_report_write(
    tmp_path, capsys,
):
    reviewed, build = _fixture(tmp_path)
    (build / "robots.txt").unlink()
    output_path = tmp_path / "robots-report.json"
    args = [str(reviewed), str(build), "--json-file", str(output_path), "--github-annotations"]
    assert main(args) == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert "::error " in output.out
    assert json.loads(output_path.read_text(encoding="utf-8"))["reason"] == "missing"
    # A refused create-only output must not publish a misleading annotation.
    assert main(args) == 2
    output = capsys.readouterr()
    assert "already exists" in output.err
    assert output.out == ""


def test_standalone_robots_github_annotations_reject_json_stdout(tmp_path, capsys):
    reviewed, build = _fixture(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main([str(reviewed), str(build), "--json", "--github-annotations"])
    assert exc.value.code == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_checked_in_policy_smoke_matches_documented_exit_codes(capsys):
    root = Path(__file__).resolve().parents[2] / "examples/seo-contract/robots-policy"
    good, report, _ = _run(capsys, root / "reviewed.txt", root / "passing")
    bad, drift, _ = _run(capsys, root / "reviewed.txt", root / "blocked")
    assert good == 0 and report["status"] == "pass"
    assert bad == 1 and drift["reason"] == "changed"


def test_standalone_required_sitemaps_pass_offline_with_multiple_reviewed_urls(
    tmp_path, capsys, monkeypatch,
):
    reviewed, build = _fixture(tmp_path)
    content = reviewed.read_bytes() + b"Sitemap: https://example.test/news.xml\n"
    reviewed.write_bytes(content)
    (build / "robots.txt").write_bytes(content)

    def deny_socket(*args, **kwargs):
        raise AssertionError("Standalone robots policy must not fetch URLs")

    monkeypatch.setattr(socket, "socket", deny_socket)
    code, result, stderr = _run(
        capsys, reviewed, build,
        "--require-robots-sitemap", "https://example.test/sitemap.xml",
        "--require-robots-sitemap", "https://example.test/news.xml",
    )
    assert (code, stderr, result["status"]) == (0, "", "pass")
    assert result["required_sitemap_urls"] == [
        "https://example.test/sitemap.xml", "https://example.test/news.xml",
    ]
    assert result["missing_sitemap_urls"] == []


def test_standalone_missing_required_sitemap_fails_matching_policy(tmp_path, capsys):
    reviewed, build = _fixture(tmp_path)
    code, result, stderr = _run(
        capsys, reviewed, build,
        "--require-robots-sitemap", "https://example.test/sitemap.xml",
        "--require-robots-sitemap", "https://example.test/absent.xml",
    )
    assert (code, stderr) == (1, "")
    assert result["reason"] == "sitemap_declaration_missing"
    assert result["expected_sha256"] == result["observed_sha256"]
    assert result["missing_sitemap_urls"] == ["https://example.test/absent.xml"]

    plain_code = main([
        str(reviewed), str(build),
        "--require-robots-sitemap", "https://example.test/absent.xml",
    ])
    output = capsys.readouterr().out
    assert plain_code == 1
    assert "Sitemap" in output and "https://example.test/absent.xml" in output


@pytest.mark.parametrize("values", [
    ["https://example.test/sitemap.xml", "https://example.test/sitemap.xml"],
    ["file:///sitemap.xml"],
    ["https://example.test/a.xml"] * 17,
])
def test_standalone_required_sitemap_invalid_options_return_json_error(
    tmp_path, capsys, values,
):
    reviewed, build = _fixture(tmp_path)
    flags = [item for value in values for item in ("--require-robots-sitemap", value)]
    code, result, stderr = _run(capsys, reviewed, build, *flags)
    assert (code, stderr, result["status"]) == (2, "", "error")


def test_standalone_single_required_sitemap_keeps_legacy_json_shape(
    tmp_path, capsys,
):
    reviewed, build = _fixture(tmp_path)
    code, result, stderr = _run(
        capsys, reviewed, build,
        "--require-robots-sitemap", "https://example.test/sitemap.xml",
    )
    assert (code, stderr, result["status"]) == (0, "", "pass")
    assert result["required_sitemap_url"] == "https://example.test/sitemap.xml"
    assert "required_sitemap_urls" not in result
    assert "missing_sitemap_urls" not in result


@pytest.mark.parametrize("blocked", [False, True])
def test_standalone_robots_json_file_is_complete_utf8_offline_artifact(
    tmp_path, capsys, monkeypatch, blocked,
):
    reviewed, build = _fixture(tmp_path)
    if blocked:
        (build / "robots.txt").write_bytes(b"User-agent: *\nDisallow: /\n")
    output = tmp_path / "reports.json"

    def deny_socket(*args, **kwargs):
        raise AssertionError("Writing the offline robots CI report must not open sockets")

    monkeypatch.setattr(socket, "socket", deny_socket)
    expected_code, expected, _ = _run(capsys, reviewed, build)
    code = main([str(reviewed), str(build), "--json-file", str(output)])
    stream = capsys.readouterr()

    assert code == expected_code == (1 if blocked else 0)
    assert stream.out == stream.err == ""
    raw = output.read_bytes()
    assert raw.startswith(b"{") and raw.endswith(b"\n")
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert json.loads(raw.decode("utf-8")) == expected


@pytest.mark.parametrize("problem", [
    "existing", "missing_parent", "inside_build", "reviewed_source",
])
def test_standalone_robots_json_file_refuses_unsafe_destination(
    tmp_path, capsys, problem,
):
    reviewed, build = _fixture(tmp_path)
    output = {
        "existing": tmp_path / "existing.json",
        "missing_parent": tmp_path / "missing" / "result.json",
        "inside_build": build / "report.json",
        "reviewed_source": reviewed,
    }[problem]
    if problem == "existing":
        output.write_bytes(b"KEEP\n")
    before_policy = reviewed.read_bytes()
    before_build = (build / "robots.txt").read_bytes()

    code = main([str(reviewed), str(build), "--json-file", str(output)])
    stream = capsys.readouterr()
    assert code == 2
    assert stream.out == ""
    assert "Invalid input:" in stream.err
    assert reviewed.read_bytes() == before_policy
    assert (build / "robots.txt").read_bytes() == before_build
    if problem == "existing":
        assert output.read_bytes() == b"KEEP\n"
    elif problem != "reviewed_source":
        assert not output.exists()


def test_standalone_robots_json_file_not_written_when_input_invalid(
    tmp_path, capsys,
):
    _, build = _fixture(tmp_path)
    output = tmp_path / "invalid.json"
    code = main([
        str(tmp_path / "absent.txt"), str(build),
        "--json-file", str(output),
    ])
    stream = capsys.readouterr()
    assert code == 2
    assert stream.out == ""
    assert "Invalid input:" in stream.err
    assert not output.exists()


def test_standalone_robots_json_file_and_stdout_json_are_exclusive(
    tmp_path,
):
    reviewed, build = _fixture(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main([
            str(reviewed), str(build),
            "--json", "--json-file", str(tmp_path / "result.json"),
        ])
    assert exc.value.code == 2


def test_standalone_robots_json_file_preserves_missing_sitemap_details(
    tmp_path, capsys,
):
    reviewed, build = _fixture(tmp_path)
    output = tmp_path / "robots-report.json"
    code = main([
        str(reviewed), str(build),
        "--require-robots-sitemap", "https://example.test/sitemap.xml",
        "--require-robots-sitemap", "https://example.test/absent.xml",
        "--json-file", str(output),
    ])
    stream = capsys.readouterr()
    assert code == 1
    assert stream.out == stream.err == ""
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert report["reason"] == "sitemap_declaration_missing"
    assert report["missing_sitemap_urls"] == ["https://example.test/absent.xml"]
