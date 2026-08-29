from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SEARCHARIS_", env_file=".env", extra="ignore")

    env: str = "local"
    project_id: str | None = None
    region: str = "us-central1"
    pubsub_topic: str = "searcharis-deployments"
    tasks_queue: str = "searcharis-verification"
    worker_url: AnyHttpUrl | None = None
    validator_mcp_url: AnyHttpUrl = "https://web-validator-mcp.digestseo.com/mcp"
    github_token: str | None = None
    github_repository: str | None = None
    webhook_secret: str | None = None
    tasks_invoker_service_account: str | None = None
    demo_token: str | None = None
    demo_repository: str | None = None
    demo_target_url: AnyHttpUrl | None = None
