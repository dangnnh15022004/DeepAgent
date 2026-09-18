#!/usr/bin/env python3
"""Quick check: is Postgres pool initialized?"""
import httpx

r = httpx.get("http://localhost:8001/api/v1/chat/history?session_id=anything", timeout=10)
print("status:", r.status_code)
print("body:", r.text[:500])
