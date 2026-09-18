"""Compare headers between browser-like request and python httpx default."""

import asyncio
import httpx
import json

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkZWVwdHJhY2VfdXNlcklkIjoiYzNmZTMzZjgtOGI1NS00NmQyLWJmYjAtNWYwOGRmMGY5NjMyIiwiZGVlcHRyYWNlX2VtYWlsIjoidGVzdGN1c3RvbWVyQGdtYWlsLmNvbSIsImRlZXB0cmFjZV9yb2xlIjoiQ3VzdG9tZXIiLCJleHAiOjE3ODk0NzEyMzMsImlzcyI6IkRlZXBUcmFjZSIsImF1ZCI6IkRlZXBUcmFjZUF1ZGllbmNlIn0.VzOyLGVeASpgNC8jOM8FGAQEYl75uylV_P2bQ-5_CdU"


async def test(label: str, headers: dict):
    print(f"\n=== {label} ===")
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        res = await client.get(
            "https://deepsalesops-dev-api.deep.com.vn/api/v1/user/address",
            headers=headers,
        )
        print(f"  Status: {res.status_code}")
        body = res.text[:200]
        print(f"  Body: {body}")
        print(f"  Request headers sent: {dict(res.request.headers)}")


async def main():
    # 1. Minimal — like python httpx default
    await test("Minimal (python-httpx/0.x)", {
        "Authorization": f"Bearer {TOKEN}",
    })

    # 2. Browser-like — what 99g-agent was using (already removed)
    await test("Browser-like", {
        "Authorization": f"Bearer {TOKEN}",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        "Origin": "https://dev.deeppro.vn",
        "Referer": "https://dev.deeppro.vn/",
        "Content-Type": "application/json",
    })

    # 3. What curl sends
    await test("curl-like", {
        "Authorization": f"Bearer {TOKEN}",
        "accept": "text/plain",
        "user-agent": "curl/8.4.0",
    })

    # 4. POST randomize (the one that works) — see what headers make it succeed
    print("\n=== POST randomize (working) with minimal headers ===")
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        res = await client.post(
            "https://deepsalesops-dev-api.deep.com.vn/api/v1/product/on-sale/randomize",
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
            json={"seed": 0},
        )
        print(f"  Status: {res.status_code}")


asyncio.run(main())
