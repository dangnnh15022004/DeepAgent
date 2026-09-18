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


# place_order status values that require user to pick from options
PLACE_ORDER_HIL_STATUSES = (
    "select_product", "select_variant", "select_address",
    "select_sale",
)
PLACE_ORDER_STATUS_TO_FLAG = {
    "select_product": "pick_product",
    "select_variant": "pick_variant",
    "select_address": "pick_address",
    "select_sale": "pick_sale",
}


def _detect_place_order_result(state: DeepAIState, agent_name: str) -> str | None:
    """Check if the most recent tool call was place_order returning a select_* status.
    Only applies to DeepsaleopsAgent — other agents should never trigger HIL for orders."""
    if agent_name != "DeepsaleopsAgent":
        return None

    messages = state.get("messages", [])
    last_tc_name = None
    last_tr_status = None
    # Find last tool message and its corresponding call name
    last_tool_msg = None
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "tool":
            last_tool_msg = msg
            break
    if not last_tool_msg:
        return None
    tc_id = getattr(last_tool_msg, "tool_call_id", None)
    for m in messages:
        if getattr(m, "type", None) == "ai" and getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                if tc.get("id") == tc_id:
                    last_tc_name = tc.get("name", "")
                    break
            if last_tc_name:
                break
    # Parse status from tool message content
    content = last_tool_msg.content
    try:
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    import json
                    data = json.loads(item["text"])
                    last_tr_status = data.get("status")
                    break
        elif isinstance(content, str):
            import json
            data = json.loads(content)
            last_tr_status = data.get("status")
    except Exception:
        pass
    print(f"[Workers] _detect_place_order_result: agent={agent_name}, tc={last_tc_name}, status={last_tr_status}")
    if last_tc_name == "place_order" and last_tr_status in PLACE_ORDER_HIL_STATUSES:
        return PLACE_ORDER_STATUS_TO_FLAG.get(last_tr_status)
    return None

READ_ONLY_PREFIXES = ("get_", "list_", "fetch_", "search_", "read_", "find_", "count_", "place_")
# place_order is a state machine (search→select→confirm), not a direct destructive write


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
    if not _has_write_tool_call(state):
        return await ToolNode(tools).ainvoke(state)

    decision = interrupt({"reason": "write_action_requires_confirmation"})

    # Handle user selection from frontend options card
    selected_value = None
    if isinstance(decision, dict):
        selected_value = decision.get("selected_value")
        decision = decision.get("action", "confirm")

    if decision == "confirm":
        # Inject selected_value into messages so agent can use it
        if selected_value:
            messages = state.get("messages", [])
            last = messages[-1] if messages else None
            if last and getattr(last, "tool_calls", None):
                # Patch tool args with selected value
                for tc in last.tool_calls:
                    if is_write_tool_name(tc.get("name", "")):
                        if tc.get("args"):
                            tc["args"]["_user_selected"] = selected_value
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


# ─────────────────────────────────────────────────────────────────────────────
# USER INPUT GATE (for read tools that need user to pick)
# ─────────────────────────────────────────────────────────────────────────────

# Map of read tool name → state flag to set (agent pauses for user selection).
# Both agents use these tools in different modes:
#  - DeepsaleopsAgent: needs a CLICKABLE option card (e.g. "pick variant X to
#    add to cart", "pick address Y to ship to"). Force pause.
#  - DeeptraceAgent:   uses these tools for *informational* queries (e.g. "thông
#    tin lô hàng của sản phẩm Gạo Jasmine") and the LLM is expected to summarize
#    results as Markdown — no need to pause and force the user to click.
#
# So the PICK behavior is per-agent (see _detect_pick_after_tools below).
PICK_TOOLS = {
    "list_on_sale_product_variants": "pick_variant",
    "list_user_addresses": "pick_address",
    "get_product_batches": "pick_batch",
}

# Agents whose pickable tools are exposed as click-option cards to the user.
# DeeptraceAgent is DELIBERATELY excluded — when its LLM calls
# get_product_batches (or any other PICK_TOOL), the result is summarized as
# plain Markdown instead of forcing a click-option card.
PICK_AGENTS = {"DeepsaleopsAgent"}


async def user_input_gate(state: DeepAIState) -> dict:
    """Pause after a read tool that produced pickable options.

    The interrupt payload carries the awaiting flag so the chat endpoint can
    compute options to show in the UI. Resume value is the user's selection (UUID).
    """
    payload = interrupt({"reason": "awaiting_user_pick"})

    selected_value = None
    if isinstance(payload, dict):
        selected_value = payload.get("selected_value")
    elif isinstance(payload, str):
        selected_value = payload

    # Inject selection back as a HumanMessage so the agent can continue.
    from langchain_core.messages import HumanMessage
    msg = HumanMessage(content=f"User selected: {selected_value}")

    # Re-push DeepsaleopsAgent to pending_tasks so router_hub routes back to it.
    # Without this, the graph falls through to synthesizer after resume.
    pending = list(state.get("pending_tasks", []) or [])
    if "DeepsaleopsAgent" not in pending:
        pending.append("DeepsaleopsAgent")

    return {
        "messages": [msg],
        "awaiting_user_pick": None,
        "pending_tasks": pending,
    }


def _detect_pick_after_tools(state: DeepAIState, agent_name: str) -> str | None:
    """Detect if the most recent tool call is a PICK_TOOL called by an agent
    that is allowed to pause for a user pick.

    Only agents in `PICK_AGENTS` (DeepsaleopsAgent) trigger the
    click-option card. DeeptraceAgent's calls to the same tools fall through
    and the LLM summarizes the result as Markdown instead — the user is NOT
    forced to click.

    Returns the state flag to set on `awaiting_user_pick`, or None.
    """
    messages = state.get("messages", [])
    for msg in reversed(messages):
        if getattr(msg, "type", None) != "tool":
            continue
        # Find the corresponding tool call
        tool_call_id = getattr(msg, "tool_call_id", None)
        for m in messages:
            if getattr(m, "type", None) == "ai" and getattr(m, "tool_calls", None):
                for tc in m.tool_calls:
                    if tc.get("id") == tool_call_id:
                        name = tc.get("name", "")
                        if name in PICK_TOOLS:
                            if agent_name not in PICK_AGENTS:
                                print(
                                    f"[Workers] {agent_name}: tool={name} is pickable, "
                                    f"but {agent_name} is NOT in PICK_AGENTS → skip pause, "
                                    f"LLM will summarize as Markdown."
                                )
                                return None
                            print(f"[Workers] {agent_name}: detected PICK_TOOL={name} → {PICK_TOOLS[name]}")
                            return PICK_TOOLS[name]
                        else:
                            print(f"[Workers] {agent_name}: last tool={name} (not in PICK_TOOLS)")
        break  # only check last tool message
    return None


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def make_decision(state: DeepAIState, agent_name: str, tool_node: str):
    messages = state["messages"]
    last_message = messages[-1]

    # If place_order / pickable tool returned and a HIL flag is set in state,
    # route to user_input_gate to pause for the user to pick.
    pick_flag = _detect_pick_after_tools(state, agent_name)
    if pick_flag:
        state["awaiting_user_pick"] = pick_flag
        return "user_input_gate"

    place_order_flag = _detect_place_order_result(state, agent_name)
    if place_order_flag:
        print(f"[Workers] {agent_name}: place_order returned {place_order_flag} → awaiting_user_pick")
        state["awaiting_user_pick"] = place_order_flag
        return "user_input_gate"

    agent_msg_count = 0
    for m in reversed(messages[-10:]):
        if getattr(m, "tool_calls", None):
            agent_msg_count += 1
        if agent_msg_count >= 10:
            break

    if agent_msg_count >= 10:
        print(f"[System] {agent_name} exceeded max tool calls ({agent_msg_count}). Safety override.")
        return "router_hub"

    return tool_node if getattr(last_message, "tool_calls", None) else "router_hub"


def _pop_pending(state: DeepAIState, task_name: str) -> list:
    pending = list(state.get("pending_tasks", []))
    if task_name in pending:
        pending.remove(task_name)
    return pending


def _build_agent_prompt(base: str, token: str) -> str:
    return base + f"""

[SYSTEM] Authentication info:
- access_token (REQUIRED for all tools): {token}

WHEN CALLING TOOL: Always pass the full access_token (do not truncate)."""


def _sanitize_messages(messages):
    """Sanitize messages before sending to LLM.

    OpenAI requires every `ai` message with `tool_calls` to be immediately
    followed by `tool` messages for every tool_call_id. If a previous run
    errored (e.g. HTTP 403, timeout) the tool response may be missing,
    which makes the LLM API reject the whole request with 400.

    Strategy: keep all messages but DROP any `ai` message whose tool_calls
    have no matching `tool` response. This preserves the rest of the
    conversation (HumanMessage, assistant replies, etc.) without orphan
    tool_call_ids.
    """
    # Pass 1: collect all tool_call_ids that have a corresponding tool message.
    responded_tool_ids: set[str] = set()
    for m in messages:
        if getattr(m, "type", None) == "tool":
            tcid = getattr(m, "tool_call_id", None)
            if tcid:
                responded_tool_ids.add(tcid)

    # Pass 2: build sanitized list, skipping ai messages with orphan tool_calls.
    sanitized = []
    for m in messages:
        if getattr(m, "type", None) == "ai" and getattr(m, "tool_calls", None):
            orphan = any(
                (tc.get("id") and tc["id"] not in responded_tool_ids)
                for tc in (m.tool_calls or [])
            )
            if orphan:
                print(
                    f"[Workers] _sanitize: dropping ai message with {len(m.tool_calls or [])} "
                    f"orphan tool_call(s) (no matching tool result)."
                )
                continue  # drop this ai message entirely
        sanitized.append(m)
    return sanitized


def get_access_token(config: RunnableConfig) -> str:
    return config.get("configurable", {}).get("access_token", "")


# ─────────────────────────────────────────────────────────────────────────────
# DEEPSALEOPS AGENT
# ─────────────────────────────────────────────────────────────────────────────

async def deepsaleops_agent(state: DeepAIState, config: RunnableConfig):
    token = get_access_token(config)
    agent_llm = llm.bind_tools(mcp_manager.deepsaleops_tools)
    prompt = _build_agent_prompt(DEEPSALEOPS_AGENT_PROMPT, token)

    # Safety: hard-stop if we've gone around too many times (LLM keeps calling tools)
    msgs = state.get("messages", [])
    recent_tool_calls = sum(1 for m in msgs[-8:] if getattr(m, "tool_calls", None))
    if recent_tool_calls >= 6:
        return {
            "agent_reports": ["[Deepsaleops] Stopped: too many consecutive tool calls."],
            "pending_tasks": _pop_pending(state, "DeepsaleopsAgent"),
        }

    response = await agent_llm.ainvoke(
        [SystemMessage(content=prompt)] + _sanitize_messages(msgs)
    )

    if response.tool_calls:
        return {"messages": [response]}

    return {
        "agent_reports": [f"[Deepsaleops] {response.content}"],
        "pending_tasks": _pop_pending(state, "DeepsaleopsAgent"),
    }


async def deepsaleops_tools_node(state: DeepAIState) -> dict:
    result = await _guarded_tools_node(mcp_manager.deepsaleops_tools, state)

    # Debug: print what the tool returned
    if result.get("messages"):
        for m in result["messages"]:
            if getattr(m, "type", None) == "tool":
                content = m.content if isinstance(m.content, str) else str(m.content)
                print(f"[Workers] deepsaleops tool result (first 500): {content[:500]}")
    # If the last tool call was place_order and returned select_*, we let
    # make_decision detect it and route to user_input_gate (no interrupt here).
    # The chat endpoint will pause the conversation and ask the user to pick.
    place_order_flag = _detect_place_order_result(state, "DeepsaleopsAgent")
    if place_order_flag:
        print(f"[Workers] deepsaleops_tools: place_order returned {place_order_flag} → set flag for routing")
        result["awaiting_user_pick"] = place_order_flag
    return result


# ─────────────────────────────────────────────────────────────────────────────
# DEEPTRACE AGENT
# ─────────────────────────────────────────────────────────────────────────────

async def deeptrace_agent(state: DeepAIState, config: RunnableConfig):
    token = get_access_token(config)
    agent_llm = llm.bind_tools(mcp_manager.deeptrace_tools)
    prompt = _build_agent_prompt(DEEPTRACE_AGENT_PROMPT, token)

    msgs = state.get("messages", [])
    recent_tool_calls = sum(1 for m in msgs[-8:] if getattr(m, "tool_calls", None))
    if recent_tool_calls >= 6:
        return {
            "agent_reports": ["[Deeptrace] Stopped: too many consecutive tool calls."],
            "pending_tasks": _pop_pending(state, "DeeptraceAgent"),
        }

    response = await agent_llm.ainvoke(
        [SystemMessage(content=prompt)] + _sanitize_messages(msgs)
    )

    if response.tool_calls:
        return {"messages": [response]}

    return {
        "agent_reports": [f"[Deeptrace] {response.content}"],
        "pending_tasks": _pop_pending(state, "DeeptraceAgent"),
    }


async def deeptrace_tools_node(state: DeepAIState) -> dict:
    return await _guarded_tools_node(mcp_manager.deeptrace_tools, state)


# ─────────────────────────────────────────────────────────────────────────────
# SYS AGENT
# ─────────────────────────────────────────────────────────────────────────────

async def sys_agent(state: DeepAIState, config: RunnableConfig):
    token = get_access_token(config)
    agent_llm = llm.bind_tools(mcp_manager.system_tools)
    prompt = _build_agent_prompt(SYS_AGENT_PROMPT, token)

    msgs = state.get("messages", [])
    recent_tool_calls = sum(1 for m in msgs[-8:] if getattr(m, "tool_calls", None))
    if recent_tool_calls >= 6:
        return {
            "agent_reports": ["[Sys] Stopped: too many consecutive tool calls."],
            "pending_tasks": _pop_pending(state, "SysAgent"),
        }

    response = await agent_llm.ainvoke(
        [SystemMessage(content=prompt)] + _sanitize_messages(msgs)
    )

    if response.tool_calls:
        return {"messages": [response]}

    return {
        "agent_reports": [f"[Sys] {response.content}"],
        "pending_tasks": _pop_pending(state, "SysAgent"),
    }


async def sys_tools_node(state: DeepAIState) -> dict:
    return await _guarded_tools_node(mcp_manager.system_tools, state)
