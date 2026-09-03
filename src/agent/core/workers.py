"""
Worker nodes for each sub-agent.

Each sub-agent binds only its own tools and returns `response.content` directly
— no second render pass, no language detection, no structured error table.
The access_token is injected server-side via mcp_manager ContextVar so the
LLM never sees the JWT.
"""

from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from src.agent.memory.state import DeepAIState
from src.agent.prompts.system_prompts import (
    DEEPSALEOPS_AGENT_PROMPT,
    DEEPTRACE_AGENT_PROMPT,
    SYS_AGENT_PROMPT,
)
from src.agent.tools.mcp_manager import mcp_manager
from src.llm.providers.azure import llm


# ─── config helpers ──────────────────────────────────────────────────────────


def get_access_token(config: RunnableConfig) -> str:
    return config.get("configurable", {}).get("access_token", "")


# ─── read-only / write tool heuristic ────────────────────────────────────────
# DeepTrace tools are all `search_*` / `get_*` (read-only) so HIL is effectively
# a no-op today, but the guard is wired up for the day a write tool lands.

READ_ONLY_PREFIXES = ("get_", "list_", "fetch_", "search_", "read_", "find_", "count_")


def is_write_tool_name(tool_name: str) -> bool:
    if not tool_name:
        return True
    return not tool_name.lower().startswith(READ_ONLY_PREFIXES)


def _has_write_tool_call(state: DeepAIState) -> bool:
    messages = state.get("messages", [])
    if not messages:
        return False
    last = messages[-1]
    if not getattr(last, "tool_calls", None):
        return False
    return any(is_write_tool_name(tc.get("name", "")) for tc in last.tool_calls)


async def _guarded_tools_node(tools: list, state: DeepAIState) -> dict:
    """Run tools; pause for human confirmation on write actions, run read-only
    tools directly."""
    if not _has_write_tool_call(state):
        return await ToolNode(tools).ainvoke(state)

    decision = interrupt({"reason": "write_action_requires_confirmation"})
    if decision == "confirm":
        return await ToolNode(tools).ainvoke(state)

    last = state["messages"][-1]
    rejected = [
        ToolMessage(
            content=f"User REJECTED this action. Reason: {decision}",
            tool_call_id=tc["id"],
        )
        for tc in last.tool_calls
        if is_write_tool_name(tc.get("name", ""))
    ]
    return {"messages": rejected}


async def _invoke_tools_with_token(
    tools: list, state: DeepAIState, config: RunnableConfig
) -> dict:
    mcp_manager.bind_access_token(get_access_token(config))
    return await _guarded_tools_node(tools, state)


# ─── shared routing logic ────────────────────────────────────────────────────


def make_decision(state: DeepAIState, agent_name: str, tool_node: str):
    """Route to tool_node when the agent has pending tool_calls; else return to hub.
    Caps runaway tool-call loops."""
    messages = state["messages"]
    last_message = messages[-1]

    tool_msg_count = 0
    for m in reversed(messages[-10:]):
        if getattr(m, "tool_calls", None):
            tool_msg_count += 1
        if tool_msg_count >= 10:
            break

    if tool_msg_count >= 10:
        print(f"[System] {agent_name} exceeded max tool calls ({tool_msg_count}). Safety override.")
        return "router_hub"

    return tool_node if getattr(last_message, "tool_calls", None) else "router_hub"


def _pop_pending(state: DeepAIState, task_name: str) -> list:
    pending = list(state.get("pending_tasks", []) or [])
    if task_name in pending:
        pending.remove(task_name)
    return pending


def _build_agent_prompt(base: str, token: str, tenant_id: str = "default_tenant") -> str:
    return base + f"""

[SYSTEM] Authentication info:
- tenant_id: {tenant_id}
- access_token (REQUIRED for all tools): {token}

[CRITICAL] For write operations (create_, update_, delete_):
- These actions require human approval before execution.

WHEN CALLING TOOL: Always pass the full access_token (do not truncate)."""


# ─── DEEPSALEOPS ─────────────────────────────────────────────────────────────


async def deepsaleops_agent(state: DeepAIState, config: RunnableConfig):
    token = get_access_token(config)
    agent_llm = llm.bind_tools(mcp_manager.deepsaleops_tools)
    prompt = _build_agent_prompt(DEEPSALEOPS_AGENT_PROMPT, token)

    response = await agent_llm.ainvoke([SystemMessage(content=prompt)] + state["messages"])

    if response.tool_calls:
        return {"messages": [response]}

    return {
        "agent_reports": [f"[DeepsaleopsAgent] {response.content}"],
        "pending_tasks": _pop_pending(state, "DeepsaleopsAgent"),
    }


async def deepsaleops_tools_node(state: DeepAIState, config: RunnableConfig):
    return await _invoke_tools_with_token(mcp_manager.deepsaleops_tools, state, config)


# ─── DEEPTRACE ───────────────────────────────────────────────────────────────


async def deeptrace_agent(state: DeepAIState, config: RunnableConfig):
    token = get_access_token(config)
    agent_llm = llm.bind_tools(mcp_manager.deeptrace_tools)
    prompt = _build_agent_prompt(DEEPTRACE_AGENT_PROMPT, token)

    response = await agent_llm.ainvoke([SystemMessage(content=prompt)] + state["messages"])

    if response.tool_calls:
        return {"messages": [response]}

    return {
        "agent_reports": [f"[DeeptraceAgent] {response.content}"],
        "pending_tasks": _pop_pending(state, "DeeptraceAgent"),
    }


async def deeptrace_tools_node(state: DeepAIState, config: RunnableConfig):
    return await _invoke_tools_with_token(mcp_manager.deeptrace_tools, state, config)


# ─── SYS AGENT ───────────────────────────────────────────────────────────────


async def sys_agent(state: DeepAIState, config: RunnableConfig):
    token = get_access_token(config)
    agent_llm = llm.bind_tools(mcp_manager.system_tools)
    prompt = _build_agent_prompt(SYS_AGENT_PROMPT, token)

    response = await agent_llm.ainvoke([SystemMessage(content=prompt)] + state["messages"])

    if response.tool_calls:
        return {"messages": [response]}

    return {
        "agent_reports": [f"[SysAgent] {response.content}"],
        "pending_tasks": _pop_pending(state, "SysAgent"),
    }


async def sys_tools_node(state: DeepAIState, config: RunnableConfig):
    return await _invoke_tools_with_token(mcp_manager.system_tools, state, config)
