from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "DeepAI Agent"
    app_version: str = "1.0.0"

    azure_inference_endpoint: str
    azure_inference_key: str
    azure_inference_model: str = "gpt-4o-mini"

    langchain_tracing_v2: Optional[str] = None
    langchain_api_key: Optional[str] = None
    langchain_project: Optional[str] = None

    # LangSmith checkpointer (persists full session state across interrupts/resumes)
    # Reads LANGCHAIN_API_KEY and LANGCHAIN_PROJECT from .env (already configured)
    langsmith_tracing_v2: bool = False
    langsmith_api_key: Optional[str] = None
    langsmith_project: Optional[str] = None

    pinecone_api_key: Optional[str] = None
    pinecone_index: Optional[str] = None
    database_url: Optional[str] = None

    # ── Postgres (chat history persistence) ──────────────────────────────
    # Read by src/api/db/postgres.py. POSTGRES_DSN env var, e.g.:
    #   POSTGRES_DSN=postgresql://postgres:postgres@postgres:5432/deepagent
    # Falls back to local-only if unset (the app will skip history features).
    postgres_dsn: Optional[str] = None
    history_window: int = 5  # how many past turns to load as planner context

    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_order_topic: str = "ecommerce-orders"

    # DeepSalesOps API base URL (used for auth + MCP tools)
    deepsaleops_base_url: str = "https://deepsalesops-dev-api.deep.com.vn"

settings = Settings()