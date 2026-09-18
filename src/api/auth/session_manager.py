"""
In-memory session manager.
Stores: session_token → { access_token, user_id, email, created_at }
"""

import uuid
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Session:
    access_token: str          # Real JWT from DeepSalesOps /auth/login/native
    user_id: str
    email: str
    role: str
    created_at: float = field(default_factory=time.time)


# In-memory store: session_token → Session
_sessions: dict[str, Session] = {}


def create_session(access_token: str, user_id: str, email: str, role: str) -> str:
    """Create a new session and return its session token."""
    session_token = str(uuid.uuid4())
    _sessions[session_token] = Session(
        access_token=access_token,
        user_id=user_id,
        email=email,
        role=role,
    )
    return session_token


def get_session(session_token: str) -> Optional[Session]:
    """Return session or None if not found / expired."""
    return _sessions.get(session_token)


def delete_session(session_token: str) -> bool:
    """Remove session. Returns True if existed."""
    if session_token in _sessions:
        del _sessions[session_token]
        return True
    return False


def cleanup_expired(ttl_seconds: float = 3600 * 24) -> int:
    """Remove sessions older than ttl_seconds. Returns count removed."""
    now = time.time()
    expired = [tok for tok, s in _sessions.items() if now - s.created_at > ttl_seconds]
    for tok in expired:
        del _sessions[tok]
    return len(expired)
