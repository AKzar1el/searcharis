import pytest
from fastapi.testclient import TestClient

from demo_target.app import create_app, render_page


def test_healthy_page_has_one_nonempty_title_and_required_search_signals():
    html = render_page("healthy")
    assert html.count("<title>") == 1
    assert "<title>Searcharis Demo Store - Search Safe</title>" in html
    assert '<meta name="description"' in html
    assert '<link rel="canonical"' in html
    assert '<meta name="viewport"' in html
    assert "<h1>" in html
    assert 'application/ld+json' in html


def test_broken_page_removes_only_title_from_required_search_signals():
    html = render_page("broken")
    assert "<title>" not in html
    assert '<meta name="description"' in html
    assert '<link rel="canonical"' in html
    assert '<meta name="viewport"' in html
    assert "<h1>" in html
    assert 'application/ld+json' in html


def test_invalid_variant_is_rejected():
    with pytest.raises(ValueError, match="TARGET_VARIANT"):
        create_app("unexpected")


def test_health_endpoint_returns_200():
    with TestClient(create_app("healthy")) as client:
        assert client.get("/healthz").status_code == 200


def test_public_page_uses_request_origin_for_canonical_and_open_graph_url():
    with TestClient(create_app("healthy"), base_url="https://demo.run.app") as client:
        response = client.get("/")

    assert response.status_code == 200
    assert '<link rel="canonical" href="https://demo.run.app/">' in response.text
    assert 'content="https://demo.run.app/preview.png"' in response.text
