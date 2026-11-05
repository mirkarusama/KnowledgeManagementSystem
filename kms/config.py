"""All settings come from environment variables (see .env.example).

Nothing is hard-coded to one vendor: every external piece is just a URL,
so a component can be swapped by changing configuration.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KMS_", env_file=".env", extra="ignore")

    # --- LLM gateway (LiteLLM). The app only knows logical model names. ---
    llm_base_url: str = "http://litellm:4000/v1"
    llm_api_key: str = "sk-kms-dev"
    synth_model: str = "km-synth"
    router_model: str = "km-router"
    embed_model: str = "km-embed"
    embed_dim: int = 1024
    # Appended to prompts for Qwen3-style models to switch off "thinking" output.
    prompt_suffix: str = ""
    llm_timeout_s: float = 180.0
    use_llm_router: bool = True

    # Optional reranker (TEI /rerank). Empty = hybrid score only.
    rerank_url: str = ""

    # --- Data stores ---
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str = ""
    postgres_dsn: str = "postgresql://kms:kms@postgres:5432/kms"
    valkey_url: str = "redis://valkey:6379/0"

    # --- Identity (Keycloak) ---
    oidc_issuer: str = "http://localhost:8080/realms/kms"
    # Where *this container* can reach Keycloak (may differ from the public issuer URL).
    oidc_internal_url: str = "http://keycloak:8080/realms/kms"
    oidc_audience: str = "kms-api"
    mcp_client_id: str = "kms-mcp"
    mcp_client_secret: str = "kms-mcp-secret"
    # Audience requested when exchanging the user's token for a source token (RFC 8693).
    source_token_audience: str = "atlassian"
    # Dev-only: allow the X-Debug-User header instead of a real token (tests, CLI).
    allow_debug_auth: bool = False

    # --- Policy (OPA) ---
    opa_url: str = "http://opa:8181"

    # --- MCP servers ---
    mcp_atlassian_url: str = "http://mcp-atlassian:8000/mcp"
    mcp_servicenow_url: str = ""  # Phase 2

    # --- Atlassian sources ---
    confluence_url: str = "http://mock-atlassian:9000"
    jira_url: str = "http://mock-atlassian:9000"
    # Base URLs used in citation links shown to people (may differ from the in-cluster URLs above).
    confluence_public_url: str = ""
    jira_public_url: str = ""
    atlassian_deployment: str = "datacenter"  # "cloud" or "datacenter"
    # Service account used by ingestion (read-only). Cloud: email + API token (basic auth).
    atlassian_service_user: str = ""
    atlassian_service_token: str = "svc-dev-token"
    # How the CONFIRM step reads "as the user":
    #   exchange          - RFC 8693 token exchange via Keycloak, then call the source with that token
    #   permission_check  - service account asks the source "can user X read Y?" (Atlassian Cloud)
    confirm_mode: str = "exchange"
    confluence_spaces: str = ""  # comma list; empty = all
    jira_projects: str = ""  # comma list; empty = all
    classification_label_prefix: str = "classification-"
    default_classification: str = "internal"

    # --- Ingestion ---
    ingest_stream: str = "kms:ingest"
    webhook_secret: str = ""
    use_docling: bool = False
    chunk_chars: int = 1500
    chunk_overlap: int = 200
    acl_removal_guard: float = 0.05  # pause sync if >5% of docs would disappear
    poll_interval_s: int = 300
    acl_sync_interval_s: int = 900

    # --- Answering ---
    top_k: int = 8
    max_evidence: int = 8
    groundedness_threshold: float = 0.6
    strict_citations: bool = True
    freshness_days: int = 90

    # --- Observability ---
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    def list_or_none(self, value: str) -> list[str] | None:
        items = [v.strip() for v in value.split(",") if v.strip()]
        return items or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
