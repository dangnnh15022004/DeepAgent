"""
System MCP Server - user profile, contact info, app introduction.
All @mcp.tool() — no more @langchain @tool decorators.
"""

import json
from mcp.server.fastmcp import FastMCP

from src.agent.tools.common.api_utils import api_get


mcp = FastMCP("System_Tools_Server")


@mcp.tool()
async def get_my_profile(access_token: str) -> str:
    """Get the authenticated user's profile: email, role, and companyList.

    CRITICAL RULES:
    - access_token is REQUIRED (automatically injected by the system).
    - Call this FIRST when the user refers to "my products", "my company", "my batches",
      or when the answer depends on identity/role.

    Returns 'companyId' for each company — use it directly as input to other tools
    (e.g. summarize_company_products). 'primaryCompany' is the first active company,
    ready to pass forward without asking the user.

    Args:
        access_token: JWT token from the authenticated session.
    """
    raw = await api_get("/api/v1/auth/me", access_token)
    try:
        data = json.loads(raw)
        if "error" in data:
            return raw

        company_list = [
            {
                "companyId": c.get("companyId"),
                "companyName": c.get("companyName"),
                "companyAddress": c.get("companyAddress"),
                "isActive": c.get("isActive"),
            }
            for c in data.get("companyList", [])
        ]
        active_companies = [c for c in company_list if c.get("isActive") == 1]
        primary_company = active_companies[0] if active_companies else (
            company_list[0] if company_list else None
        )

        return json.dumps({
            "userId": data.get("userId") or data.get("user_id"),
            "email": data.get("email"),
            "role": data.get("role"),
            "companyList": company_list,
            "primaryCompany": primary_company,
        }, ensure_ascii=False)
    except Exception:
        return raw


@mcp.tool()
async def get_contact_info(access_token: str) -> str:
    """Get DeepPro's contact information: company name, address, tax code,
    hotline, sales contact, and technical support.

    Use this when the user asks about how to contact DeepPro,
    who to reach for sales, or what the company info / hotline is.

    Args:
        access_token: JWT token from the authenticated session.
    """
    raw = await api_get("/api/v1/contact/contact-info", access_token)
    try:
        data = json.loads(raw)
        if "error" in data:
            return raw
        return json.dumps({
            "companyName": data.get("companyName"),
            "address": data.get("address"),
            "taxCode": data.get("taxCode"),
            "representativeName": data.get("representativeName"),
            "hotlineNumber": data.get("hotlineNumber"),
            "salesName": data.get("salesName"),
            "salesContactNumber": data.get("salesContactNumber"),
            "salesContactEmail": data.get("salesContactEmail"),
            "technicalSupportName": data.get("technicalSupportName"),
            "technicalSupportContactNumber": data.get("technicalSupportContactNumber"),
            "technicalSupportEmail": data.get("technicalSupportEmail"),
        }, ensure_ascii=False)
    except Exception:
        return raw


@mcp.tool()
async def get_app_introduction(access_token: str) -> str:
    """Get the DeepTrace app introduction text and version info.

    Use this when the user asks about what DeepTrace is, what the app does,
    or wants to see an overview / description of the application.

    Args:
        access_token: JWT token from the authenticated session.
    """
    raw = await api_get("/api/v1/native/introduction", access_token)
    try:
        data = json.loads(raw)
        if "error" in data:
            return raw
        return json.dumps({
            "version": data.get("version"),
            "language": data.get("language"),
            "description": data.get("description"),
            "created": data.get("created"),
        }, ensure_ascii=False)
    except Exception:
        return raw


if __name__ == "__main__":
    mcp.run(transport="stdio")
