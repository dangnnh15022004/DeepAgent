"""System MCP Server."""

import json

from mcp.server.fastmcp import FastMCP

from src.agent.tools.common.api_utils import api_get

mcp = FastMCP("System_Tools_Server")


@mcp.tool()
async def get_my_profile(access_token: str) -> str:
    """Get the authenticated user's profile: email, role, companyList.

    REQUIRED: access_token.
    """
    return await api_get("/v1/auth/me", access_token)


@mcp.tool()
async def get_contact_info(access_token: str) -> str:
    """Get DeepPro contact info: company, hotline, sales, technical support.

    REQUIRED: access_token.
    """
    return await api_get("/v1/contact/contact-info", access_token)


@mcp.tool()
async def get_app_introduction(access_token: str) -> str:
    """Get the DeepTrace app introduction text and version info.

    REQUIRED: access_token.
    """
    return await api_get("/v1/native/introduction", access_token)


if __name__ == "__main__":
    mcp.run(transport="stdio")
