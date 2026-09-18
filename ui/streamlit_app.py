"""
Streamlit chat UI for DeepAgent — bypasses browser JS issues.

Run:
    streamlit run ui/streamlit_app.py --server.port 8501

It calls the FastAPI backend at http://localhost:8001 (configurable below).
Backend stays the source of truth — this UI is just a thin client.
"""

import os
import time
from typing import Any, Dict, List

import httpx
import streamlit as st

API_BASE = os.environ.get("DEEPAGENT_API", "http://localhost:8001")

st.set_page_config(
    page_title="DeepAgent Chat",
    page_icon="🤖",
    layout="centered",
    initial_sidebar_state="expanded",
)


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .stChatMessage { padding: 8px 12px; border-radius: 10px; margin-bottom: 4px; }
        [data-testid="stChatMessage"][data-testid*="user"] { background: #1e293b; }
        .small-muted { color: #64748b; font-size: 0.8rem; }
        .login-card {
            max-width: 420px; margin: 10vh auto; padding: 32px;
            background: #1e293b; border-radius: 12px; color: #e2e8f0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


inject_css()


# ── Session init ────────────────────────────────────────────────────────────
def _init_state() -> None:
    st.session_state.setdefault("session_token", None)
    st.session_state.setdefault("user_email", None)
    st.session_state.setdefault("user_id", None)
    st.session_state.setdefault("role", "Customer")
    st.session_state.setdefault("chat_session", f"sess-{int(time.time())}")
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("pending_action", None)


_init_state()


# ── Auth ────────────────────────────────────────────────────────────────────
def _login(email: str, password: str) -> Dict[str, Any]:
    with httpx.Client(timeout=30.0, verify=False) as c:
        r = c.post(
            f"{API_BASE}/api/v1/auth/login",
            json={"email": email, "password": password},
        )
    if r.status_code != 200:
        raise RuntimeError(r.json().get("detail") or r.text)
    return r.json()


def _me(session_token: str) -> Dict[str, Any]:
    with httpx.Client(timeout=10.0, verify=False) as c:
        r = c.get(
            f"{API_BASE}/api/v1/auth/me",
            headers={"Authorization": f"Session {session_token}"},
        )
    r.raise_for_status()
    return r.json()


def _logout(session_token: str) -> None:
    with httpx.Client(timeout=10.0, verify=False) as c:
        c.post(
            f"{API_BASE}/api/v1/auth/logout",
            headers={"Authorization": f"Session {session_token}"},
        )


# ── Chat ────────────────────────────────────────────────────────────────────
def _send(message: str, is_resume: bool = False, meta: dict | None = None) -> Dict[str, Any]:
    payload: dict[str, Any] = {
        "message": message,
        "session_id": st.session_state["chat_session"],
        "is_resume": is_resume,
    }
    if meta is not None:
        payload["meta"] = meta
    with httpx.Client(timeout=120.0, verify=False) as c:
        r = c.post(
            f"{API_BASE}/api/v1/chat",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Session {st.session_state['session_token']}",
            },
        )
    return r.json()


# ── Render login if not authed ─────────────────────────────────────────────
def _render_login() -> None:
    st.title("🤖 DeepAgent")
    st.caption("Đăng nhập để bắt đầu chat")
    with st.form("login_form", clear_on_submit=False):
        email = st.text_input("Email", value="testcustomer@gmail.com")
        password = st.text_input("Password", value="testPassword@2003", type="password")
        submitted = st.form_submit_button("Đăng nhập", use_container_width=True)
        if submitted:
            try:
                with st.spinner("Đang đăng nhập..."):
                    data = _login(email, password)
                st.session_state["session_token"] = data["session_token"]
                st.session_state["user_email"] = data["email"]
                st.session_state["user_id"] = data["user_id"]
                st.session_state["role"] = data.get("role", "Customer")
                st.success("Đăng nhập thành công")
                st.rerun()
            except Exception as e:
                st.error(f"Đăng nhập thất bại: {e}")


# ── Render chat once authed ────────────────────────────────────────────────
def _render_chat() -> None:
    with st.sidebar:
        st.markdown(f"**👤 {st.session_state['user_email']}**")
        st.caption(f"Role: `{st.session_state['role']}`")
        st.caption(f"Session: `{st.session_state['chat_session']}`")
        if st.button("🚪 Đăng xuất", use_container_width=True):
            try:
                _logout(st.session_state["session_token"])
            except Exception:
                pass
            for k in ("session_token", "user_email", "user_id", "role", "messages", "pending_action"):
                st.session_state[k] = None
            st.session_state["chat_session"] = f"sess-{int(time.time())}"
            st.session_state["messages"] = []
            st.rerun()
        st.divider()
        if st.button("🧹 Cuộc hội thoại mới", use_container_width=True):
            st.session_state["chat_session"] = f"sess-{int(time.time())}"
            st.session_state["messages"] = []
            st.session_state["pending_action"] = None
            st.rerun()

    st.title("🤖 DeepAgent Chat")
    st.caption("Hỏi bất cứ điều gì về sản phẩm, đơn hàng, khách hàng...")

    # history
    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # pending confirm
    pending = st.session_state.get("pending_action")
    if pending:
        msg = pending.get("display_message", "Xác nhận?")
        options = pending.get("options") or []
        pick_type = pending.get("pick_type") or ""
        st.info(f"💬 **{msg}**")

        # ── set_quantity: form to enter quantity + email ────────────────
        if pick_type == "set_quantity":
            with st.form("qty_form", clear_on_submit=False):
                qty = st.number_input("Số lượng", min_value=1, value=1, step=1)
                email = st.text_input("Email (nhận đơn)", value=st.session_state.get("user_email", ""))
                submitted = st.form_submit_button("Tiếp tục", use_container_width=True)
                if submitted:
                    st.session_state["pending_action"] = None
                    st.session_state["messages"].append({"role": "user", "content": f"Số lượng: {qty}, Email: {email}"})
                    with st.chat_message("user"):
                        st.markdown(f"Số lượng: **{qty}**, Email: **{email}**")
                    with st.spinner("Đang xử lý..."):
                        try:
                            resp = _send(
                                f"Số lượng {qty}, email {email}",
                                is_resume=True,
                                meta={"action": "set_quantity", "state": pending.get("state", ""), "quantity": int(qty), "email": email},
                            )
                            _handle_response(resp)
                        except Exception as e:
                            st.error(f"Lỗi: {e}")
                    st.rerun()

        # ── confirm: real confirm only ──────────────────────────────────
        elif pick_type == "confirm":
            if st.button("✅ Xác nhận đặt hàng", type="primary", use_container_width=True):
                st.session_state["pending_action"] = None
                st.session_state["messages"].append({"role": "user", "content": "Xác nhận đặt hàng"})
                with st.chat_message("user"):
                    st.markdown("✅ **Xác nhận đặt hàng**")
                with st.spinner("Đang tạo đơn..."):
                    try:
                        resp = _send("Xác nhận", is_resume=True, meta={"action": "confirm", "state": pending.get("state", "")})
                        _handle_response(resp)
                    except Exception as e:
                        st.error(f"Lỗi: {e}")
                st.rerun()

        # ── select_*: render each option as a clickable button ──────────
        elif options:
            for i, o in enumerate(options, 1):
                label = o.get("label", "Option")
                value = o.get("value") or o.get("description", "")
                desc = o.get("description") or ""
                btn_label = f"{i}. {label}" + (f" — {desc}" if desc and desc != label and desc != f"ID: {value}" else "")
                if st.button(btn_label, key=f"opt_{i}_{pick_type}_{value[:8]}", use_container_width=True):
                    # Build meta based on pick_type so backend knows which
                    # action to call on place_order state machine.
                    meta = {"action": pick_type, "state": pending.get("state", "")}
                    field_map = {
                        "select_product": "product_id",
                        "select_variant": "variant_id",
                        "select_address": "address_id",
                        "select_sale": "sale_id",
                    }
                    field = field_map.get(pick_type)
                    if field:
                        meta[field] = value

                    st.session_state["pending_action"] = None
                    st.session_state["messages"].append({"role": "user", "content": f"Chọn: {label}"})
                    with st.chat_message("user"):
                        st.markdown(f"Chọn: **{label}**")
                    with st.spinner("Đang xử lý..."):
                        try:
                            resp = _send(f"Chọn: {label}", is_resume=True, meta=meta)
                            _handle_response(resp)
                        except Exception as e:
                            st.error(f"Lỗi: {e}")
                    st.rerun()

            if pick_type in ("select_product", "select_variant"):
                st.divider()
                st.caption("🧪 **Test mode:** bỏ qua chọn địa chỉ/sale/qty, mock hết")
                if st.button("🧪 Mock → Tạo order luôn", type="primary", key="mock_btn_sel", use_container_width=True):
                    st.session_state["pending_action"] = None
                    st.session_state["messages"].append({"role": "user", "content": "Mock tạo order"})
                    with st.chat_message("user"):
                        st.markdown("🧪 **Mock → Tạo order**")
                    with st.spinner("Đang tạo đơn (mock)..."):
                        try:
                            resp = _send("Mock tạo order", is_resume=True, meta={"action": "mock_confirm", "state": pending.get("state", "")})
                            _handle_response(resp)
                        except Exception as e:
                            st.error(f"Lỗi: {e}")
                    st.rerun()
        else:
            st.caption("👉 Trả lời bằng cách nhập nội dung vào ô chat bên dưới.")

    # input
    if prompt := st.chat_input("Nhập câu hỏi..."):
        st.session_state["messages"].append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.spinner("Đang suy nghĩ..."):
            try:
                resp = _send(prompt, is_resume=False)
                _handle_response(resp)
            except Exception as e:
                st.error(f"Lỗi: {e}")
        st.rerun()


def _handle_response(resp: Dict[str, Any]) -> None:
    status = resp.get("status")
    if status in ("WAITING_USER_PICK", "WAITING_CONFIRMATION"):
        st.session_state["pending_action"] = resp.get("interrupt_payload", {})
        text = resp.get("reply") or "Cần chọn thêm"
    elif status == "success":
        st.session_state["pending_action"] = None
        text = resp.get("reply") or "(no reply)"
    elif status == "no_pending_action":
        st.session_state["pending_action"] = None
        text = resp.get("reply") or "Không có thao tác đang chờ."
    else:
        st.session_state["pending_action"] = None
        text = resp.get("detail") or str(resp)

    st.session_state["messages"].append({"role": "assistant", "content": text})
    with st.chat_message("assistant"):
        st.markdown(text)


# ── Gate ────────────────────────────────────────────────────────────────────
if not st.session_state.get("session_token"):
    _render_login()
else:
    # Auto-verify token at boot
    try:
        me = _me(st.session_state["session_token"])
        st.session_state["user_email"] = me["email"]
        st.session_state["user_id"] = me["user_id"]
        st.session_state["role"] = me["role"]
    except Exception as e:
        # Token invalid or backend down → force re-login
        st.warning(f"Phiên đăng nhập hết hạn hoặc lỗi xác thực: {e}")
        for k in ("session_token", "user_email", "user_id", "role", "messages", "pending_action"):
            st.session_state[k] = None
        st.session_state["chat_session"] = f"sess-{int(time.time())}"
        st.session_state["messages"] = []
        _render_login()
        st.stop()
    _render_chat()
