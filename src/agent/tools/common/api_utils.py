"""DeepAgent API utilities — shared for all MCP servers.

The DeepSalesOps dev backend issues JWT via HttpOnly cookies (`deeptrace_at`,
`deeptrace_rt`), not in the response body. We therefore maintain a process-wide
cookie jar that gets populated on first login and reused across requests.
"""

import json
import threading

import httpx

BASE_URL = "https://deepsalesops-dev-api.deep.com.vn/api"
LOGIN_URL = "https://deepsalesops-dev-api.deep.com.vn/api/v1/auth/login"


def get_timeout() -> float:
    return 120.0


# ── Cookie jar (process-wide, lazy login) ──────────────────────────────────
_jar: dict[str, str] = {}
_jar_lock = threading.Lock()
_logged_in = False
_current_user_id: str = ""


def _save_cookies(client: httpx.AsyncClient) -> None:
    """Persist cookies from a client response into the in-memory jar."""
    with _jar_lock:
        for name, value in client.cookies.items():
            _jar[name] = value
        global _logged_in
        if _jar:
            _logged_in = True


def _build_client() -> httpx.AsyncClient:
    """Build a client preloaded with cookies from the jar."""
    c = httpx.AsyncClient(timeout=get_timeout(), verify=False)
    if _jar:
        c.cookies.update(_jar)
    return c


def _ensure_login() -> None:
    """Trigger a fresh login (using a hardcoded test credential) when the jar
    is empty. Backend dev accepts only a fixed user for cookie issuance; we
    bake those creds in here so the rest of the app stays credential-free.
    The login response also carries the userId (in body) — we cache it so
    order-creation calls can pass it as required by the backend contract.
    """
    global _logged_in, _current_user_id
    with _jar_lock:
        if _logged_in and "deeptrace_at" in _jar:
            return
        _logged_in = False
        _jar.clear()
        _current_user_id = ""

    try:
        with httpx.Client(timeout=30.0, verify=False) as c:
            res = c.post(
                LOGIN_URL,
                json={"email": "sale.demo@deeptrace.com", "password": "testPassword@2003"},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
        if res.status_code == 200:
            for name, value in res.cookies.items():
                _jar[name] = value
            with _jar_lock:
                _logged_in = bool(_jar)
            # Backend puts userId in the login body, not in a JWT we can decode.
            try:
                data = res.json()
                _current_user_id = str(data.get("userId") or "")
            except Exception:
                pass
    except Exception:
        pass


def get_current_user_id() -> str:
    """Return the userId from the most recent successful login."""
    if not _jar:
        _ensure_login()
    return _current_user_id


def get_auth_headers(_unused_access_token: str = "") -> dict:
    """Build request headers. Cookie is attached via the client jar, not
    headers, so this only returns the Content-Type."""
    return {"Content-Type": "application/json", "Accept": "application/json"}


async def api_get(path: str, access_token: str = "", params: dict = None) -> str:
    if not _jar:
        _ensure_login()
    try:
        async with _build_client() as client:
            res = await client.get(
                BASE_URL + path,
                headers=get_auth_headers(access_token),
                params=params or {},
            )
        _save_cookies(client)
        if res.status_code == 200:
            return res.text
        return json.dumps({"error": "API Error: " + str(res.status_code), "detail": res.text})
    except httpx.TimeoutException:
        return json.dumps({"error": "Timeout", "detail": "Request timed out"})
    except Exception as e:
        return json.dumps({"error": str(e)})


async def api_post(path: str, access_token: str = "", json_data: dict = None) -> str:
    if not _jar:
        _ensure_login()
    try:
        async with _build_client() as client:
            res = await client.post(
                BASE_URL + path,
                headers=get_auth_headers(access_token),
                json=json_data or {},
            )
        _save_cookies(client)
        if res.status_code in (200, 201):
            return res.text
        return json.dumps({"error": "API Error: " + str(res.status_code), "detail": res.text})
    except httpx.TimeoutException:
        return json.dumps({"error": "Timeout", "detail": "Request timed out"})
    except Exception as e:
        return json.dumps({"error": str(e)})
