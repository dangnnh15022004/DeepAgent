from langchain_openai import AzureChatOpenAI
from src.config.settings import settings

clean_endpoint = settings.azure_inference_endpoint.replace("/openai/v1/", "").replace("/openai/v1", "").rstrip("/")

llm = AzureChatOpenAI(
    azure_endpoint=clean_endpoint,
    api_key=settings.azure_inference_key,
    azure_deployment=settings.azure_inference_model,
    api_version="2024-02-15-preview",
    temperature=0.0,
    max_completion_tokens=16384,
)