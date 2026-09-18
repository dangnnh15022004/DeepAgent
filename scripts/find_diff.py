"""Compare EXACTLY what curl does vs what our tool does.

Replicate the 200 OK curl request byte-by-byte, then poke at it line by line
until we find what triggers 401.
"""

import asyncio
import httpx
import json

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkZWVwdHJhY2VfdXNlcklkIjoiYzNmZTMzZjgtOGI1NS00NmQyLWJmYjAtNWYwOGRmMGY5NjMyIiwiZGVlcHRyYWNlX2VtYWlsIjoidGVzdGN1c3RvbWVyQGdtYWlsLmNvbSIsImRlZXB0cmFjZV9yb2xlIjoiQ3VzdG9tZXIiLCJleHAiOjE3ODk0NzEyMzMsImlzcyI6IkRlZXBUcmFjZSIsImF1ZCI6IkRlZXBUcmFjZUF1ZGllbmNlIn0.VzOyLGVeASpgNC8jOM8FGAQEYl75uylV_P2bQ-5_CdU"

URL = "https://deepsalesops-dev-api.deep.com.vn/api/v1/user/address"


async def try_request(label: str, send_kwargs: dict):
    print(f"\n--- {label} ---")
    try:
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            res = await client.get(URL, **send_kwargs)
        print(f"  Status: {res.status_code}")
        print(f"  Body[:200]: {res.text[:200]}")
        if res.status_code == 401:
            # show all response headers
            print(f"  Response headers: {dict(res.headers)}")
    except Exception as e:
        print(f"  Exception: {e}")


async def main():
    base = {"headers": {"Authorization": f"Bearer {TOKEN}"}}

    # 1. Play with headers one at a time
    await try_request("1. Only Authorization",
                      {"headers": {"Authorization": f"Bearer {TOKEN}"}})

    await try_request("2. + Content-Type: application/json",
                      {"headers": {"Authorization": f"Bearer {TOKEN}",
                                   "Content-Type": "application/json"}})

    await try_request("3. + accept: application/json",
                      {"headers": {"Authorization": f"Bearer {TOKEN}",
                                   "Accept": "application/json"}})

    await try_request("4. + user-agent python-httpx",
                      {"headers": {"Authorization": f"Bearer {TOKEN}",
                                   "User-Agent": "python-httpx/0.28.1"}})

    # 5. Try TLS fingerprint — common cause for "same code, different result"
    # By using curl_cffi which uses libcurl fingerprint (browsers tend to pass)
    try:
        from curl_cffi.requests import AsyncSession
        async with AsyncSession(verify=False) as s:
            r = await s.get(URL, headers={"Authorization": f"Bearer {TOKEN}"},
                            impersonate="chrome124")
            print(f"\n5. curl_cffi chrome → {r.status_code}")
            print(f"   Body[:200]: {r.text[:200]}")
    except ImportError:
        print("\n5. curl_cffi NOT INSTALLED — skipping")


asyncio.run(main())
