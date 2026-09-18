"""
Async Postgres client for chat history persistence.

Uses asyncpg pool. The pool is initialized in FastAPI lifespan and shared
across all requests. All write/read operations are session-scoped so the
planner can pull recent turns to contextualize the next user message.

Table schema is created idempotently by init.sql (mounted into the
postgres container via docker-entrypoint-initdb.d).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

try:
    import asyncpg  # type: ignore
except ImportError:  # asyncpg is optional; app starts even if DB is offline
    asyncpg = None  # type: ignore

from src.config.settings import settings

logger = logging.getLogger("deepagent.db")

_pool: Optional["asyncpg.Pool"] = None


async def init_pool() -> None:
    """Create connection pool. Safe to call multiple times (idempotent)."""
    global _pool
    if _pool is not None or asyncpg is None:
        return

    dsn = settings.postgres_dsn
    if not dsn:
        logger.warning("[DB] POSTGRES_DSN not set — chat history disabled.")
        return

    try:
        _pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=1,
            max_size=5,
            command_timeout=10,
        )
        logger.info(f"[DB] Postgres pool ready: {dsn.split('@')[-1]}")
    except Exception as e:
        logger.error(f"[DB] Failed to connect to Postgres: {e}")
        _pool = None


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def is_ready() -> bool:
    return _pool is not None


# ── Write ─────────────────────────────────────────────────────────────────────

async def save_chat_message(
    session_id: str,
    user_message: str,
    bot_reply: str,
    user_id: Optional[str] = None,
) -> None:
    """Persist a single turn (user → bot). Best-effort; failures are logged
    but never raised so the chat endpoint always returns."""
    if _pool is None:
        return
    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO chat_messages (session_id, user_id, user_message, bot_reply)
                VALUES ($1, $2, $3, $4)
                """,
                session_id,
                user_id,
                user_message,
                bot_reply,
            )
    except Exception as e:
        logger.warning(f"[DB] save_chat_message failed: {e}")


# ── Read ──────────────────────────────────────────────────────────────────────

async def get_recent_history(
    session_id: str,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Return the N most recent turns for `session_id`, ordered oldest→newest.

    Each entry is {role, content} ready to be prepended to a conversation.
    """
    if _pool is None:
        return []
    n = limit or settings.history_window
    try:
        async with _pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH recent AS (
                    SELECT user_message, bot_reply, created_at
                    FROM chat_messages
                    WHERE session_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2
                )
                SELECT user_message, bot_reply, created_at
                FROM recent
                ORDER BY created_at ASC
                """,
                session_id,
                n,
            )
    except Exception as e:
        logger.warning(f"[DB] get_recent_history failed: {e}")
        return []

    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({"role": "user",      "content": r["user_message"]})
        out.append({"role": "assistant", "content": r["bot_reply"]})
    return out
