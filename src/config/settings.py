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

    pinecone_api_key: Optional[str] = None
    pinecone_index: Optional[str] = None
    database_url: Optional[str] = None

    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_order_topic: str = "ecommerce-orders"

settings = Settings()