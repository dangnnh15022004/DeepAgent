"""MCP Manager — manages connections and tools from MCP servers."""

import os
import signal
import subprocess

from langchain_mcp_adapters.sessions import StdioConnection
from langchain_mcp_adapters.tools import load_mcp_tools

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)


MCP_SERVERS = [
    ("deeptrace", "src.agent.tools.deeptrace_mcp_server"),
    ("system", "src.agent.tools.system_mcp_server"),
    ("deepsaleops", "src.agent.tools.deepsaleops_mcp_server"),
]


def _kill_orphan_subprocesses():
    """Kill any leftover MCP server subprocesses from a previous server start.

    langchain_mcp_adapters' load_mcp_tools uses `async with create_session(...)`
    which closes the client session but the stdio subprocess may keep running.
    After uvicorn --reload, multiple stale subprocesses can pile up, all bound
    to the same port. Kill them all to ensure a clean slate.
    """
    for name, module in MCP_SERVERS:
        try:
            subprocess.run(
                ["pkill", "-9", "-f", module],
                check=False,
                timeout=5,
            )
        except Exception as e:
            print(f"[MCP MANAGER] pkill {module} failed: {e}")


class DeepAIManager:
    def __init__(self):
        self.deeptrace_tools: list = []
        self.system_tools: list = []
        self.deepsaleops_tools: list = []

    async def _load(self, name: str, module: str):
        try:
            tools = await load_mcp_tools(
                None,
                connection=StdioConnection(
                    transport="stdio",
                    command="python3",
                    args=["-m", module],
                    cwd=PROJECT_ROOT,
                    env={**os.environ, "PYTHONPATH": PROJECT_ROOT},
                ),
            )
            print(f"[MCP MANAGER] {name} loaded {len(tools)} tools")
            return tools
        except Exception as e:
            print(f"[MCP MANAGER] {name} load failed: {e}")
            return []

    async def start(self):
        # Always clean up any orphans first.
        _kill_orphan_subprocesses()

        self.deeptrace_tools = await self._load("deeptrace", "src.agent.tools.deeptrace_mcp_server")
        self.system_tools = await self._load("system", "src.agent.tools.system_mcp_server")
        self.deepsaleops_tools = await self._load("deepsaleops", "src.agent.tools.deepsaleops_mcp_server")

        if not self.deepsaleops_tools:
            raise RuntimeError(
                "[MCP MANAGER] deepsaleops_tools is EMPTY — LLM has no tools to call. "
                "Check that deepsaleops_mcp_server.py starts without errors."
            )

        print(
            f"[MCP MANAGER] Started — deeptrace={len(self.deeptrace_tools)}, "
            f"system={len(self.system_tools)}, "
            f"deepsaleops={len(self.deepsaleops_tools)}"
        )

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> str:
        """Directly invoke a tool by name, bypassing the LLM. Used by the meta path."""
        import json
        tool_map = {
            "deeptrace": self.deeptrace_tools,
            "system": self.system_tools,
            "deepsaleops": self.deepsaleops_tools,
        }
        tools = tool_map.get(server_name, [])
        for tool in tools:
            if tool.name == tool_name:
                try:
                    result = await tool.ainvoke(arguments)
                    print(f"[MCP MANAGER] call_tool {server_name}.{tool_name} -> type={type(result).__name__}")
                    print(f"[MCP MANAGER] result preview: {str(result)[:300]}")

                    # langchain_mcp_adapters wraps tool results in MCP content-block format:
                    #   [{'type': 'text', 'text': '...json string...'}]
                    # We need to extract the inner text and return it as a JSON string.
                    if isinstance(result, list) and result:
                        first = result[0]
                        if isinstance(first, dict) and first.get("type") == "text":
                            text = first.get("text", "")
                            print(f"[MCP MANAGER] extracted MCP text block, len={len(text)}")
                            return text  # already a JSON string

                    if isinstance(result, str):
                        return result

                    # Fallback: serialize dict/list
                    return json.dumps(result, ensure_ascii=False)
                except Exception as e:
                    return json.dumps({"error": str(e)})
        return json.dumps({"error": f"Tool '{tool_name}' not found on server '{server_name}'"})

    async def stop(self):
        self.deeptrace_tools = []
        self.system_tools = []
        self.deepsaleops_tools = []
        _kill_orphan_subprocesses()
        print("[MCP MANAGER] Stopped.")


mcp_manager = DeepAIManager()
