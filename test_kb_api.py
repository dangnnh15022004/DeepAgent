#!/usr/bin/env python3
"""Test KB API endpoint - uses settings from .env"""

import httpx
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.settings import settings


async def test_kb_api():
    print("=" * 50)
    print("Testing KB API")
    print("=" * 50)

    kb_url = settings.kb_service_url
    kb_key = settings.kb_service_api_key

    print(f"KB URL: {kb_url}")
    print(f"KB API Key: {kb_key[:30] if kb_key else 'NOT SET'}...")
    print()
    print(f"Azure Chat: {settings.azure_openai_chat_endpoint}")
    print(f"Azure Embedding: {settings.azure_openai_embedding_endpoint}")
    print()

    if not kb_url or not kb_key:
        print("ERROR: KB Service not configured in .env")
        return

    payload = {
        "question": "Deep Pro là gì?",
        "session_id": "test_session"
    }

    try:
        async with httpx.AsyncClient() as client:
            print("Sending request to KB API...")
            res = await client.post(
                kb_url,
                json=payload,
                headers={
                    "X-Api-Key": kb_key,
                    "Content-Type": "application/json"
                },
                timeout=30.0
            )

            print(f"Status Code: {res.status_code}")
            print(f"Response: {res.text}")
    except httpx.TimeoutException:
        print("ERROR: Request timeout")
    except Exception as e:
        print(f"ERROR: {e}")


if __name__ == "__main__":
    asyncio.run(test_kb_api())
