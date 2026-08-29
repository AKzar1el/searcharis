from pathlib import Path


def test_main_container_does_not_require_optional_lockfile_to_exist():
    dockerfile = Path('Dockerfile').read_text()

    assert 'COPY uv.lock*' not in dockerfile
    assert 'COPY . .' in dockerfile
    assert 'if [ -f uv.lock ]' in dockerfile


def test_docker_context_excludes_repository_metadata_and_local_worktrees():
    dockerignore = Path('.dockerignore').read_text().splitlines()

    assert '.git' in dockerignore
    assert '.worktrees' in dockerignore
    assert '.venv' in dockerignore


def test_cloud_deploy_configures_adk_for_vertex_ai():
    deploy_script = Path('deployment/deploy-services.sh').read_text()

    assert 'GOOGLE_GENAI_USE_VERTEXAI=TRUE' in deploy_script
    assert 'GOOGLE_GENAI_USE_ENTERPRISE' not in deploy_script
