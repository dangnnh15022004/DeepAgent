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
    # User language detected by the planner — consumed by the synthesizer.
    user_lang: Optional[str]
    # Set to a string like "pick_product" / "pick_variant" / "pick_address"
    # to indicate the agent is waiting for user to pick from options card.
    awaiting_user_pick: Optional[str]


class ChatRequest(BaseModel):
    session_id: str
    message: str
    is_resume: bool = Field(
        default=False,
        description="True when frontend resumes from a LangGraph interrupt.",
    )
    meta: Optional[dict] = Field(
        default=None,
        description=(
            "Bypass LLM. When set, the backend directly calls place_order "
            "with the meta payload and returns the result. Shape: "
            "{action, state, variant_id, address_id, quantity, email}"
        ),
    )


class ChatResponse(BaseModel):
    status: str = Field(default="success")
    session_id: Optional[str] = Field(
        default=None,
        description="Session ID to continue the conversation.",
    )
    reply: str = Field(..., description="Final answer from the Synthesizer Agent")
    extracted_data: Optional[Dict[str, Any]] = Field(default_factory=dict)
