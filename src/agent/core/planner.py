from typing import Literal
from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, HumanMessage

from src.agent.memory.state import DeepAIState
from src.llm.providers.azure import llm
from src.agent.prompts.system_prompts import PLANNER_PROMPT


class PlanOutput(BaseModel):
    standalone_query: str = Field(
        description=(
            "The user's latest message rewritten as a clear, standalone, "
            "self-contained query. Resolve pronouns using conversation "
            "history. Keep the same language as the customer."
        )
    )
    pending_tasks: list[Literal["DeepsaleopsAgent", "SysAgent", "DeeptraceAgent"]] = Field(
        description=(
            "Sub-agents to invoke. Use [] for greetings, chit-chat, or "
            "queries already answered. Route by intent: ordering/buying → "
            "DeepsaleopsAgent; product info/traceability → DeeptraceAgent; "
            "policy/HR/general → SysAgent."
        )
    )
    detected_lang: str = Field(
        description=(
            "ISO code of the user's LATEST message language only. "
            "Options: 'vi', 'en', 'zh', 'ja', 'ko', 'fr', 'de', 'es', "
            "'th', 'lo'."
        )
    )


def planner_node(state: DeepAIState):
    # 2-pass: first rewrite, then route.
    # Pass 1: rewrite as standalone_query (history-aware).
    # Pass 2: route to sub-agent(s) based on intent.
    last_user_msg = state["messages"][-1].content

    # Build clean history (human/ai turns only, no tool calls).
    messages = state.get("messages", [])
    clean_history = [
        m for m in messages[:-1]
        if m.type in ("human", "ai") and not getattr(m, "tool_calls", None)
    ]
    # Wider window for rewrite — last 6 turns; for routing we re-use same.
    history_text = (
        "\n".join([f"{m.type}: {m.content}" for m in clean_history[-6:]])
        if clean_history
        else "Không có lịch sử."
    )

    # ── Pass 1: rewrite the latest message as standalone_query ─────────────
    rewrite_prompt = f"""You rewrite the user's latest message as a
self-contained `standalone_query` that resolves pronouns, references, and
ambiguous terms using the recent chat history. Keep the same language as
the latest user message. Do NOT answer the question — only rewrite it.

=== RECENT HISTORY ===
{history_text}

=== USER'S LATEST MESSAGE ===
{last_user_msg}

Output ONLY the rewritten query, no commentary, no quotes, no prefix."""

    rewrite_llm = llm  # plain text
    rewritten = rewrite_llm.invoke(
        [SystemMessage(content=rewrite_prompt), HumanMessage(content="Rewrite:")]
    ).content.strip()

    # ── Pass 2: route to sub-agent(s) ─────────────────────────────────────
    routing_prompt = PLANNER_PROMPT.format(
        rewritten_query=rewritten,
        history_str=history_text,
    )

    structured_llm = llm.with_structured_output(PlanOutput)
    plan = structured_llm.invoke(
        [
            SystemMessage(content=routing_prompt),
            HumanMessage(
                content=(
                    "User's LATEST message — use THIS ONLY for language "
                    f"detection: {last_user_msg}"
                )
            ),
        ]
    )

    print(
        f"[Planner] rewritten='{rewritten}' | "
        f"tasks={plan.pending_tasks} | lang={plan.detected_lang}"
    )

    return {
        "standalone_query": plan.standalone_query or rewritten,
        "pending_tasks": plan.pending_tasks,
        "user_lang": plan.detected_lang,
    }
