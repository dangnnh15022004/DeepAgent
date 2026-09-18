"""Reproduce EXACTLY what the chatbot's list_user_addresses tool sends.

This script bypasses MCP/uvicorn/LangGraph and calls the same code path
(`api_utils.api_get`) the chatbot uses, with the SAME headers the tool sends.
"""

import asyncio
import json

# Use the EXACT same api_utils the MCP server imports
from src.agent.tools.common.api_utils import api_get


async def main():
    access_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkZWVwdHJhY2VfdXNlcklkIjoiYzNmZTMzZjgtOGI1NS00NmQyLWJmYjAtNWYwOGRmMGY5NjMyIiwiZGVlcHRyYWNlX2VtYWlsIjoidGVzdGN1c3RvbWVyQGdtYWlsLmNvbSIsImRlZXB0cmFjZV9yb2xlIjoiQ3VzdG9tZXIiLCJleHAiOjE3ODk0NzEyMzMsImlzcyI6IkRlZXBUcmFjZSIsImF1ZCI6IkRlZXBUcmFjZUF1ZGllbmNlIn0.VzOyLGVeASpgNC8jOM8FGAQEYl75uylV_P2bQ-5_CdU"

    print("=" * 60)
    print("Test 1: list_user_addresses (the one that returns 401)")
    print("=" * 60)
    result = await api_get("/v1/user/address", access_token)
    print(json.dumps(json.loads(result), indent=2, ensure_ascii=False)[:500])

    print()
    print("=" * 60)
    print("Test 2: randomize (working baseline)")
    print("=" * 60)
    from src.agent.tools.common.api_utils import api_post
    result = await api_post("/v1/product/on-sale/randomize", access_token, {"seed": 0})
    print(json.loads(result)["items"][0]["productName"])
    print("✅ OK")

    print()
    print("=" * 60)
    print("Test 3: auth/me (working baseline)")
    print("=" * 60)
    result = await api_get("/v1/auth/me", access_token)
    print(json.dumps(json.loads(result), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
