from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from src.agent.memory.state import DeepAIState
from src.agent.core.planner import planner_node
from src.agent.core.synthesizer import synthesizer_node, router_hub_node
from src.agent.core.workers import (
    deepsaleops_agent, deepsaleops_tools_node,
    deeptrace_agent, deeptrace_tools_node,
    sys_agent, sys_tools_node,
    user_input_gate,
    make_decision,
)

# MemorySaver: persists state across interrupts/resumes within the server process.
# LangSmith single-trace is handled via parent_run_id in chat.py (not here).
agentic_graph = None  # lazy init


def route_to_next_task(state: DeepAIState):
    """Route pending_tasks to the next sub-agent node, or fall through to synthesizer."""
    tasks = state.get("pending_tasks") or []
    if not tasks:
        return "synthesizer"
    next_task = tasks[0]
    if next_task in ("DeepsaleopsAgent", "SysAgent", "DeeptraceAgent"):
        return next_task
    return "synthesizer"


if agentic_graph is None:
    _workflow = StateGraph(DeepAIState)

    # Nodes
    _workflow.add_node("planner", planner_node)
    _workflow.add_node("synthesizer", synthesizer_node)
    _workflow.add_node("router_hub", router_hub_node)
    _workflow.add_node("user_input_gate", user_input_gate)

    # Sub-agent nodes
    agent_node_map = {
        "DeepsaleopsAgent": (deepsaleops_agent, deepsaleops_tools_node),
        "SysAgent": (sys_agent, sys_tools_node),
        "DeeptraceAgent": (deeptrace_agent, deeptrace_tools_node),
    }
    for agent_node, (agent_fn, tools_fn) in agent_node_map.items():
        _workflow.add_node(agent_node, agent_fn)
        _workflow.add_node(f"{agent_node}Tools", tools_fn)

    # Edges
    _workflow.add_edge(START, "planner")
    _workflow.add_edge("planner", "router_hub")

    # After router_hub, route to next task
    router_map = {name: name for name in agent_node_map}
    router_map["synthesizer"] = "synthesizer"
    _workflow.add_conditional_edges("router_hub", route_to_next_task, router_map)

    # Each agent: agent -> tools (if tool_calls) -> decision -> back to agent
    # OR -> user_input_gate (if place_order returned select_* and we need user pick)
    # OR -> router_hub (if no tool_calls)
    for agent_node, (agent_fn, tools_fn) in agent_node_map.items():
        tools_node = f"{agent_node}Tools"
        decision_map = {
            tools_node: tools_node,
            "user_input_gate": "user_input_gate",
            "router_hub": "router_hub",
        }
        _workflow.add_conditional_edges(
            agent_node,
            lambda s, n=agent_node, t=tools_node: make_decision(s, n, t),
            decision_map,
        )
        _workflow.add_conditional_edges(
            tools_node,
            lambda s, n=agent_node, t=tools_node: make_decision(s, n, t),
            decision_map,
        )

    _workflow.add_edge("user_input_gate", "router_hub")
    _workflow.add_edge("synthesizer", END)

    agentic_graph = _workflow.compile(checkpointer=MemorySaver())
