"""
DeepAgent Streamlit UI — local dev/test interface.
Connects to the FastAPI backend via POST /api/v1/chat.

Run:
    cd web_dev_test
    pip install -r requirements.txt
    streamlit run streamlit_app.py

Prerequisites:
    1. Start the API server: uvicorn src.main:app --reload (from project root)
    2. Open http://localhost:8501
    3. Paste your JWT token in the sidebar and start chatting.
"""

import time
import re

import streamlit as st
import requests

# ── Config ────────────────────────────────────────────────────────────────────

API_BASE = "http://localhost:8000"
CHAT_ENDPOINT = f"{API_BASE}/api/v1/chat"
REQUEST_TIMEOUT = 120

# ── Page setup ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="DeepAgent — Dev Test",
    page_icon="🤖",
    layout="wide",
)
st.title("🤖 DeepAgent — Dev Test UI")

# ── Sidebar: token & session config ───────────────────────────────────────────

with st.sidebar:
    st.header("⚙️ Configuration")

    token = st.text_input(
        "JWT Token",
        type="password",
        placeholder="Paste your Bearer token here...",
        help="Get this token from the DeepPro platform or your auth endpoint.",
    )

    st.divider()

    session_id = st.text_input(
        "Session ID",
        value="dev_session",
        help="Unique session identifier for conversation memory.",
    )

    st.divider()
    st.caption("API endpoint: `POST /api/v1/chat`")

    if st.button("🗑️ Clear chat"):
        st.session_state.messages = []
        st.session_state.pop("pending_request", None)
        st.rerun()


# ── Session state ─────────────────────────────────────────────────────────────

if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_request" not in st.session_state:
    st.session_state.pending_request = None


# ── Chat helper ───────────────────────────────────────────────────────────────


def send_message(message: str, bearer_token: str, sid: str) -> str:
    """Call the FastAPI /chat endpoint and return the reply."""
    headers = {"Content-Type": "application/json"}
    clean_token = bearer_token.strip().strip('"').strip("'")
    if clean_token:
        headers["Authorization"] = f"Bearer {clean_token}"

    payload = {"session_id": sid, "message": message}

    try:
        resp = requests.post(
            CHAT_ENDPOINT, json=payload, headers=headers, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        return resp.json().get("reply", "(no reply)")
    except requests.exceptions.Timeout:
        return f"❌ Request timed out after {REQUEST_TIMEOUT}s. The agent may be stuck — try again."
    except requests.exceptions.HTTPError as e:
        try:
            detail = e.response.json().get("detail", str(e))
        except Exception:
            detail = str(e)
        if e.response.status_code == 401:
            return "❌ 401 Unauthorized — token is missing, expired, or invalid. Paste a fresh JWT in the sidebar."
        return f"❌ HTTP {e.response.status_code}: {detail}"
    except Exception as e:
        return f"❌ Unexpected error: {e}"


# ── Render assistant message ──────────────────────────────────────────────────


def render_assistant_message(content: str):
    """Render an assistant message as plain text — strip markdown images only."""
    text_without_images = re.sub(r"!\[([^\]]*)\]\([^)]+\)", "", content).strip()
    if text_without_images:
        st.markdown(text_without_images, unsafe_allow_html=True)


# ── Render chat history ───────────────────────────────────────────────────────

for msg in st.session_state.messages:
    if msg["role"] == "user":
        with st.chat_message("user", avatar="👤"):
            st.markdown(msg["content"])
    else:
        with st.chat_message("assistant", avatar="🤖"):
            render_assistant_message(msg["content"])


# ── Render pending request (if any) ───────────────────────────────────────────

if st.session_state.pending_request:
    pending = st.session_state.pending_request
    with st.chat_message("user", avatar="👤"):
        st.markdown(pending["prompt"])

    with st.chat_message("assistant", avatar="🤖"):
        with st.spinner("Agent is thinking..."):
            reply = send_message(pending["prompt"], pending["token"], pending["session_id"])
        render_assistant_message(reply)

    st.session_state.messages.append({"role": "user", "content": pending["prompt"]})
    st.session_state.messages.append({"role": "assistant", "content": reply})
    st.session_state.pending_request = None
    st.rerun()


# ── Chat input ────────────────────────────────────────────────────────────────

if prompt := st.chat_input("Ask DeepAgent something..."):
    if not token.strip():
        st.warning("⚠️ Please enter your JWT token in the sidebar first.")
    else:
        # Defer the request to the next rerun so the spinner renders first
        st.session_state.pending_request = {
            "id": int(time.time() * 1000),
            "prompt": prompt,
            "token": token,
            "session_id": session_id,
        }
        st.rerun()
