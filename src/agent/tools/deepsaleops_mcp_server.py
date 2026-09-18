"""DeepSaleOps MCP Server."""

import json
from datetime import datetime, timezone
from typing import Optional

from mcp.server.fastmcp import FastMCP

from src.agent.tools.common.api_utils import api_get, api_post

mcp = FastMCP("Deepsaleops_Tools_Server")


# ─── Helpers ───────────────────────────────────────────────────────────────


def _filter_active(items: list, now: datetime) -> list:
    """Keep only rows where isActive=1 and not expired."""
    result = []
    for item in items or []:
        if item.get("isActive") != 1:
            continue
        exp = item.get("expiredDate") or item.get("effectiveTo")
        if exp:
            try:
                exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                if exp_dt < now:
                    continue
            except Exception:
                pass
        result.append(item)
    return result


def _active_pricing(pricings: list, now: datetime) -> Optional[dict]:
    """Return the currently-in-window pricing, or the most-recent active one."""
    in_window, active = [], []
    for p in pricings or []:
        if p.get("isActive") != 1 and p.get("active") != 1:
            continue
        frm = p.get("effectiveFrom")
        to = p.get("effectiveTo")
        try:
            frm_dt = datetime.fromisoformat(frm.replace("Z", "+00:00")) if frm else None
            to_dt = datetime.fromisoformat(to.replace("Z", "+00:00")) if to else None
        except Exception:
            continue
        if frm_dt and to_dt and frm_dt <= now <= to_dt:
            in_window.append(p)
        elif frm_dt:
            active.append(p)
    if in_window:
        in_window.sort(key=lambda p: p.get("effectiveFrom", ""), reverse=True)
        return in_window[0]
    if active:
        active.sort(key=lambda p: p.get("effectiveFrom", ""), reverse=True)
        return active[0]
    return None


def _strip(code: Optional[str]) -> Optional[str]:
    if code is None:
        return None
    s = str(code).strip()
    if not s or s.lower() in ("null", "none"):
        return None
    return s


def _unwrap(data) -> dict:
    """Normalize API response — some endpoints return a bare list instead of
    a dict wrapper. Coerce to {'items': [...]} so downstream `.get('items')`
    never breaks with AttributeError."""
    if isinstance(data, list):
        return {"items": data}
    if isinstance(data, dict):
        return data
    return {"items": [], "error": "Invalid response shape"}


# ─── INTERNAL: LIST VARIANTS (not exposed to LLM) ─────────────────────────


async def _list_variants_internal(on_sale_product_id: str, access_token: str) -> dict:
    """Fetch all variants for a product and enrich with active pricing. Used by place_order only."""
    if not on_sale_product_id.strip():
        return {"error": "MISSING_ARGUMENT", "detail": "on_sale_product_id is required"}

    raw = await api_get(
        f"/v1/product/on-sale/{on_sale_product_id.strip()}/variant", access_token
    )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "Invalid response", "raw": raw}
    if isinstance(data, dict) and data.get("error"):
        return data

    # Normalize: upstream may return bare list or {items:[]}/{variants:[]}
    if isinstance(data, list):
        raw_list = data
    elif isinstance(data, dict):
        raw_list = data.get("items") or data.get("variants") or []
    else:
        raw_list = []

    # Guard: items may be list-of-lists or scalars (corrupt upstream response)
    normalized = []
    for v in raw_list or []:
        if isinstance(v, dict):
            normalized.append(v)
        elif v:  # scalar id fallback
            normalized.append({"onSaleProductVariantId": str(v), "name": "Default"})

    if not normalized:
        return {"productId": on_sale_product_id, "variants": [], "totalCount": 0}

    now = datetime.now(timezone.utc)

    async def enrich(v):
        # Guard: upstream may return list-of-lists in some edge cases
        if not isinstance(v, dict):
            v = {"onSaleProductVariantId": str(v) if v else "", "name": "Default"}
        vid = v.get("onSaleProductVariantId") or v.get("variantId") or v.get("id")
        out = {
            "onSaleProductVariantId": vid,
            "name": v.get("name") or "Default",
            "description": v.get("description") or "",
            "packagingType": v.get("packagingType") or "",
            "netContent": v.get("netContent") or "",
            "price": None,
            "currencyId": None,
        }
        if vid:
            r = await api_get(f"/v1/product/on-sale/variant/{vid}", access_token)
            try:
                d = json.loads(r)
            except json.JSONDecodeError:
                return out
            if isinstance(d, dict) and not d.get("error"):
                active = _active_pricing(d.get("productPricings") or [], now)
                if active:
                    out["price"] = active.get("price") or active.get("unitPrice")
                    out["currencyId"] = active.get("currencyId")
                if d.get("description"):
                    out["description"] = d["description"]
                if d.get("packagingType"):
                    out["packagingType"] = d["packagingType"]
                if d.get("netContent"):
                    out["netContent"] = d["netContent"]
                if d.get("name"):
                    out["name"] = d["name"]
        return out

    import asyncio
    enriched = await asyncio.gather(*[enrich(v) for v in raw_list])

    return {
        "productId": on_sale_product_id,
        "variants": list(enriched),
        "totalCount": len(enriched),
    }


# ─── INTERNAL: SEARCH (not exposed to LLM) ─────────────────────────────────


async def _search_on_sale_products_internal(
    access_token: str,
    keyword: str = "",
    business_category_id: str = "",
    page_number: int = 1,
    page_size: int = 20,
) -> dict:
    """Internal search — used by place_order only. NOT a tool."""
    body = {
        "keyword": keyword.strip() if keyword else "",
        "pageNumber": page_number,
        "pageSize": page_size,
    }
    if business_category_id.strip():
        body["businessCategoryId"] = business_category_id.strip()
    raw = await api_post("/v1/product/on-sale/search", access_token, body)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "Invalid response", "raw": raw}



# ─── Discounts (READ-ONLY INFO — moved to deeptrace_mcp_server) ─────────


async def _filter_active_discounts(on_sale_product_id: str, access_token: str) -> dict:
    """Internal helper: fetch + filter active discounts for a product.
    Used by place_order to enrich confirm step. Not exposed to LLM."""
    if not on_sale_product_id:
        return {"categories": []}
    raw = await api_get(f"/v1/discount/on-sale/{on_sale_product_id}", access_token)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"categories": []}
    if data.get("error"):
        return {"categories": []}
    now = datetime.now(timezone.utc)
    return {
        "categories": [
            {"id": c.get("id"), "name": c.get("name"), "discounts": _filter_active(c.get("discounts") or [], now)}
            for c in (data.get("categories") or [])
        ]
    }


# ─── Order pre-flight ──────────────────────────────────────────────────────


async def _list_user_addresses_internal(access_token: str) -> dict:
    """Fetch user's addresses. Used by place_order only."""
    raw = await api_get("/v1/user/address", access_token)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "Invalid response", "raw": raw}
    if isinstance(data, dict) and data.get("error"):
        return data
    items = (
        data if isinstance(data, list)
        else data.get("userAddresses")
        or data.get("items")
        or data.get("addresses")
        or data.get("data")
        or []
    )
    return {"items": items, "totalCount": len(items)}


async def _list_sales_for_product_internal(
    on_sale_product_id: str, access_token: str, page_number: int = 1, page_size: int = 20
) -> dict:
    """Fetch sales reps for a product. Used by place_order only."""
    if not on_sale_product_id.strip():
        return {"error": "MISSING_ARGUMENT", "detail": "on_sale_product_id is required"}
    raw = await api_get(
        f"/v1/product/{on_sale_product_id.strip()}/sale",
        access_token,
        params={"pageNumber": page_number, "pageSize": page_size},
    )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "Invalid response", "raw": raw}
    if isinstance(data, dict) and data.get("error"):
        return data
    items = (
        data if isinstance(data, list)
        else data.get("items")
        or data.get("sales")
        or data.get("salesReps")
        or data.get("data")
        or []
    )
    return {"items": items, "totalCount": len(items)}


# ─── CREATE ORDER ────────────────────────────────────────────────────────


# ─── INTERNAL: CREATE ORDER (already impl below) ─────────────────────────


async def _create_order_impl(
    on_sale_product_variant_id: str,
    quantity: int,
    warehouse_id: str,
    user_addresses_id: str,
    city: str,
    district: str,
    longitude: float,
    latitude: float,
    email: str,
    snapshot_price: float,
    access_token: str,
    shipping_fee: float = 0,
    on_sale_product_id: Optional[str] = None,
    product_name: Optional[str] = None,
    variant_name: Optional[str] = None,
    unit_price: Optional[float] = None,
    sale_id: Optional[str] = None,
    default_discount_code: Optional[str] = None,
    campaign_discount_code: Optional[str] = None,
) -> dict:
    """Internal implementation. Called by place_order only. NOT a tool."""
    payload = {
        "onSaleProductVariants": [{
            "onSaleProductVariantId": on_sale_product_variant_id,
            "quantity": int(quantity),
            "defaultDiscountCode": _strip(default_discount_code),
            "campaignDiscountCode": _strip(campaign_discount_code),
        }],
        "warehouseId": warehouse_id,
        "userAddressId": user_addresses_id,
        "snapshotPrice": float(snapshot_price),
        "shippingFee": float(shipping_fee),
        "email": email.strip(),
        "city": city,
        "district": district,
        "longitude": float(longitude),
        "latitude": float(latitude),
    }
    # Backend requires either UserId OR (Address + PhoneNumber). We pass the
    # userId from the current cookie session; phoneNumber fallback is rarely
    # available in the address payload so userId is the reliable path.
    from src.agent.tools.common.api_utils import get_current_user_id
    user_id = get_current_user_id()
    if user_id:
        payload["userId"] = user_id
    if on_sale_product_id:
        payload["onSaleProductId"] = on_sale_product_id
    if sale_id:
        payload["saleId"] = sale_id

    raw = await api_post("/v1/order/create-for-customer", access_token, payload)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "Invalid response", "raw": raw}


# ─── STATE MACHINE: place_order ──────────────────────────────────────────


def _encode_state(state: dict) -> str:
    """Opaque base64 state for LLM — do not read or modify."""
    import base64
    return base64.b64encode(json.dumps(state).encode()).decode()


def _decode_state(encoded: str) -> dict:
    """Decode opaque state. Returns {} on failure."""
    import base64
    try:
        return json.loads(base64.b64decode(encoded.encode()).decode())
    except Exception:
        return {}


async def _auto_pick_warehouse(variant_id: str, access_token: str) -> tuple:
    """Fetch warehouse with highest stock. Returns (warehouse_id, available)."""
    raw = await api_get(f"/v1/warehouse/on-sale-product-variant/{variant_id.strip()}/availability", access_token)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "", 0
    if isinstance(data, dict) and data.get("error"):
        return "", 0
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("items") or []
    else:
        items = []
    if not items:
        return "", 0
    # Filter non-dict entries defensively
    items = [w for w in items if isinstance(w, dict)]
    if not items:
        return "", 0
    best = max(items, key=lambda w: w.get("availableStock", 0))
    wid = best.get("warehouseId") or best.get("id") or ""
    stock = best.get("availableStock", 0)
    return wid, stock


@mcp.tool()
async def place_order(
    action: str,
    access_token: str,
    state: str = "",
    keyword: str = "",
    product_id: str = "",
    variant_id: str = "",
    address_id: str = "",
    sale_id: str = "",
    quantity: int = 0,
    email: str = "",
) -> str:
    """End-to-end order orchestrator — state machine with mandatory HIL.

    This is the ONLY tool the LLM should call when the user wants to place an order.
    Pass the `state` parameter VERBATIM — it is an OPAQUE base64 string.
    Do NOT read, decode, or modify state.

    ACTIONS:
      start          — begin ordering. keyword=product name to search.
                       Validate: keyword non-empty.
                       Next: select_variant (if 1 variant) or select_product (if many).

      select_product — user picked a product. product_id=selected product ID.
                       Validate: product_id non-empty.
                       Next: select_variant.

      select_variant — user picked a variant. variant_id=selected variant ID.
                       Validate: variant_id non-empty.
                       Side-effects: fetch warehouse (auto-pick first), addresses.
                       Next: select_address.

      select_address — user picked an address. address_id=selected address ID.
                       Validate: address_id non-empty.
                       Next: set_quantity.

      set_quantity   — user entered quantity. quantity=int >= 1, email=str.
                       Validate: quantity >= 1, email non-empty.
                       Side-effects: fetch sales (auto-pick if 0 or 1, HIL if many).
                       Next: select_sale (if many sales) or confirm.

      select_sale    — user picked a sale rep. sale_id=selected sale ID.
                       Validate: sale_id non-empty, in available list.
                       Next: confirm.

      confirm        — user confirmed the order summary.
                       Validate: full state valid.
                       Action: POST /v1/order/create-for-customer.
                       Next: created.

    RETURNS: JSON with fields:
      status         — "select_product" | "select_variant" | "select_address" |
                      "set_quantity"   | "select_sale"    | "confirm"        |
                      "created"       | "error"      message        — human-readable message (in Vietnamese by default)
      state          — OPAQUE base64 state string. Pass back verbatim on next call.
      data           — list of options for the next UI step (variants, addresses, etc.)
      order          — full order details (only when status="created")
    """
    # Decode state
    cur = _decode_state(state) if state else {}

    # START
    if action == "start":
        print(f"[place_order] start: keyword='{keyword}'")
        if not keyword.strip():
            return json.dumps({"status": "error", "message": "Từ khóa tìm kiếm không được trống.", "state": state}, ensure_ascii=False)

        search_result = await _search_on_sale_products_internal(
            access_token=access_token,
            keyword=keyword.strip(),
        )
        print(f"[place_order] search_result: keys={list(search_result.keys()) if isinstance(search_result, dict) else type(search_result)}")

        items = (search_result.get("items") or search_result.get("data") or [])
        if not items:
            return json.dumps({"status": "error", "message": f"Không tìm thấy sản phẩm nào cho '{keyword}'.", "state": state}, ensure_ascii=False)

        new_state = dict(cur, keyword=keyword.strip(), products=items)
        encoded = _encode_state(new_state)

        if len(items) == 1:
            product_id = items[0].get("productId") or items[0].get("id") or items[0].get("onSaleProductId") or ""
            if not product_id:
                return json.dumps({"status": "error", "message": "Sản phẩm không có ID.", "state": state}, ensure_ascii=False)

            variant_data = await _list_variants_internal(product_id, access_token)
            if variant_data.get("error"):
                return json.dumps({"status": "error", "message": "Không lấy được biến thể.", "state": state}, ensure_ascii=False)
            enriched = variant_data.get("variants") or []
            if not enriched:
                return json.dumps({"status": "error", "message": "Sản phẩm không có biến thể nào.", "state": state}, ensure_ascii=False)

            new_state2 = dict(new_state, selected_product_id=product_id, variants=enriched)
            encoded2 = _encode_state(new_state2)

            if len(enriched) == 1:
                v = enriched[0]
                vid = v.get("onSaleProductVariantId")
                warehouse_id, available = await _auto_pick_warehouse(vid, access_token)
                if not warehouse_id:
                    return json.dumps({"status": "error", "message": "Sản phẩm không có hàng trong kho.", "state": encoded2}, ensure_ascii=False)
                addr_data = await _list_user_addresses_internal(access_token)
                addresses = addr_data.get("items") or addr_data.get("addresses") or []
                new_state3 = dict(new_state2, selected_variant_id=vid, selected_variant=v,
                                  warehouse_id=warehouse_id, warehouse_available=available, addresses=addresses)
                return json.dumps({
                    "status": "select_address",
                    "message": "Vui lòng chọn địa chỉ giao hàng:",
                    "state": _encode_state(new_state3),
                    "data": {"addresses": addresses, "variant": v},
                }, ensure_ascii=False)

            return json.dumps({
                "status": "select_variant",
                "message": "Vui lòng chọn biến thể:",
                "state": encoded2,
                "data": {"variants": enriched, "product": items[0]},
            }, ensure_ascii=False)

        return json.dumps({
            "status": "select_product",
            "message": f"Tìm thấy {len(items)} sản phẩm. Vui lòng chọn 1 sản phẩm:",
            "state": encoded,
            "data": {"products": items},
        }, ensure_ascii=False)

    # SELECT PRODUCT
    if action == "select_product":
        if not product_id.strip():
            return json.dumps({"status": "error", "message": "Vui lòng chọn 1 sản phẩm.", "state": state}, ensure_ascii=False)

        variant_data = await _list_variants_internal(product_id.strip(), access_token)
        if variant_data.get("error"):
            return json.dumps({
                "status": "error",
                "message": f"Không lấy được biến thể: {variant_data.get('error')}",
                "state": state,
            }, ensure_ascii=False)

        enriched = variant_data.get("variants") or []
        if not enriched:
            return json.dumps({"status": "error", "message": "Sản phẩm không có biến thể nào.", "state": state}, ensure_ascii=False)

        new_state = dict(cur, selected_product_id=product_id.strip(), variants=enriched)
        return json.dumps({
            "status": "select_variant",
            "message": "Vui lòng chọn biến thể:",
            "state": _encode_state(new_state),
            "data": {"variants": enriched},
        }, ensure_ascii=False)

    # SELECT VARIANT
    if action == "select_variant":
        if not variant_id.strip():
            return json.dumps({"status": "error", "message": "Vui lòng chọn 1 biến thể.", "state": state}, ensure_ascii=False)

        variants = cur.get("variants", [])
        selected_variant = next((v for v in variants
                                 if (v.get("onSaleProductVariantId") or v.get("variantId") or v.get("id")) == variant_id.strip()), None)
        if not selected_variant:
            return json.dumps({"status": "error", "message": "Biến thể không hợp lệ.", "state": state}, ensure_ascii=False)

        warehouse_id, available = await _auto_pick_warehouse(variant_id.strip(), access_token)
        if not warehouse_id:
            return json.dumps({"status": "error", "message": "Sản phẩm không có hàng trong kho.", "state": state}, ensure_ascii=False)

        addr_data = await _list_user_addresses_internal(access_token)
        addresses = addr_data.get("items") or addr_data.get("addresses") or []

        new_state = dict(cur, selected_variant_id=variant_id.strip(), selected_variant=selected_variant,
                         warehouse_id=warehouse_id, warehouse_available=available, addresses=addresses)
        return json.dumps({
            "status": "select_address",
            "message": "Vui lòng chọn địa chỉ giao hàng:",
            "state": _encode_state(new_state),
            "data": {"addresses": addresses, "variant": selected_variant},
        }, ensure_ascii=False)

    # SELECT ADDRESS
    if action == "select_address":
        if not address_id.strip():
            return json.dumps({"status": "error", "message": "Vui lòng chọn 1 địa chỉ.", "state": state}, ensure_ascii=False)

        addresses = cur.get("addresses", [])
        selected_address = next(
            (a for a in addresses
             if address_id.strip() in {
                 a.get("addressId"), a.get("id"), a.get("userAddressId"),
                 a.get("userAddressesId"), a.get("user_addresses_id"),
             }),
            None,
        )
        if not selected_address:
            return json.dumps({"status": "error", "message": "Địa chỉ không hợp lệ.", "state": state}, ensure_ascii=False)

        new_state = dict(cur, selected_address_id=address_id.strip(), selected_address=selected_address)
        return json.dumps({
            "status": "set_quantity",
            "message": "Vui lòng nhập số lượng và email để tiếp tục:",
            "state": _encode_state(new_state),
            "data": {
                "address": selected_address,
                "variant": cur.get("selected_variant"),
                "product_name": cur.get("keyword", ""),
            },
        }, ensure_ascii=False)

    # SET QUANTITY
    if action == "set_quantity":
        if quantity <= 0:
            return json.dumps({"status": "error", "message": "Số lượng phải lớn hơn 0.", "state": state}, ensure_ascii=False)
        if not email.strip() or "@" not in email:
            return json.dumps({"status": "error", "message": "Email không hợp lệ.", "state": state}, ensure_ascii=False)

        variant = cur.get("selected_variant", {})
        price = variant.get("price") or 0.0
        subtotal = price * quantity

        # Fetch sales for HIL — may be 0/1/many. If 0 or 1, skip; else prompt.
        product_id = cur.get("selected_product_id", "")
        sales_data = await _list_sales_for_product_internal(product_id, access_token) if product_id else {"items": []}
        sales = sales_data.get("items") or []

        new_state = dict(cur, quantity=quantity, email=email.strip(),
                         unit_price=price, subtotal=subtotal, sales=sales)

        # 0 sales: skip HIL, go straight to confirm
        if len(sales) == 0:
            return json.dumps({
                "status": "confirm",
                "message": "Xác nhận đơn hàng:",
                "state": _encode_state(new_state),
                "data": {
                    "product_name": cur.get("keyword", ""),
                    "variant": variant,
                    "quantity": quantity,
                    "unit_price": price,
                    "subtotal": subtotal,
                    "address": cur.get("selected_address"),
                    "warehouse_id": cur.get("warehouse_id", ""),
                    "warehouse_available": cur.get("warehouse_available", 0),
                    "email": email.strip(),
                    "sales": sales,
                },
            }, ensure_ascii=False)

        # 1 sale: auto-pick, skip HIL
        if len(sales) == 1:
            new_state["selected_sale_id"] = sales[0].get("id") or sales[0].get("saleId") or ""
            new_state["selected_sale"] = sales[0]
            return json.dumps({
                "status": "confirm",
                "message": "Xác nhận đơn hàng:",
                "state": _encode_state(new_state),
                "data": {
                    "product_name": cur.get("keyword", ""),
                    "variant": variant,
                    "quantity": quantity,
                    "unit_price": price,
                    "subtotal": subtotal,
                    "address": cur.get("selected_address"),
                    "warehouse_id": cur.get("warehouse_id", ""),
                    "warehouse_available": cur.get("warehouse_available", 0),
                    "email": email.strip(),
                    "sales": sales,
                    "selected_sale": sales[0],
                },
            }, ensure_ascii=False)

        # many sales: HIL dropdown
        return json.dumps({
            "status": "select_sale",
            "message": "Vui lòng chọn sale phụ trách:",
            "state": _encode_state(new_state),
            "data": {
                "sales": sales,
                "variant": variant,
                "quantity": quantity,
                "unit_price": price,
                "subtotal": subtotal,
            },
        }, ensure_ascii=False)

    # SELECT SALE
    if action == "select_sale":
        sales = cur.get("sales") or []
        if not sales:
            return json.dumps({"status": "error", "message": "Không có sale nào để chọn.", "state": state}, ensure_ascii=False)

        # Match by userId (sale schema: {userId, email, phoneNumber, avatarUrl})
        match = None
        for s in sales:
            sid = (s.get("userId") or s.get("id") or s.get("saleId")
                   or s.get("salesRepId") or "")
            if sid and sid == sale_id.strip():
                match = s
                break
        if not match:
            return json.dumps({"status": "error", "message": "Vui lòng chọn 1 sale.", "state": state}, ensure_ascii=False)

        variant = cur.get("selected_variant", {})
        new_state = dict(cur,
                         selected_sale_id=match.get("userId") or match.get("id") or match.get("saleId") or "",
                         selected_sale=match)
        return json.dumps({
            "status": "confirm",
            "message": "Xác nhận đơn hàng:",
            "state": _encode_state(new_state),
            "data": {
                "product_name": cur.get("keyword", ""),
                "variant": variant,
                "quantity": cur.get("quantity"),
                "unit_price": cur.get("unit_price"),
                "subtotal": cur.get("subtotal"),
                "address": cur.get("selected_address"),
                "warehouse_id": cur.get("warehouse_id", ""),
                "warehouse_available": cur.get("warehouse_available", 0),
                "email": cur.get("email"),
                "selected_sale": match,
            },
        }, ensure_ascii=False)

    # CONFIRM
    if action == "confirm":
        missing = []
        if not cur.get("selected_variant_id"):
            missing.append("biến thể")
        if not cur.get("quantity", 0) > 0:
            missing.append("số lượng")
        if not cur.get("warehouse_id"):
            missing.append("kho hàng")
        if not cur.get("selected_address_id"):
            missing.append("địa chỉ")
        if not cur.get("email"):
            missing.append("email")
        if missing:
            return json.dumps({
                "status": "error",
                "message": f"Thiếu thông tin: {', '.join(missing)}. Vui lòng bắt đầu lại.",
                "state": "",
            }, ensure_ascii=False)

        variant = cur.get("selected_variant", {})
        addr = cur.get("selected_address", {})
        price = cur.get("unit_price") or variant.get("price") or 0.0
        city = addr.get("city") or addr.get("province") or addr.get("cityName") or ""
        district = addr.get("district") or addr.get("districtName") or ""

        order_result = await _create_order_impl(
            on_sale_product_variant_id=cur["selected_variant_id"],
            quantity=cur["quantity"],
            warehouse_id=cur["warehouse_id"],
            user_addresses_id=cur["selected_address_id"],
            city=city,
            district=district,
            longitude=float(addr.get("longitude", 0) or 0),
            latitude=float(addr.get("latitude", 0) or 0),
            email=cur["email"],
            snapshot_price=price,
            access_token=access_token,
            shipping_fee=0,
            product_name=cur.get("keyword", ""),
            variant_name=variant.get("name", ""),
            unit_price=price,
        )

        if order_result.get("error"):
            return json.dumps({
                "status": "error",
                "message": f"Lỗi tạo đơn: {order_result.get('error')} — {order_result.get('detail', '')}",
                "state": state,
            }, ensure_ascii=False)

        if order_result.get("orderId"):
            return json.dumps({
                "status": "created",
                "message": "Đơn hàng đã được tạo thành công!",
                "state": "",
                "order": order_result,
            }, ensure_ascii=False)

        return json.dumps({
            "status": "error",
            "message": f"Không tạo được đơn: {order_result}",
            "state": state,
        }, ensure_ascii=False)

    return json.dumps({"status": "error", "message": f"Unknown action: {action}", "state": state}, ensure_ascii=False)


# ─── Post-order ───────────────────────────────────────────────────────────
# (get_order_items moved to deeptrace_mcp_server)


if __name__ == "__main__":
    mcp.run(transport="stdio")
