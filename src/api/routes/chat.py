"""
DeepAgent Chat endpoint — exact 99g-agent pattern.

Flow:
1. POST /chat (is_resume=false) → graph runs, hits `interrupt()` on write tool
   → returns `WAITING_CONFIRMATION`.
2. POST /chat (is_resume=true) → classify user reply → `Command(resume=...)`.
3. If user sends a NEW task while HIL is pending, reject and start fresh.
"""

import hashlib
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.types import Command

from src.agent.memory.state import ChatRequest, ChatResponse
from src.agent.tools.mcp_manager import mcp_manager
from src.agent.workflows.graph import agentic_graph
from src.api.auth.session_manager import get_session
from src.llm.providers.azure import llm

router = APIRouter(prefix="/api/v1", tags=["Chat"])

# ── LangSmith single-trace tracking ────────────────────────────────────────────
# Maps thread_id → the run_id of the FIRST ainvoke call for that session.
# All subsequent ainvoke calls use this as parent_run_id so LangSmith renders
# the entire conversation as ONE trace tree instead of one trace per HTTP request.
_session_first_run_id: dict[str, str] = {}


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:12]


def _get_graph_config(session_id: str, token: str, user_hash: str) -> dict:
    thread_id = f"{user_hash}_{session_id}"
    print(f"[Chat] Thread: {thread_id}")
    return {
        "configurable": {
            "thread_id": thread_id,
            "access_token": token,
        },
        "metadata": {
            "session_id": thread_id,
            "user_hash": user_hash,
        },
        "tags": ["deepagent", "chat"],
    }


def _extract_token(authorization: str) -> str:
    if authorization.startswith("Session "):
        # Resolve session token → real access token for MCP tools
        session_token = authorization[8:]
        session = get_session(session_token)
        if session:
            return session.access_token
        raise ValueError("Session expired or invalid")
    if authorization.startswith("Bearer "):
        return authorization[7:]
    if authorization.startswith("JWT "):
        return authorization[4:]
    return authorization


# ─── Intent classifier ───────────────────────────────────────────────────────


def classify_user_intent(user_text: str) -> str:
    # If message looks like a UUID/product ID, it's a user selection from options card
    if len(user_text) >= 20 and "-" in user_text:
        return "user_selection"

    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are an intent classifier for confirmation requests.

Decide ONE of three intents:
1. 'confirm'  — user agrees (yes, ok, đồng ý, làm đi, proceed...)
2. 'reject'   — user declines (no, cancel, hủy, không, thôi...)
3. 'new_task' — user is asking something completely different

Return ONLY one word: confirm, reject, or new_task."""),
        ("user", "{text}")
    ])
    try:
        result = (prompt | llm).invoke({"text": user_text})
        decision = result.content.strip().lower()
        if "new_task" in decision:
            return "new_task"
        if "confirm" in decision:
            return "confirm"
        return "reject"
    except Exception:
        reject_keywords = ["no", "cancel", "stop", "nope", "nah", "không", "hủy", "huy", "thôi"]
        return "reject" if any(kw in user_text.lower() for kw in reject_keywords) else "confirm"


# ─── HIL helpers ──────────────────────────────────────────────────────────


SENSITIVE_PARAMS = {"access_token", "token", "password", "secret", "api_key"}


def _build_options_from_result(result: dict | None, tool_name: str) -> list[dict] | None:
    """Extract selectable options from a tool result."""
    if not result or not isinstance(result, dict):
        return None

    options = []

    # place_order state machine: extract options from data field
    if tool_name == "place_order":
        status = result.get("status", "")
        data = result.get("data") or {}
        if isinstance(data, dict):
            items = data.get("products") or data.get("variants") or data.get("addresses") or data.get("sales") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []
        if not items:
            return None
        if status in ("select_product",):
            for item in items[:5]:
                label = item.get("productName", item.get("name", "Sản phẩm"))
                price = item.get("minRetailPrice") or item.get("maxRetailPrice") or 0
                price_str = f" — {float(price):,.0f} VND" if price else ""
                options.append({
                    "label": label + price_str,
                    "value": str(item.get("productId", item.get("id", ""))),
                    "description": item.get("description", ""),
                })
        elif status in ("select_variant",):
            for v in items[:5]:
                name = v.get("name", "Biến thể")
                weight = v.get("netContent", "")
                price = v.get("price", 0)
                price_str = f"{float(price):,.0f} VND" if price else "Liên hệ"
                label = f"{name} ({weight}) — {price_str}" if weight else f"{name} — {price_str}"
                options.append({
                    "label": label,
                    "value": str(v.get("onSaleProductVariantId", v.get("variantId", v.get("id", "")))),
                    "description": v.get("description", ""),
                })
        elif status == "select_address":
            for addr in items[:5]:
                name = addr.get("name", addr.get("receiverName", ""))
                phone = addr.get("phone", "")
                city = addr.get("city", "")
                addr_text = f"{name} — {phone}, {city}"
                options.append({
                    "label": addr_text,
                    "value": str(addr.get("id", "")),
                    "description": addr.get("address", ""),
                })
        elif status == "select_sale":
            for sale in items[:5]:
                name = sale.get("name", sale.get("saleName", ""))
                options.append({
                    "label": name,
                    "value": str(sale.get("id", "")),
                    "description": sale.get("description", ""),
                })
        return options if options else None

    # list_on_sale_products / search_on_sale_products → show products
    if tool_name in ("list_on_sale_products", "search_on_sale_products"):
        items = (result.get("items")
                 or result.get("productItems")
                 or result.get("data")
                 or [])
        if not items:
            return None
        for item in items[:5]:
            label = item.get("productName", item.get("name", "Sản phẩm không tên"))
            price = (item.get("minRetailPrice")
                     or item.get("maxRetailPrice")
                     or item.get("unitPrice")
                     or item.get("price"))
            price_str = f" — {float(price):,.0f} VND" if price else ""
            cat = item.get("category") or item.get("businessCategory") or ""
            net = item.get("netContent") or item.get("weight") or ""
            desc = " | ".join(filter(None, [cat, net])).strip(" |")
            options.append({
                "label": label + price_str,
                "value": str(item.get("productId") or item.get("id") or item.get("onSaleProductId") or item.get("productName", "")),
                "description": desc,
            })
        if len(items) > 5:
            options.append({
                "label": f"Xem thêm {len(items) - 5} sản phẩm...",
                "value": "show_more_products",
                "description": "",
            })
        return options if options else None

    # list_on_sale_product_variants → show variants with price
    if tool_name == "list_on_sale_product_variants" and "variants" in result:
        variants = result["variants"]
        if not variants:
            return None
        for v in variants[:5]:
            name = v.get("name", "Biến thể mặc định")
            weight = v.get("netContent") or v.get("weight") or ""
            price = v.get("price")
            currency = v.get("currencyId") or 1
            price_str = f"{float(price):,.0f} VND" if price else "Liên hệ"
            label = f"{name}"
            if weight:
                label += f" ({weight})"
            label += f" — {price_str}"
            options.append({
                "label": label,
                "value": str(v.get("onSaleProductVariantId") or v.get("variantId") or v.get("id") or ""),
                "description": f"ID: {v.get('onSaleProductVariantId') or v.get('variantId') or v.get('id')}",
            })
        return options if options else None

    # list_user_addresses → show addresses (key might be items/addresses/data)
    if tool_name == "list_user_addresses":
        addrs = (result.get("addresses")
                 or result.get("items")
                 or result.get("data")
                 or result.get("addressList")
                 or [])
        if not addrs:
            return None
        for addr in addrs:
            label = (addr.get("label") or addr.get("fullAddress") or addr.get("addressLine") or "Địa chỉ")
            is_default = " (Mặc định)" if addr.get("isDefault") or addr.get("is_default") else ""
            desc = ", ".join(filter(None, [
                addr.get("fullAddress") or addr.get("addressLine"),
                addr.get("ward") or addr.get("wardName"),
                addr.get("district") or addr.get("districtName"),
                addr.get("city") or addr.get("province") or addr.get("cityName"),
            ]))
            options.append({
                "label": str(label) + is_default,
                "value": str(addr.get("addressId") or addr.get("id") or addr.get("userAddressId") or ""),
                "description": desc,
            })
        return options if options else None

    # create_order → return None (simple yes/no)
    if tool_name == "create_order":
        return None

    return None


def _get_action_description(tool_name: str, params: dict, lang: str = "en") -> str:
    item_count = total_qty = 0
    if isinstance(params.get("on_sale_product_variants"), list):
        item_count = len(params["on_sale_product_variants"])
        total_qty = sum(
            int(v.get("quantity", 0))
            for v in params["on_sale_product_variants"]
            if isinstance(v, dict)
        )

    def fmt(x):
        if x is None:
            return "?"
        try:
            return f"{float(x):,.0f} VND"
        except (TypeError, ValueError):
            return f"{x}"

    if tool_name == "create_order":
        msg = f"tạo đơn hàng gồm {item_count} sản phẩm (tổng {total_qty} cái), tạm tính {fmt(params.get('snapshot_price'))}, ship {fmt(params.get('shipping_fee'))}, giao đến {params.get('district', '')}, {params.get('city', '')}"
        return msg

    return f"thực hiện '{tool_name}'"


def _build_hil_response(tool_name: str | None, params: dict, result: dict | None = None, user_lang: str = "vi") -> dict:
    # If tool returned a structured result with status field, use status-based renderer
    if result and isinstance(result, dict) and result.get("status"):
        return _render_status_result(result, user_lang)

    if tool_name == "place_order" and result and isinstance(result, dict) and result.get("status"):
        return _render_status_result(result, user_lang)

    if tool_name == "create_order":
        message = _format_order_summary_for_confirmation(params, user_lang)
        return {"action": tool_name, "display_message": message, "needs_confirmation": True}

    action = _get_action_description(tool_name, params, user_lang)
    confirm_prompt = ChatPromptTemplate.from_messages([
        ("system", f"""You are a helpful assistant.

Action: {action}

Write ONE short confirmation question in {user_lang} language. Return ONLY the sentence."""),
        ("user", "Generate the confirmation message.")
    ])
    try:
        result = (confirm_prompt | llm).invoke({})
        message = result.content.strip()
    except Exception:
        fallback = {
            "vi": f"Bạn có muốn tiếp tục '{tool_name}' không?",
            "en": f"Do you want to proceed with '{tool_name}'?",
            "zh": f"您想继续 '{tool_name}' 吗？",
        }
        message = fallback.get(user_lang, fallback["vi"])

    return {"action": tool_name, "display_message": message, "needs_confirmation": True}


def _format_order_summary_for_confirmation(params: dict, user_lang: str = "vi") -> str:
    """Render the full create_order payload as a markdown summary the user
    must read and confirm before we POST /v1/order/create."""
    lines = []
    title = "**Xác nhận đơn hàng**" if user_lang == "vi" else "**Order confirmation**"
    lines.append(title)
    lines.append("")

    variants = params.get("on_sale_product_variants") or []
    if isinstance(variants, list) and variants:
        lines.append("**Sản phẩm**" if user_lang == "vi" else "**Items**")
        for v in variants:
            if not isinstance(v, dict):
                continue
            name = v.get("product_name") or v.get("variant_name") or v.get("on_sale_product_variant_id") or ""
            qty = v.get("quantity")
            price = v.get("unit_price") or v.get("snapshot_price")
            line = f"- {name}"
            if qty is not None:
                line += f" × {qty}"
            if price is not None:
                try:
                    line += f" — {float(price):,.0f} VND"
                except (TypeError, ValueError):
                    line += f" — {price}"
            lines.append(line)
        lines.append("")

    def add(label_vi, label_en, key):
        val = params.get(key)
        if val is None or val == "":
            return
        label = label_vi if user_lang == "vi" else label_en
        lines.append(f"- **{label}:** {val}")

    add("Số lượng", "Quantity", "quantity")
    add("Mã kho", "Warehouse ID", "warehouse_id")
    add("Mã địa chỉ", "Address ID", "user_addresses_id")
    add("Thành phố", "City", "city")
    add("Quận/Huyện", "District", "district")
    add("Kinh độ", "Longitude", "longitude")
    add("Vĩ độ", "Latitude", "latitude")
    add("Email", "Email", "email")

    snapshot = params.get("snapshot_price")
    if snapshot is not None:
        try:
            snap_str = f"{float(snapshot):,.0f} VND"
        except (TypeError, ValueError):
            snap_str = str(snapshot)
        lines.append(f"- **{'Đơn giá' if user_lang == 'vi' else 'Unit price'}:** {snap_str}")

    shipping = params.get("shipping_fee")
    if shipping is not None:
        try:
            ship_str = f"{float(shipping):,.0f} VND"
        except (TypeError, ValueError):
            ship_str = str(shipping)
        lines.append(f"- **{'Phí vận chuyển' if user_lang == 'vi' else 'Shipping fee'}:** {ship_str}")

    add("Mã giảm giá", "Discount code", "default_discount_code")
    add("Mã chiến dịch", "Campaign code", "campaign_discount_code")

    lines.append("")
    prompt = "Vui lòng xem lại và xác nhận để tạo đơn." if user_lang == "vi" else "Please review and confirm to place the order."
    lines.append(prompt)
    return "\n".join(lines)


def _render_status_result(result: dict, user_lang: str = "vi") -> dict:
    """Parse place_order result by status field and build HIL response for frontend."""
    # Defensive: if result is a list (from corrupt upstream), treat as error
    if not isinstance(result, dict):
        return {
            "action": "error",
            "display_message": f"Lỗi: phản hồi không hợp lệ từ server ({type(result).__name__}).",
            "needs_confirmation": False,
            "is_terminal": False,
        }
    status = result.get("status", "")
    message = result.get("message", "")
    data = result.get("data", {}) or {}
    state = result.get("state", "")  # opaque state string for next call
    order = result.get("order", {})
    lang_vi = user_lang == "vi"

    def _with_state(hil: dict) -> dict:
        """Attach state so frontend can pass it back on next pick."""
        hil["state"] = state
        return hil

    # ── created ────────────────────────────────────────────────────────────
    if status == "created":
        code = order.get("orderCode") or order.get("code") or ""
        order_id = order.get("orderId") or order.get("id") or ""
        total = order.get("totalPrice") or order.get("totalAmount") or 0
        try:
            total_str = f"{float(total):,.0f} VND"
        except (TypeError, ValueError):
            total_str = str(total)
        addr_raw = order.get("address") or order.get("shippingAddress") or {}
        addr_parts = [
            addr_raw.get("fullAddress") or addr_raw.get("addressLine") or "",
            addr_raw.get("ward") or addr_raw.get("wardName") or "",
            addr_raw.get("district") or addr_raw.get("districtName") or "",
            addr_raw.get("city") or addr_raw.get("cityName") or "",
        ]
        addr_str = ", ".join(filter(None, addr_parts))

        reply = f"**Đặt hàng thành công!**\n\n"
        reply += f"- **Mã đơn:** {code}\n"
        reply += f"- **Tổng tiền:** {total_str}\n"
        if addr_str:
            reply += f"- **Giao đến:** {addr_str}\n"
        if order_id:
            reply += f"- **Order ID:** {order_id}"
        return {
            "action": "created",
            "display_message": reply,
            "needs_confirmation": False,
            "is_terminal": True,
            "status": "success",
        }

    # ── error ───────────────────────────────────────────────────────────────
    if status == "error":
        return {
            "action": "error",
            "display_message": f"**Lỗi:** {message}",
            "needs_confirmation": False,
            "is_terminal": False,
        }

    # ── select_product ──────────────────────────────────────────────────────
    if status == "select_product":
        products = data.get("products", [])
        options = []
        for p in products[:10]:
            name = p.get("productName") or p.get("name") or "Sản phẩm"
            price = p.get("minRetailPrice") or p.get("price") or ""
            price_str = f" — {float(price):,.0f} VND" if price else ""
            pid = p.get("productId") or p.get("id") or p.get("onSaleProductId") or ""
            options.append({
                "label": name + price_str,
                "value": pid,
                "description": f"ID: {pid}",
            })
        return _with_state({
            "action": "select_product",
            "display_message": message or ("Vui lòng chọn sản phẩm:" if lang_vi else "Please select a product:"),
            "needs_confirmation": False,
            "pick_type": "select_product",
            "options": options if options else None,
        })

    # ── select_variant ─────────────────────────────────────────────────────
    if status == "select_variant":
        variants = data.get("variants", [])
        options = []
        for v in variants[:10]:
            name = v.get("name", "Biến thể")
            weight = v.get("netContent") or v.get("weight") or ""
            price = v.get("price")
            price_str = f" — {float(price):,.0f} VND" if price else " — Liên hệ"
            label = f"{name}"
            if weight:
                label += f" ({weight})"
            label += price_str
            vid = v.get("onSaleProductVariantId") or v.get("variantId") or v.get("id") or ""
            options.append({
                "label": label,
                "value": vid,
                "description": f"ID: {vid}",
            })
        return _with_state({
            "action": "select_variant",
            "display_message": message or ("Vui lòng chọn biến thể:" if lang_vi else "Please select a variant:"),
            "needs_confirmation": False,
            "pick_type": "select_variant",
            "options": options if options else None,
        })

    # ── select_address ──────────────────────────────────────────────────────
    if status == "select_address":
        addresses = data.get("addresses", [])
        print(f"[Chat] select_address: data_keys={list(data.keys())}, addr_count={len(addresses)}")
        if addresses:
            print(f"[Chat] first addr: {addresses[0]}")
        variant = data.get("variant", {})
        options = []
        for a in addresses:
            label = a.get("label") or a.get("address") or a.get("fullAddress") or a.get("addressLine") or "Địa chỉ"
            recipent = a.get("recipentName") or a.get("name") or ""
            if recipent and recipent != label:
                label = f"{recipent} — {label}" if label != "Địa chỉ" else recipent
            is_default = " (Mặc định)" if a.get("isDefault") or a.get("is_default") else ""
            desc = ", ".join(filter(None, [
                a.get("address") or a.get("fullAddress") or a.get("addressLine"),
                a.get("ward") or a.get("wardName"),
                a.get("district") or a.get("districtName"),
                a.get("city") or a.get("province") or a.get("cityName"),
            ]))
            aid = (a.get("addressId") or a.get("id") or a.get("userAddressId")
                   or a.get("userAddressesId") or a.get("user_addresses_id") or "")
            options.append({
                "label": str(label) + is_default,
                "value": aid,
                "description": desc or f"ID: {aid}",
            })
        return _with_state({
            "action": "select_address",
            "display_message": message or ("Vui lòng chọn địa chỉ giao hàng:" if lang_vi else "Please select a shipping address:"),
            "needs_confirmation": False,
            "pick_type": "select_address",
            "options": options if options else None,
        })

    # ── select_sale ────────────────────────────────────────────────────────
    if status == "select_sale":
        sales = data.get("sales") or data.get("items") or []
        options = []
        for s in sales:
            if not isinstance(s, dict):
                continue
            # Sale schema: { userId, email, phoneNumber, avatarUrl } — no name field!
            email = s.get("email") or ""
            phone = s.get("phone") or s.get("phoneNumber") or s.get("mobile") or ""
            # Derive display name: email local-part if no real name field
            name = (s.get("fullName") or s.get("name") or s.get("saleName")
                    or s.get("userName") or s.get("displayName")
                    or (email.split("@")[0] if email else "")
                    or "Sale")
            role = s.get("role") or s.get("position") or ""
            desc_parts = [p for p in [role, phone, email] if p]
            # IMPORTANT: userId IS the unique identifier (no separate "id"/"saleId")
            sid = (s.get("userId") or s.get("id") or s.get("saleId")
                   or s.get("salesRepId") or s.get("user_id") or "")
            options.append({
                "label": name,
                "value": sid,
                "description": " — ".join(desc_parts) or f"ID: {sid}",
            })
        return _with_state({
            "action": "select_sale",
            "display_message": message or ("Vui lòng chọn sale phụ trách:" if lang_vi else "Please select a sales rep:"),
            "needs_confirmation": False,
            "pick_type": "select_sale",
            "options": options if options else None,
        })

    # ── set_quantity ───────────────────────────────────────────────────────
    if status == "set_quantity":
        variant = data.get("variant", {})
        address = data.get("address", {})
        vname = variant.get("name", "")
        vweight = variant.get("netContent", "")
        vprice = variant.get("price", 0)
        try:
            price_str = f"{float(vprice):,.0f} VND"
        except (TypeError, ValueError):
            price_str = "Liên hệ"

        # Build address summary for display
        addr_parts = [
            address.get("fullAddress") or address.get("addressLine") or "",
            address.get("ward") or address.get("wardName") or "",
            address.get("district") or address.get("districtName") or "",
            address.get("city") or address.get("province") or address.get("cityName") or "",
        ]
        addr_str = ", ".join(filter(None, addr_parts))

        summary = f"**Sản phẩm:** {vname}"
        if vweight:
            summary += f" ({vweight})"
        summary += f" — {price_str}\n"
        if addr_str:
            summary += f"**Giao đến:** {addr_str}\n"
        summary += "\n**Nhập số lượng và email để tiếp tục:**"

        return _with_state({
            "action": "set_quantity",
            "display_message": summary,
            "needs_confirmation": False,
            "pick_type": "set_quantity",
            "data": {
                "variant_name": vname,
                "unit_price": vprice,
                "product_name": data.get("product_name", ""),
            },
        })

    # ── confirm ───────────────────────────────────────────────────────────
    if status == "confirm":
        variant = data.get("variant", {})
        address = data.get("address", {})
        qty = data.get("quantity", 0)
        price = data.get("unit_price", 0) or variant.get("price", 0)
        subtotal = data.get("subtotal", price * qty)
        warehouse_avail = data.get("warehouse_available", 0)

        vname = variant.get("name", "Sản phẩm")
        vweight = variant.get("netContent", "")
        vpkg = variant.get("packagingType", "")
        try:
            price_str = f"{float(price):,.0f} VND"
            subtotal_str = f"{float(subtotal):,.0f} VND"
        except (TypeError, ValueError):
            price_str = str(price)
            subtotal_str = str(subtotal)

        addr_parts = [
            address.get("fullAddress") or address.get("addressLine") or "",
            address.get("ward") or address.get("wardName") or "",
            address.get("district") or address.get("districtName") or "",
            address.get("city") or address.get("province") or address.get("cityName") or "",
        ]
        addr_str = ", ".join(filter(None, addr_parts))
        email = data.get("email", "")
        warehouse_id = data.get("warehouse_id", "")

        reply = "**Xác nhận đơn hàng**\n\n"
        reply += f"**Sản phẩm:** {vname}"
        if vweight:
            reply += f" ({vweight})"
        reply += f"\n**Số lượng:** {qty}\n"
        reply += f"**Đơn giá:** {price_str}\n"
        reply += f"**Tạm tính:** {subtotal_str}\n"
        if addr_str:
            reply += f"**Giao đến:** {addr_str}\n"
        if email:
            reply += f"**Email:** {email}\n"
        if warehouse_id:
            reply += f"**Kho (còn {warehouse_avail}):** {warehouse_id}\n"
        reply += "\nVui lòng xem lại và xác nhận để tạo đơn."

        return _with_state({
            "action": "confirm",
            "display_message": reply,
            "needs_confirmation": True,
            "pick_type": "confirm",
        })

    # ── unknown status ─────────────────────────────────────────────────────
    return {
        "action": "error",
        "display_message": f"Lỗi không xác định: status={status}",
        "needs_confirmation": False,
        "is_terminal": False,
    }


def _format_order_summary_for_confirmation(params: dict, user_lang: str = "vi") -> str:
    title = "**Xác nhận đơn hàng**" if user_lang == "vi" else "**Order confirmation**"
    lines.append(title)
    lines.append("")

    variants = params.get("on_sale_product_variants") or []
    if isinstance(variants, list) and variants:
        lines.append("**Sản phẩm**" if user_lang == "vi" else "**Items**")
        for v in variants:
            if not isinstance(v, dict):
                continue
            name = v.get("product_name") or v.get("variant_name") or v.get("on_sale_product_variant_id") or ""
            qty = v.get("quantity")
            price = v.get("unit_price") or v.get("snapshot_price")
            line = f"- {name}"
            if qty is not None:
                line += f" × {qty}"
            if price is not None:
                try:
                    line += f" — {float(price):,.0f} VND"
                except (TypeError, ValueError):
                    line += f" — {price}"
            lines.append(line)
        lines.append("")

    def add(label_vi, label_en, key):
        val = params.get(key)
        if val is None or val == "":
            return
        label = label_vi if user_lang == "vi" else label_en
        lines.append(f"- **{label}:** {val}")

    add("Số lượng", "Quantity", "quantity")
    add("Mã kho", "Warehouse ID", "warehouse_id")
    add("Mã địa chỉ", "Address ID", "user_addresses_id")
    add("Thành phố", "City", "city")
    add("Quận/Huyện", "District", "district")
    add("Kinh độ", "Longitude", "longitude")
    add("Vĩ độ", "Latitude", "latitude")
    add("Email", "Email", "email")

    snapshot = params.get("snapshot_price")
    if snapshot is not None:
        try:
            snap_str = f"{float(snapshot):,.0f} VND"
        except (TypeError, ValueError):
            snap_str = str(snapshot)
        lines.append(f"- **{'Đơn giá' if user_lang == 'vi' else 'Unit price'}:** {snap_str}")

    shipping = params.get("shipping_fee")
    if shipping is not None:
        try:
            ship_str = f"{float(shipping):,.0f} VND"
        except (TypeError, ValueError):
            ship_str = str(shipping)
        lines.append(f"- **{'Phí vận chuyển' if user_lang == 'vi' else 'Shipping fee'}:** {ship_str}")

    add("Mã giảm giá", "Discount code", "default_discount_code")
    add("Mã chiến dịch", "Campaign code", "campaign_discount_code")

    lines.append("")
    prompt = "Vui lòng xem lại và xác nhận để tạo đơn." if user_lang == "vi" else "Please review and confirm to place the order."
    lines.append(prompt)
    return "\n".join(lines)


def _state_values(state) -> dict:
    if hasattr(state, "values") and isinstance(state.values, dict):
        return state.values
    if isinstance(state, dict):
        return state
    return {}


def _last_tool_call(state) -> dict | None:
    messages = _state_values(state).get("messages", [])
    for msg in reversed(messages):
        if getattr(msg, "tool_calls", None):
            return msg.tool_calls[-1]
    return None


def _drop_orphan_tool_calls(messages) -> list:
    """Drop ai messages with tool_calls whose tool_call_ids have no matching
    tool result in the conversation. Mirrors `_sanitize_messages` in
    workers.py but applied to the persistent state.

    This fixes OpenAI 400 errors when a previous flow ended abruptly
    (e.g. place_order → HTTP 403) leaving an `ai` tool_call with no
    following `tool` response.
    """
    responded_tool_ids: set[str] = set()
    for m in messages:
        if getattr(m, "type", None) == "tool":
            tcid = getattr(m, "tool_call_id", None)
            if tcid:
                responded_tool_ids.add(tcid)

    cleaned = []
    dropped = 0
    for m in messages:
        if getattr(m, "type", None) == "ai" and getattr(m, "tool_calls", None):
            orphan = any(
                (tc.get("id") and tc["id"] not in responded_tool_ids)
                for tc in (m.tool_calls or [])
            )
            if orphan:
                dropped += 1
                continue
        cleaned.append(m)
    if dropped:
        print(f"[Chat] _drop_orphan_tool_calls: removed {dropped} orphan ai message(s).")
    return cleaned


def _last_tool_result(state) -> dict | None:
    """Get the last ToolMessage result."""
    messages = _state_values(state).get("messages", [])
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "tool":
            try:
                content = msg.content
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and "text" in item:
                            import json as _json
                            return _json.loads(item["text"])
                elif isinstance(content, str):
                    import json as _json
                    return _json.loads(content)
            except Exception:
                pass
    return None


def _success_response(session_id: str, state) -> ChatResponse:
    values = _state_values(state)
    messages = values.get("messages", [])
    reply = messages[-1].content if messages else "Done."
    return ChatResponse(
        status="success",
        session_id=session_id,
        reply=reply,
        extracted_data=values.get("extracted_data", {}),
    )


async def _persist_turn(
    session_id: str,
    user_message: str,
    bot_reply: str,
    user_id: Optional[str] = None,
) -> None:
    """Best-effort: save one (user, bot) turn to Postgres chat_messages.

    The DB helper swallows its own errors (logs a warning), so this never
    affects the response — chat continues to work even if Postgres is down."""
    try:
        from src.api.db import postgres as db
        await db.save_chat_message(session_id, user_message, bot_reply, user_id)
    except Exception as e:
        print(f"[Chat] WARN: persist_turn failed: {e}")


async def _load_history_context(session_id: str) -> list:
    """Load the last N turns for this session and convert to alternating
    HumanMessage/AIMessage so the planner can disambiguate follow-ups
    like "thông tin sản phẩm vừa mua"."""

    from src.api.db import postgres as db
    history = await db.get_recent_history(session_id)
    if not history:
        return []

    from langchain_core.messages import HumanMessage, AIMessage
    msgs = []
    for turn in history:
        if turn["role"] == "user":
            msgs.append(HumanMessage(content=turn["content"]))
        else:
            msgs.append(AIMessage(content=turn["content"]))
    return msgs


def _waiting_response(session_id: str, state) -> JSONResponse:
    tool_call = _last_tool_call(state)
    tool_result = _last_tool_result(state)
    user_lang = _state_values(state).get("user_lang", "vi") or "vi"

    print(f"[Chat] _waiting_response: tool_call={tool_call.get('name') if tool_call else None}, "
          f"has_tool_result={bool(tool_result)}, "
          f"tool_result_status={tool_result.get('status') if tool_result and isinstance(tool_result, dict) else None}")

    # Detect "user pick" pending state (read tool finished, waiting for user selection)
    pick_flag = _state_values(state).get("awaiting_user_pick")
    print(f"[Chat] pick_flag={pick_flag}")

    # Find pick_target tool: scan messages backward for the relevant read tool
    pick_tool_name = None
    if pick_flag:
        pick_map = {
            "pick_product": "search_on_sale_products",
            "pick_variant": "list_on_sale_product_variants",
            "pick_address": "list_user_addresses",
            "pick_sale": "list_sales_for_product",
        }
        pick_tool_name = pick_map.get(pick_flag)
        # place_order uses place_order tool name for meta path
        if pick_flag in ("pick_product", "pick_variant", "pick_address", "pick_sale"):
            # Check if the last tool call was place_order (deep-sale flow)
            if tool_call and tool_call.get("name") == "place_order":
                pick_tool_name = "place_order"

    if tool_call:
        tool_name = tool_call.get("name", "unknown")
        safe_args = {k: v for k, v in tool_call.get("args", {}).items()
                     if k.lower() not in SENSITIVE_PARAMS}
        # For pick states, generate a friendly ask, not just HIL confirm
        if pick_flag:
            msg_map = {
                "vi": {
                    "pick_product": "Dạ, có nhiều sản phẩm phù hợp. Quý khách vui lòng chọn 1 sản phẩm:",
                    "pick_variant": "Dạ, sản phẩm có nhiều biến thể. Quý khách vui lòng chọn 1 biến thể:",
                    "pick_address": "Dạ, quý khách vui lòng chọn địa chỉ giao hàng:",
                    "pick_sale": "Dạ, có nhiều nhân viên kinh doanh. Quý khách vui lòng chọn 1 người phụ trách:",
                },
                "en": {
                    "pick_product": "Here are some products. Please pick one:",
                    "pick_variant": "This product has multiple variants. Please pick one:",
                    "pick_address": "Please pick a shipping address:",
                    "pick_sale": "Multiple sale reps available. Please pick one:",
                }
            }
            display = msg_map.get(user_lang, msg_map["vi"]).get(pick_flag, hil_default_msg())
            hil = {"action": pick_tool_name, "display_message": display, "needs_confirmation": False, "pick_type": pick_flag}
            options = _build_options_from_result(tool_result, pick_tool_name)
            print(f"[Chat] DEBUG options for {pick_flag}: {options[:2] if options else None}")
            if options:
                hil["options"] = options
        else:
            hil = _build_hil_response(tool_name, safe_args, tool_result, user_lang)
            # Attach options from tool result for frontend to display
            options = _build_options_from_result(tool_result, tool_name)
            if options:
                hil["options"] = options
    else:
        hil = _build_hil_response(None, {}, user_lang)

    status = "WAITING_USER_PICK" if pick_flag else "WAITING_CONFIRMATION"
    return JSONResponse({
        "status": status,
        "session_id": session_id,
        "reply": hil["display_message"],
        "interrupt_payload": hil,
    })


def hil_default_msg() -> str:
    return "Bạn có muốn tiếp tục không?"


async def _resume_graph(config, message: str, label: str, tool_result: dict | None = None) -> str:
    decision = classify_user_intent(message)
    print(f"[Chat] {label}: user_message='{message}' -> decision='{decision}'")
    if decision == "new_task":
        return decision
    if decision == "user_selection":
        # Pass selected UUID to interrupt via dict payload
        await agentic_graph.ainvoke(Command(resume={"selected_value": message}), config)
        return "confirm"
    await agentic_graph.ainvoke(Command(resume=decision), config)
    return decision


def _extract_state_from_tool_call_args(tool_call: dict) -> str:
    """Extract `state` argument from the last place_order tool call."""
    if not tool_call:
        return ""
    args = tool_call.get("args", {}) or {}
    return args.get("state", "")


async def _run_meta_path(config, meta: dict, token: str, session_id: str):
    """Call place_order directly (bypass LLM) and render HIL response.

    Used by both the explicit `request.meta` path and the UUID HIL path
    when the user picks an option from a dropdown.
    """
    action = meta.get("action", "")
    state = meta.get("state", "")
    keyword = meta.get("keyword", "")
    product_id = meta.get("product_id", "")
    variant_id = meta.get("variant_id", "")
    address_id = meta.get("address_id", "")
    sale_id = meta.get("sale_id", "")
    quantity = meta.get("quantity", 0)
    email = meta.get("email", "")
    print(f"[Chat] META path: action={action}, state_len={len(state)}, product_id={product_id}, variant_id={variant_id}, address_id={address_id}, sale_id={sale_id}, quantity={quantity}, email={email!r}")

    result_str = await mcp_manager.call_tool(
        "deepsaleops",
        "place_order",
        {
            "action": action,
            "access_token": token,
            "state": state,
            "keyword": keyword,
            "product_id": product_id,
            "variant_id": variant_id,
            "address_id": address_id,
            "sale_id": sale_id,
            "quantity": quantity,
            "email": email,
        },
    )
    print(f"[Chat] META raw result_str type={type(result_str).__name__} len={len(result_str) if isinstance(result_str, str) else 'N/A'}")
    print(f"[Chat] META raw result_str preview={result_str[:200] if isinstance(result_str, str) else str(result_str)[:200]}")

    import json as _json
    try:
        result = _json.loads(result_str) if isinstance(result_str, str) else result_str
    except Exception:
        result = {"status": "error", "message": f"Invalid response from place_order: {result_str}"}

    print(f"[Chat] META parsed result type={type(result).__name__} status={result.get('status') if isinstance(result, dict) else 'N/A'}")

    current_state = await agentic_graph.aget_state(config)
    user_lang = _state_values(current_state).get("user_lang", "vi") or "vi"
    hil = _render_status_result(result, user_lang)

    is_terminal = hil.get("is_terminal", False)
    status = "success" if is_terminal else "WAITING_USER_PICK"
    return JSONResponse({
        "status": status,
        "session_id": session_id,
        "reply": hil["display_message"],
        "interrupt_payload": hil,
    })


async def _run_meta_resume(config, meta: dict, token: str, session_id: str):
    """Call place_order directly (bypass LLM) and render HIL response.

    Used when the user picks an option from a HIL card or uses the Mock
    shortcut. Bypasses the LLM entirely — faster and deterministic. The
    state machine advances one step per click until `mock_confirm` creates
    the order.
    """
    action = meta.get("action", "")
    state = meta.get("state", "")
    keyword = meta.get("keyword", "")
    product_id = meta.get("product_id", "")
    variant_id = meta.get("variant_id", "")
    address_id = meta.get("address_id", "")
    sale_id = meta.get("sale_id", "")
    quantity = int(meta.get("quantity", 0) or 0)
    email = meta.get("email", "")

    print(f"[Chat] META direct: action={action}, state_len={len(state)}, product_id={product_id}, variant_id={variant_id}")

    # Call place_order MCP tool directly
    try:
        result_str = await mcp_manager.call_tool(
            "deepsaleops",
            "place_order",
            {
                "access_token": token,
                "action": action,
                "state": state,
                "keyword": keyword,
                "product_id": product_id,
                "variant_id": variant_id,
                "address_id": address_id,
                "sale_id": sale_id,
                "quantity": quantity,
                "email": email,
            },
        )
    except Exception as e:
        return JSONResponse({
            "status": "error",
            "session_id": session_id,
            "reply": f"Lỗi gọi tool: {e}",
        })

    import json as _json
    try:
        result = _json.loads(result_str) if isinstance(result_str, str) else result_str
    except Exception:
        result = {"status": "error", "message": f"Invalid response: {result_str}"}

    print(f"[Chat] META direct result: status={result.get('status')}")

    # Render HIL payload
    status = result.get("status", "")
    if status in ("success", "created", "error"):
        msg = result.get("message", "")
        return JSONResponse({
            "status": "success" if status in ("success", "created") else "error",
            "session_id": session_id,
            "reply": msg,
        })

    # For select_*/set_*/confirm: render HIL options
    from src.api.routes.chat import _render_status_result  # type: ignore
    hil = _render_status_result(result, "vi")
    is_terminal = hil.get("is_terminal", False)
    response_status = "success" if is_terminal else "WAITING_USER_PICK"
    return JSONResponse({
        "status": response_status,
        "session_id": session_id,
        "reply": hil.get("display_message", ""),
        "interrupt_payload": hil,
    })


# ─── Endpoint ───────────────────────────────────────────────────────────────


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, req: Request):
    try:
        try:
            token = _extract_token(req.headers.get("Authorization", ""))
        except ValueError as e:
            raise HTTPException(status_code=401, detail=str(e))

        session_id = request.session_id
        user_hash = _hash_token(token)

        print(f"[Chat] User hash: {user_hash}, Session: {session_id}")
        print(f"[Chat] Request: message={request.message!r}, is_resume={request.is_resume}, meta={request.meta}")

        config = _get_graph_config(session_id, token, user_hash)

        # ── Meta path: user picked an option from a HIL card. Call
        # place_order directly (bypass LLM). Keeps flow fast and
        # deterministic; the state machine advances one step at a time.
        if request.meta:
            return await _run_meta_resume(config, request.meta, token, session_id)

        # ── Normal flow: planner → DeepsaleopsAgent → LLM chooses tools
        current_state = await agentic_graph.aget_state(config)
        is_interrupted = bool(current_state.next)
        print(f"[Chat] is_interrupted={is_interrupted}, next={current_state.next}")

        if request.is_resume and not is_interrupted:
            return JSONResponse({
                "status": "no_pending_action",
                "session_id": session_id,
                "reply": "No pending action to confirm.",
            })

        # ── LangSmith single-trace strategy ────────────────────────────────
        # Goal: the ENTIRE conversation session (all turns + interrupts) appears
        # as ONE trace in LangSmith instead of one trace per HTTP request.
        # Strategy: first ainvoke call → capture run_id; subsequent calls reuse it
        # as parent_run_id so LangSmith links all child runs into one trace tree.

        thread_id = config["configurable"]["thread_id"]
        is_first_turn = thread_id not in _session_first_run_id
        first_run_id = _session_first_run_id.get(thread_id)

        if is_interrupted:
            current_tool_result = _last_tool_result(current_state)
            label = "Resume" if request.is_resume else "Auto-resume"
            decision = await _resume_graph(config, request.message, label, current_tool_result)

            if decision == "new_task":
                try:
                    await agentic_graph.ainvoke(Command(resume="reject"), config)
                except Exception:
                    pass
                # Cancel pending flow: drop messages from the prior flow that
                # left orphan tool_calls (e.g. place_order 403'd without a
                # proper tool response). Without this cleanup, the next
                # ainvoke fails with OpenAI 400 "tool_call_ids did not have
                # response messages".
                cleaned_state_values = dict(current_state.values or {})
                cleaned_msgs = _drop_orphan_tool_calls(
                    cleaned_state_values.get("messages", []) or []
                )
                cleaned_state_values["messages"] = cleaned_msgs
                try:
                    await agentic_graph.aupdate_state(config, cleaned_state_values)
                    print("[Chat] Dropped orphan tool_call messages before new_task restart.")
                except Exception as cleanup_err:
                    print(f"[Chat] WARN: failed to clean orphan tool calls: {cleanup_err}")
                # Reset: this IS the new first turn for this fresh session
                history_msgs = await _load_history_context(session_id)
                inputs = {"messages": [*history_msgs, HumanMessage(content=request.message)], "access_token": token}
                await agentic_graph.ainvoke(inputs, config)
                # Track new first run_id after fresh start
                latest = await agentic_graph.aget_state(config)
                _session_first_run_id[thread_id] = latest.metadata.get("run_id", "") if latest.metadata else ""
        else:
            # Prepend recent chat history so the planner can disambiguate
            # follow-ups like "thông tin sản phẩm vừa mua" → knows which order
            # was just placed in this session.
            history_msgs = await _load_history_context(session_id)
            inputs = {"messages": [*history_msgs, HumanMessage(content=request.message)], "access_token": token}
            if first_run_id:
                from uuid import UUID
                inputs["parent_run_id"] = UUID(first_run_id)

            result = await agentic_graph.ainvoke(inputs, config)

            # Store first run_id so all subsequent turns chain to it
            if is_first_turn:
                latest = await agentic_graph.aget_state(config)
                run_id = latest.metadata.get("run_id", "") if latest.metadata else ""
                if run_id:
                    _session_first_run_id[thread_id] = run_id
                    print(f"[Chat] LangSmith trace started: run_id={run_id}")

        new_state = await agentic_graph.aget_state(config)
        if new_state.next:
            return _waiting_response(session_id, new_state)

        # ── HIL override: check last place_order tool result ─────────────
        # If LLM just ran place_order and it returned a WAITING_USER_PICK
        # status, the LLM should NOT have ended the graph. We override the
        # LLM's verbose markdown reply with the proper HIL payload so the
        # frontend shows a dropdown/form instead of text.
        last_tool_result = _last_tool_result(new_state)
        last_tool_call = _last_tool_call(new_state)
        if (last_tool_call
                and last_tool_call.get("name") == "place_order"
                and isinstance(last_tool_result, dict)
                and last_tool_result.get("status") in {
                    "select_product", "select_variant", "select_address",
                    "select_sale", "set_quantity", "confirm",
                }):
            user_lang = _state_values(new_state).get("user_lang", "vi") or "vi"
            hil = _render_status_result(last_tool_result, user_lang)
            is_terminal = hil.get("is_terminal", False)
            status = "success" if is_terminal else "WAITING_USER_PICK"
            return JSONResponse({
                "status": status,
                "session_id": session_id,
                "reply": hil["display_message"],
                "interrupt_payload": hil,
            })

        response = _success_response(session_id, new_state)
        await _persist_turn(session_id, request.message, response.reply, user_hash)
        return response

    except Exception as e:
        import traceback
        error_str = str(e)
        print(f"[Chat] Error: {error_str}")
        print(f"[Chat] Traceback:\n{traceback.format_exc()}")
        if "content_filter" in error_str.lower():
            raise HTTPException(
                status_code=400,
                detail="Request blocked by content filter. Please rephrase and try again.",
            )
        raise HTTPException(status_code=500, detail=error_str)
