"""Review-queue export is a candidate list, never an automatic SEO approval."""

import json
import socket

import pytest

from searcharis.manifest_bootstrap import main as bootstrap_main
from searcharis.site_cli import _unreviewed_candidate_paths, main


def _site(tmp_path, *, extras=("pricing/index.html", "blog.html")):
    build = tmp_path / "dist"
    build.mkdir()
    (build / "index.html").write_text(
        "<html><head><title>Home</title></head></html>", encoding="utf-8"
    )
    for relative in extras:
        target = build / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "<html><head><title>Page</title></head></html>", encoding="utf-8"
        )
    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(json.dumps({
        "version": 1,
        "pages": [{"path": "index.html", "contract": {
            "url": "https://example.test/", "title_equals": "Home",
        }}],
    }), encoding="utf-8")
    return build, reviewed


def test_unreviewed_export_round_trips_to_manual_manifest_extension(tmp_path, monkeypatch):
    build, reviewed = _site(tmp_path)

    def no_network(*_args, **_kwargs):
        raise AssertionError("SEO CI must not connect to the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    queue = tmp_path / "candidate-paths.txt"
    assert main([str(reviewed), str(build), "--unreviewed-file", str(queue)]) == 0
    text = queue.read_text(encoding="utf-8")
    assert "review" in text.splitlines()[0].lower()
    assert text.splitlines()[1:] == ["blog.html", "pricing/index.html"]
    assert "index.html" not in text.splitlines()[1:]
    assert main([str(reviewed), str(build), "--require-full-coverage"]) == 1

    # The operator may inspect/edit the candidate before explicitly extending reviews.
    expanded = tmp_path / "expanded.json"
    assert bootstrap_main([
        str(build), "https://example.test/", "--extend-from", str(reviewed),
        "--only-file", str(queue), "--check-built", "--output", str(expanded),
    ]) == 0
    pages = json.loads(expanded.read_text(encoding="utf-8"))["pages"]
    assert [item["path"] for item in pages] == [
        "index.html", "blog.html", "pricing/index.html",
    ]
    assert pages[0]["contract"]["title_equals"] == "Home"


def test_unreviewed_export_preserves_strict_failure_and_reports(tmp_path):
    build, reviewed = _site(tmp_path, extras=("about.html",))
    queue = tmp_path / "candidate-paths.txt"
    json_report = tmp_path / "report.json"
    assert main([
        str(reviewed), str(build), "--require-full-coverage",
        "--unreviewed-file", str(queue), "--json-file", str(json_report),
    ]) == 1
    assert queue.read_text(encoding="utf-8").splitlines()[1:] == ["about.html"]
    assert json.loads(json_report.read_text(encoding="utf-8"))["status"] == "fail"


@pytest.mark.parametrize("obstacle", ["existing", "in-build", "missing-parent"])
def test_unreviewed_export_is_create_only_and_outside_build(tmp_path, capsys, obstacle):
    build, reviewed = _site(tmp_path)
    queue = (
        build / "pending.txt" if obstacle == "in-build"
        else tmp_path / "absent" / "pending.txt" if obstacle == "missing-parent"
        else tmp_path / "pending.txt"
    )
    if obstacle == "existing":
        queue.write_text("my approved data", encoding="utf-8")
    assert main([str(reviewed), str(build), "--unreviewed-file", str(queue)]) == 2
    assert "Invalid input" in capsys.readouterr().err
    if obstacle == "existing":
        assert queue.read_text(encoding="utf-8") == "my approved data"
    else:
        assert not queue.exists()


def test_unreviewed_export_empty_queue_is_not_a_fake_review(tmp_path):
    build, reviewed = _site(tmp_path, extras=())
    queue = tmp_path / "pending.txt"
    assert main([str(reviewed), str(build), "--unreviewed-file", str(queue)]) == 0
    assert queue.read_text(encoding="utf-8").splitlines()[1:] == []


def test_unreviewed_export_rejects_comment_shaped_paths(tmp_path):
    build, reviewed = _site(tmp_path, extras=("#not-a-comment.html",))
    queue = tmp_path / "pending.txt"
    assert main([str(reviewed), str(build), "--unreviewed-file", str(queue)]) == 2
    assert not queue.exists()


def test_unreviewed_export_not_silently_added_to_report_bundle(tmp_path):
    build, reviewed = _site(tmp_path)
    out = tmp_path / "reports"
    out.mkdir()
    with pytest.raises(SystemExit) as error:
        main([
            str(reviewed), str(build), "--report-dir", str(out),
            "--unreviewed-file", str(tmp_path / "pending.txt"),
        ])
    assert error.value.code == 2
    assert not list(out.iterdir())


def test_unreviewed_export_never_truncates_an_unimportable_queue():
    with pytest.raises(ValueError, match="exceeds 10000 paths"):
        _unreviewed_candidate_paths({
            "coverage": {"unlisted": [f"page-{i}.html" for i in range(10_001)]},
        })
    with pytest.raises(ValueError, match="exceeds 4000000 UTF-8 bytes"):
        _unreviewed_candidate_paths({
            "coverage": {"unlisted": ["a" * 4_000_000 + ".html"]},
        })
