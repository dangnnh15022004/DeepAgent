"""Shared API utilities for all DeepAgent MCP servers."""

import json
import httpx

def get_api_base_url() -> str:
    """Base URL for the DeepTrace backend."""
    return "https://dev-api-deeptrace.deepprotech.com"

def get_timeout() -> float:
    return 120.0

def get_auth_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        "Connection": "keep-alive",
        # Giả lập trình duyệt Chrome trên macOS để bypass WAF/Firewall
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

async def api_get(path: str, access_token: str, params: dict = None) -> str:
    base = get_api_base_url()
    timeout = get_timeout()
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
            res = await client.get(
                base + path,
                headers=get_auth_headers(access_token),
                params=params or {},
            )
        if res.status_code == 200:
            return res.text
        # Surface explicit auth/permission errors so tools/LLM can avoid hallucination
        if res.status_code == 401:
            return json.dumps({"error": "AUTHENTICATION_ERROR", "status": 401, "detail": res.text})
        if res.status_code == 403:
            return json.dumps({"error": "AUTHORIZATION_ERROR", "status": 403, "detail": res.text})
        return json.dumps({"error": f"API Error: {res.status_code}", "status": res.status_code, "detail": res.text})
    except httpx.TimeoutException:
        return json.dumps({"error": "Timeout", "detail": "Request timed out"})
    except Exception as e:
        return json.dumps({"error": str(e)})

async def api_post(path: str, access_token: str, json_data: dict = None) -> str:
    base = get_api_base_url()
    timeout = get_timeout()
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
            res = await client.post(
                base + path,
                headers=get_auth_headers(access_token),
                json=json_data or {},
            )
        if res.status_code == 200:
            return res.text
        if res.status_code == 401:
            return json.dumps({"error": "AUTHENTICATION_ERROR", "status": 401, "detail": res.text})
        if res.status_code == 403:
            return json.dumps({"error": "AUTHORIZATION_ERROR", "status": 403, "detail": res.text})
        return json.dumps({"error": f"API Error: {res.status_code}", "status": res.status_code, "detail": res.text})
    except httpx.TimeoutException:
        return json.dumps({"error": "Timeout", "detail": "Request timed out"})
    except Exception as e:
        return json.dumps({"error": str(e)})

async def api_patch(path: str, access_token: str, json_data: dict = None) -> str:
    base = get_api_base_url()
    timeout = get_timeout()
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
            res = await client.patch(
                base + path,
                headers=get_auth_headers(access_token),
                json=json_data or {},
            )
        if res.status_code == 200:
            return res.text
        if res.status_code == 401:
            return json.dumps({"error": "AUTHENTICATION_ERROR", "status": 401, "detail": res.text})
        if res.status_code == 403:
            return json.dumps({"error": "AUTHORIZATION_ERROR", "status": 403, "detail": res.text})
        return json.dumps({"error": f"API Error: {res.status_code}", "status": res.status_code, "detail": res.text})
    except httpx.TimeoutException:
        return json.dumps({"error": "Timeout", "detail": "Request timed out"})
    except Exception as e:
        return json.dumps({"error": str(e)})