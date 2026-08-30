from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_readme_architecture_asset_exists() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "![Searcharis architecture](docs/architecture.svg)" in readme
    assert (ROOT / "docs" / "architecture.svg").is_file()


def test_env_example_uses_vertex_ai_configuration() -> None:
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "GOOGLE_GENAI_USE_VERTEXAI=TRUE" in env_example
    assert "GOOGLE_GENAI_USE_ENTERPRISE" not in env_example
    assert "SEARCHARIS_GOOGLE_MODEL" not in env_example


def test_deploy_workflow_does_not_target_deleted_branch() -> None:
    text = (ROOT / ".github" / "workflows" / "deploy-gcp.yml").read_text(encoding="utf-8")
    assert "branches: [gcp-deploy]" not in text
    assert "workflow_dispatch:" in text
