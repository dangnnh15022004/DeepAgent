from langchain_core.messages import HumanMessage, SystemMessage

from src.agent.memory.state import DeepAIState
from src.llm.providers.azure import llm


def contextualize_node(state: DeepAIState) -> dict:
    messages = state.get("messages", [])
    if not messages:
        return {"standalone_query": ""}
    last_user_message = messages[-1].content
    if len(messages) <= 1:
        return {"standalone_query": last_user_message}

    history_str = "\n".join(
        f"{m.type}: {m.content}"
        for m in messages[-5:-1]
        if hasattr(m, "content") and m.content
    )

    system_prompt = f"""You are a context analysis expert.
Rewrite the user's latest message into a self-contained query using the 'Recent History' below.
- REPLACE pronouns and implicit references with specific entities from history.
- DO NOT answer the user. RETURN ONLY the rewritten query.
- IF already clear, return as is.

Recent History:
{history_str}"""

    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Latest user message: {last_user_message}"),
    ])
    rewritten = response.content.strip()
    print(f"[Rewriter] '{last_user_message}' -> '{rewritten}'")
    return {"standalone_query": rewritten}
