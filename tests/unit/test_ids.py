from searcharis.ids import action_key, incident_fingerprint


def test_incident_fingerprint_is_stable_and_context_sensitive():
    first = incident_fingerprint(
        "AKzar1el/searcharis-demo",
        "https://demo.example",
        "https://demo.example/",
        "seo.missing_title",
    )
    second = incident_fingerprint(
        "AKzar1el/searcharis-demo",
        "https://demo.example",
        "https://demo.example/",
        "seo.missing_title",
    )
    changed = incident_fingerprint(
        "AKzar1el/searcharis-demo",
        "https://demo.example",
        "https://demo.example/pricing",
        "seo.missing_title",
    )
    assert first == second
    assert first != changed
    assert len(first) == 64


def test_action_key_changes_with_evidence():
    assert action_key("open", "incident-1", "evidence-a") != action_key(
        "open", "incident-1", "evidence-b"
    )
