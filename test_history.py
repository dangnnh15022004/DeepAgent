#!/usr/bin/env python3
"""Test chat history persistence — runs against localhost:8000 (uvicorn)."""

import httpx
import sys

BASE = "http://localhost:8001"

# ── 1. Login ─────────────────────────────────────────────────────────────────
print("=== 1. LOGIN ===")
login_resp = httpx.post(
    f"{BASE}/api/v1/auth/login",
    json={"email": "sale.demo@deeptrace.com", "password": "testPassword@2003"},
    timeout=60,
)
if login_resp.status_code != 200:
    print(f"  FAIL login: {login_resp.status_code} {login_resp.text}")
    sys.exit(1)

token = login_resp.json()["session_token"]
print(f"  OK  session_token={token[:20]}...")

session_id = f"test-{__import__('time').time():.0f}"

# ── 2. Turn 1 ────────────────────────────────────────────────────────────────
print("\n=== 2. TURN 1: 'tôi muốn hỏi spham jasmine' ===")
r1 = httpx.post(
    f"{BASE}/api/v1/chat",
    headers={"Authorization": token, "Content-Type": "application/json"},
    json={"session_id": session_id, "message": "tôi muốn hỏi spham jasmine"},
    timeout=120,
)
if r1.status_code != 200:
    print(f"  FAIL: {r1.status_code} {r1.text}")
    sys.exit(1)

d1 = r1.json()
print(f"  status={d1.get('status')}")
reply1 = d1.get("reply", "")[:200]
print(f"  reply={reply1}")

# ── 3. Turn 2 — follow-up (planner should use history context) ─────────────
print("\n=== 3. TURN 2: 'sản phẩm vừa nói là gì' ===")
r2 = httpx.post(
    f"{BASE}/api/v1/chat",
    headers={"Authorization": token, "Content-Type": "application/json"},
    json={"session_id": session_id, "message": "sản phẩm vừa nói là gì"},
    timeout=120,
)
if r2.status_code != 200:
    print(f"  FAIL: {r2.status_code} {r2.text}")
    sys.exit(1)

d2 = r2.json()
print(f"  status={d2.get('status')}")
reply2 = d2.get("reply", "")[:300]
print(f"  reply={reply2}")

# ── 4. Check DB ─────────────────────────────────────────────────────────────
print("\n=== 4. CHECK DB chat_messages ===")
import subprocess

# psql doesn't bind positional argv to $-placeholders. Easiest: shell-quote
# the session_id into the SQL string. It's a controlled test (no user input),
# so simple interpolation is safe.
result = subprocess.run(
    [
        "docker", "exec", "deepagent-postgres",
        "psql", "-U", "postgres", "-d", "deepagent",
        "-t", "-c",
        f"SELECT id, session_id, left(user_message,50), left(bot_reply,50), created_at "
        f"FROM chat_messages WHERE session_id='{session_id}' ORDER BY id;",
    ],
    capture_output=True, text=True,
)

if result.returncode == 0:
    lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
    print(f"  Found {len(lines)} rows in chat_messages:")
    for line in lines:
        print(f"    {line}")
    if len(lines) >= 2:
        print("\n  ✅ HISTORY PERSISTENCE: PASS")
    else:
        print("\n  ❌ HISTORY PERSISTENCE: FAIL — expected 2 rows, got", len(lines))
else:
    print(f"  DB query failed (is container running?): {result.stderr}")
    print("  Install psql or run: docker exec deepagent-postgres psql -U postgres -d deepagent")
    print("  Query: SELECT * FROM chat_messages WHERE session_id=session_id;")

print("\n=== DONE ===")
