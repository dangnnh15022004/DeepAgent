from langchain_core.messages import SystemMessage, AIMessage

from src.agent.memory.state import DeepAIState
from src.llm.providers.azure import llm
from src.agent.prompts.system_prompts import SYNTHESIZER_PROMPT


def synthesizer_node(state: DeepAIState):
    reports = state.get("agent_reports", [])
    reports_text = "\n".join(reports) if reports else "No data available."
    user_lang = state.get("user_lang") or "vi"

    clean_messages = [
        m for m in state["messages"]
        if m.type in ("human", "ai")
        and not (hasattr(m, "tool_calls") and m.tool_calls)
    ]

    system_prompt = f"""{SYNTHESIZER_PROMPT}

INTERNAL REPORTS:
{reports_text}

CRITICAL: Respond in the user's language: '{user_lang}'.
If '{user_lang}' is 'vi', write in Vietnamese with polite honorifics (Dạ/vâng/ạ).
"""
    messages = [SystemMessage(content=system_prompt)] + clean_messages
    response = llm.invoke(messages)
    return {"messages": [AIMessage(content=response.content)], "agent_reports": ["CLEAR_REPORTS"]}


def router_hub_node(state: DeepAIState):
    return {}
