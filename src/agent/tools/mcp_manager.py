import os
import sys
from contextvars import ContextVar
from typing import Any

from langchain_mcp_adapters.interceptors import (
    MCPToolCallRequest,
    MCPToolCallResult,
)
from langchain_mcp_adapters.sessions import (
    Connection,
    StdioConnection,
    create_session,
)
from langchain_mcp_adapters.tools import (
    convert_mcp_tool_to_langchain_tool,
)
from langchain_core.tools import BaseTool


PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)


# Bound per request from the worker nodes; the LLM never reads this nor
# composes it into a tool call. This is the single source of truth for the JWT
# used by every MCP tool — eliminating the "LLM paraphrases the token" failure
# mode that caused the intermittent 401s on Deeptrace calls.
_access_token_ctx: ContextVar[str] = ContextVar("deeptrace_access_token", default="")


def _load_mcp_server(module: str) -> StdioConnection:
    env = os.environ.copy()
    env["PYTHONPATH"] = PROJECT_ROOT

    return StdioConnection(
        transport="stdio",
        command=sys.executable,
        args=["-m", f"src.agent.tools.{module}"],
        cwd=PROJECT_ROOT,
        env=env,
    )


async def _token_injector(
    request: MCPToolCallRequest,
    handler,
) -> MCPToolCallResult:
    """
    Interceptor that injects the current request's access_token into the
    MCP tool call args before it reaches the server.
    """
    if "access_token" not in request.args:
        request = request.override(args={**request.args, "access_token": _access_token_ctx.get()})
    return await handler(request)


def _strip_access_token_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of an MCP JSON schema with the `access_token` field removed."""
    if not isinstance(schema, dict):
        return schema
    properties = schema.get("properties", {})
    cleaned_props = {k: v for k, v in properties.items() if k != "access_token"}
    required = [r for r in schema.get("required", []) if r != "access_token"]
    new_schema = dict(schema)
    new_schema["properties"] = cleaned_props
    if required:
        new_schema["required"] = required
    else:
        new_schema.pop("required", None)
    return new_schema


async def load_tools_clean(connection: Connection, server_name: str) -> list[BaseTool]:
    """
    Load MCP tools with two safety nets:
      1. `access_token` is removed from the JSON schema, so the LLM never sees it
         as a tool argument it must supply.
      2. A `token_injector` interceptor fills in the token from the per-request
         ContextVar right before the MCP server call is made.
    """
    async with create_session(connection) as session:
        await session.initialize()

        cursor = None
        mcp_tools = []
        while True:
            page = await session.list_tools(cursor=cursor)
            mcp_tools.extend(page.tools)
            if not page.nextCursor:
                break
            cursor = page.nextCursor

        clean_tools = []
        for t in mcp_tools:
            stripped_schema = _strip_access_token_from_schema(t.inputSchema or {})
            try:
                patched_tool = t.model_copy(update={"inputSchema": stripped_schema})
            except Exception:
                from mcp import types as mcp_types
                patched_tool = mcp_types.Tool(
                    name=t.name,
                    description=t.description,
                    inputSchema=stripped_schema,
                )
            clean_tools.append(
                convert_mcp_tool_to_langchain_tool(
                    None,
                    patched_tool,
                    connection=connection,
                    server_name=server_name,
                    tool_interceptors=[_token_injector],
                )
            )
        return clean_tools


class DeepAIManager:
    def __init__(self):
        self.deeptrace_tools: list = []
        self.system_tools: list = []
        self.deepsaleops_tools: list = []
        self.tools: list = []
        self._sessions: dict = {}

    async def start(self):
        trace_conn = _load_mcp_server("deeptrace_mcp_server")
        self.deeptrace_tools = await load_tools_clean(trace_conn, server_name="deeptrace")
        self._sessions["deeptrace"] = trace_conn

        system_conn = _load_mcp_server("system_mcp_server")
        self.system_tools = await load_tools_clean(system_conn, server_name="system")
        self._sessions["system"] = system_conn

        self.deepsaleops_tools = []
        self.tools = self.deeptrace_tools + self.system_tools + self.deepsaleops_tools

        print(
            f"[MCP MANAGER] deeptrace={len(self.deeptrace_tools)}, "
            f"system={len(self.system_tools)}, "
            f"deepsaleops={len(self.deepsaleops_tools)}"
        )

    def bind_access_token(self, token: str):
        """Bind the access token for the CURRENT async task."""
        return _access_token_ctx.set(token)

    async def stop(self):
        for name, session in self._sessions.items():
            try:
                await session.__aexit__(None, None, None)
            except Exception:
                pass
        self._sessions.clear()


mcp_manager = DeepAIManager()
