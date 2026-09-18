"""
Auth routes — login, logout, me.

Flow:
1. POST /auth/login  →  calls DeepSalesOps /auth/login/native
                       → creates session, returns session_token
2. POST /auth/logout → deletes session
3. GET  /auth/me     → returns current user info
"""

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from src.api.auth.session_manager import create_session, delete_session, get_session
from src.config.settings import settings

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    session_token: str
    user_id: str
    email: str
    role: str


class MeResponse(BaseModel):
    user_id: str
    email: str
    role: str


def _extract_auth(authorization: str) -> str | None:
    if not authorization:
        return None
    if authorization.startswith("Session "):
        return authorization[8:]
    if authorization.startswith("Bearer "):
        return authorization[7:]
    return authorization


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    """
    Login via DeepSalesOps /api/v1/auth/login.
    The backend contract returns a user payload without accessToken; we therefore
    treat the login as successful only if the upstream responds 200, then keep a
    session with the same user identity. The chatbot later uses the session token
    to call protected endpoints, while the JWT itself is not exposed in this flow.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            res = await client.post(
                f"{settings.deepsaleops_base_url}/api/v1/auth/login",
                json={"email": body.email, "password": body.password},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"DeepSalesOps unreachable: {e}")

    if res.status_code != 200:
        try:
            err_body = res.json()
        except Exception:
            err_body = {"message": res.text}
        raise HTTPException(
            status_code=401,
            detail=err_body.get("message", "Login failed"),
        )

    data = res.json()
    # Swagger contract for this backend does not return accessToken; instead it
    # returns userId/email/isActive/message. To keep the app working, we create a
    # session using the user identity and rely on the browser's session token.
    user_id = str(data.get("userId") or data.get("user_id") or "")
    email = str(data.get("email") or "")
    role = str(data.get("role") or data.get("roleId") or "Customer")

    # The app still needs a stable session token for subsequent auth. We generate
    # it locally and store the user identity in the session state.
    session_token = create_session(
        access_token="",
        user_id=user_id,
        email=email,
        role=role,
    )

    return LoginResponse(
        session_token=session_token,
        user_id=user_id,
        email=email,
        role=role,
    )


@router.post("/logout")
async def logout(req: Request, response: Response):
    """Delete the current session."""
    auth_header = req.headers.get("Authorization", "")
    session_token = _extract_auth(auth_header)
    if session_token:
        delete_session(session_token)
    # Always return 200 so frontend doesn't have to handle 401 on logout
    response.status_code = 200
    return {"message": "Logged out"}


@router.get("/me", response_model=MeResponse)
async def me(req: Request):
    """Return current user info from session."""
    auth_header = req.headers.get("Authorization", "")
    session_token = _extract_auth(auth_header)
    if not session_token:
        raise HTTPException(status_code=401, detail="Missing session token")

    session = get_session(session_token)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired or not found")

    return MeResponse(
        user_id=session.user_id,
        email=session.email,
        role=session.role,
    )
