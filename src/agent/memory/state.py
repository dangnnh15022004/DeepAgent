import operator
from typing import Annotated, Any, Dict, Optional, TypedDict

from langchain_core.messages import AnyMessage
from pydantic import BaseModel, Field


def manage_reports(left: list[str], right: list[str]) -> list[str]:
    if right == ["CLEAR_REPORTS"]:
        return []
    return (left or []) + right


class DeepAIState(TypedDict):
    """Shared state across all agents.

    Note: access_token and session_id are passed via RunnableConfig["configurable"],
    NOT stored in state. They are read by each agent via `get_access_token(config)`.
    """
    messages: Annotated[list[AnyMessage], operator.add]
    pending_tasks: list[str]
    extracted_data: dict[str, Any]
    standalone_query: str
    agent_reports: Annotated[list[str], manage_reports]


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    status: str = Field(default="success")
    reply: str = Field(..., description="Final answer from the Synthesizer Agent")
    extracted_data: Optional[Dict[str, Any]] = Field(default_factory=dict)
