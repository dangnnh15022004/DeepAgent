from langchain_core.messages import SystemMessage, AIMessage

from src.agent.memory.state import DeepAIState
from src.llm.providers.azure import llm
from src.agent.prompts.system_prompts import SYNTHESIZER_PROMPT


def synthesizer_node(state: DeepAIState):
    """Compose the final customer-facing reply from internal agent reports."""
    reports = state.get("agent_reports", [])
    reports_text = "\n".join(reports) if reports else "No internal reports available."

    # Drop tool messages and orphan AI-with-tool_calls to avoid 400 BadRequest.
    clean_messages = [
        m for m in state["messages"]
        if m.type in ("human", "ai")
        and not (hasattr(m, "tool_calls") and m.tool_calls)
    ]

    system_prompt = f"""{SYNTHESIZER_PROMPT}
Dựa trên các BÁO CÁO NỘI BỘ sau đây, hãy tổng hợp thành câu trả lời thân thiện cho khách hàng:
{reports_text}"""

    messages = [SystemMessage(content=system_prompt)] + clean_messages
    response = llm.invoke(messages)
    return {"messages": [AIMessage(content=response.content)], "agent_reports": ["CLEAR_REPORTS"]}


def router_hub_node(state: DeepAIState):
    """Forward to the next agent. No-op; routing is handled by `route_to_next_task`."""
    return {}
