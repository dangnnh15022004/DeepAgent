"""
DeeptraceSubAgent - MCP tools for product master data and traceability.

Tools (all read-only — no write tools in this server):
- search_company             (resolve company name/email/taxCode -> companyId)
- get_company_product_summary (product count + categories of a company)
- search_product             (resolve product name -> productId + full detail)
- get_product_batches        (list batches of a product)
- get_batch_manufacturing_log (full manufacturing log + image proofs of a batch)
- get_my_profile             (current user profile + companyList)
"""
import json
import os
import re
import sys
import uuid

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Deeptrace_Tools_Server")
BASE_URL = "https://deepsalesops-dev-api.deep.com.vn"

# Tools that take a UUID identifier (company_id, product_id, batch_id).
# If the LLM forwards a NAME instead of a UUID, we reject with a clear hint
# instead of letting the backend return a silent 404.
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _err(message: str, hint: str | None = None) -> str:
    """Standard error payload for the LLM.

    Always returns structured JSON so the LLM can parse it reliably instead
    of guessing from a bare string. A `hint` is included when the error is
    the agent's fault (wrong arg shape) — never for upstream API failures.
    """
    payload: dict = {"error": message}
    if hint:
        payload["hint"] = hint
    return json.dumps(payload, ensure_ascii=False)


def _require_uuid(value: str, field: str) -> str | None:
    """Return an error JSON string if `value` is not a UUID, else None."""
    if value and _UUID_RE.match(value.strip()):
        return None
    return _err(
        f"Invalid {field}: expected a UUID (e.g. '0a9205ab-cb79-42b3-a92e-ff86cd416ce5'), got: {value!r}",
        hint=f"Resolve the NAME into an ID first via the appropriate search tool, then pass that ID as {field}.",
    )


def _strip_bearer_prefix(user_access_token: str) -> str:
    """LLM sometimes echoes `Bearer eyJ...` instead of the bare token.

    Strip a leading `Bearer ` so we don't send `Authorization: Bearer Bearer eyJ...`.
    """
    if not user_access_token:
        return user_access_token
    s = user_access_token.strip()
    if len(s) > 7 and s[:7].lower() == "bearer ":
        return s[7:].strip()
    return s


# ─── COMPANY ────────────────────────────────────────────────────────────────


@mcp.tool()
async def search_company(
    user_access_token: str,
    company_name: str = "",
    contact_email: str = "",
    company_id: str = "",
    tax_code: str = "",
) -> str:
    """Resolve a company name/email/ID/tax-code into the system `companyId`.

    Use when: user mentions a company by name, email, or tax code and you need
    the `companyId` to call `get_company_product_summary`.

    CRITICAL RULES:
    - At least ONE of (company_name, contact_email, company_id, tax_code) is required.
    - DO NOT fabricate company info; rely on this tool's return.

    Returns: {"companyId", "companyName", "contactEmail", "taxCode", "address"}.
    """
    if not any([company_name, contact_email, company_id, tax_code]):
        return _err("No search criteria provided.", "Pass at least one of company_name, contact_email, company_id, or tax_code.")

    payload = {k: v for k, v in {
        "companyName": company_name,
        "contactEmail": contact_email,
        "companyId": company_id,
        "taxCode": tax_code,
    }.items() if v}

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                f"{BASE_URL}/api/v1/company/search",
                json=payload,
                headers={"Authorization": f"Bearer {_strip_bearer_prefix(user_access_token)}"},
            )
        if res.status_code != 200:
            return _err(f"Upstream API error: HTTP {res.status_code}.")
        data = res.json().get("items", [])
        if not data:
            return _err("No company matched the search criteria.")
        if len(data) > 1:
            return _err("Multiple companies matched — ask the user for a more specific name/email/tax-code.")
        c = data[0]
        return json.dumps({
            "companyId": c.get("companyId"),
            "companyName": c.get("companyName"),
            "contactEmail": c.get("contactEmail"),
            "taxCode": c.get("taxCode"),
            "address": c.get("address"),
        }, ensure_ascii=False)
    except Exception:
        return _err("Connection error while calling search_company.")


@mcp.tool()
async def get_company_product_summary(company_id: str, user_access_token: str) -> str:
    """Get the total product count and category list of a company.

    Use when: user asks for "danh sách sản phẩm của công ty X" or an overview.

    CRITICAL RULES:
    - `company_id` MUST be a UUID from `search_company.companyId`. DO NOT pass the company name.

    Returns: {"totalProducts": int, "categories": [str, ...]}.
    """
    if err := _require_uuid(company_id, "company_id"):
        return err
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{BASE_URL}/api/v1/company/list-product/{company_id.strip()}",
                headers={"Authorization": f"Bearer {_strip_bearer_prefix(user_access_token)}"},
            )
        if res.status_code != 200:
            return _err(f"Upstream API error: HTTP {res.status_code}.")
        data = res.json()
        categories = {item.get("category").strip().capitalize() for item in data.get("items", []) if item.get("category")}
        return json.dumps({
            "totalProducts": data.get("totalCount", 0),
            "categories": sorted(c for c in categories if "test" not in c.lower()),
        }, ensure_ascii=False)
    except Exception:
        return _err("Connection error while calling get_company_product_summary.")


# ─── PRODUCT ────────────────────────────────────────────────────────────────


# @mcp.tool()
# async def search_product(
#     product_name: str = "",
#     keyword: str = "",
#     user_access_token: str = "",
#     sale_company_id: str = "",
#     business_category_id: str = "",
#     page_number: int = 1,
#     page_size: int = 20,
# ) -> str:
#     """Find products that are CURRENTLY ON SALE matching a keyword.

#     This tool is the canonical entry-point for product lookup: it always
#     searches the on-sale commercial catalog (DeepSaleOps DB), which is the
#     catalog customers actually browse for purchase.

#     Accepts either `product_name` (legacy/alias) or `keyword` — whichever
#     the model supplies, the value is used as the search keyword.

#     Use when: user asks to find / list / search a product by name, asks
#     "có bán gì", "có sản phẩm gì", "tìm sản phẩm yến sào", etc.

#     RULES:
#     - Provide ONE of `product_name` or `keyword`. If both are empty, ask
#       the user for the product name.
#     - On success, render the on-sale price, available stock, sale company,
#       and any returned images/descriptions — translate labels into the
#       customer's language but keep codes/prices intact.

#     Returns: array of on-sale product records.
#     """
#     kw = (keyword or product_name).strip()
#     if not kw:
#         return _err("Missing required arg `keyword` (or `product_name`). Ask the user for a product name.")
#     return await search_on_sale_products(
#         keyword=kw,
#         user_access_token=user_access_token,
#         sale_company_id=sale_company_id,
#         business_category_id=business_category_id,
#         page_number=page_number,
#         page_size=page_size,
#     )


@mcp.tool()
async def search_on_sale_products(
    keyword: str,
    user_access_token: str,
    sale_company_id: str = "",
    business_category_id: str = "",
    page_number: int = 1,
    page_size: int = 20,
) -> str:
    """Search products CURRENTLY ON SALE by keyword.

    Use when: user explicitly wants to BUY / BROWSE / check price. Examples:
      "Có yến sào đang bán không?"
      "Gợi ý sản phẩm cho tôi"
      "Cho tôi xem giá sản phẩm X"

    For ambiguous queries (just a product name with no verb), the planner
    will return [] and the Synthesizer will ask the customer to clarify
    BUY vs TRACE before this tool is ever called.

    Routing pair:
      - `search_on_sale_products` (this tool) — BUYING / BROWSE / PRICE
      - `search_product` — TRACEABILITY / ORIGIN / BATCH / INGREDIENT

    Each item: productId, productName, gtin, gln, category, countryOfOrigin,
    description, ingredientOrigin, mainIngredients[], manufacturerName,
    manufacturingAddress, packagingType, netContent, onSaleProductImageUrl,
    minRetailPrice, maxRetailPrice, created.
    TotalCount, pageNumber, pageSize, totalPages for pagination.

    Chain: search_on_sale_products -> get_on_sale_product ->
           list_on_sale_product_variants or get_on_sale_product_variant
    """
    if not keyword.strip():
        return _err("Missing required arg `keyword`.", "Ask the user for a search keyword (e.g. 'yến sào').")

    payload: dict = {"keyword": keyword.strip(), "pageNumber": page_number, "pageSize": page_size}
    if sale_company_id.strip():
        payload["saleCompanyId"] = sale_company_id.strip()
    if business_category_id.strip():
        payload["businessCategoryId"] = business_category_id.strip()

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                f"{BASE_URL}/api/v1/product/on-sale/search",
                json=payload,
                headers={"Authorization": f"Bearer {_strip_bearer_prefix(user_access_token)}"},
            )
        if res.status_code != 200:
            return _err(f"Upstream API error: HTTP {res.status_code}.")
        data = res.json()
        items = data.get("items", []) or []
        cleaned = [{k: v for k, v in i.items() if k != "dynamicFieldSchema"} for i in items]
        return json.dumps({
            "items": cleaned,
            "totalCount": data.get("totalCount"),
            "pageNumber": data.get("pageNumber"),
            "pageSize": data.get("pageSize"),
            "totalPages": data.get("totalPages"),
        }, ensure_ascii=False)
    except Exception:
        return _err("Connection error while calling search_on_sale_products.")


# ─── BATCH & MANUFACTURING LOG ─────────────────────────────────────────────


@mcp.tool()
async def get_product_batches(product_id: str, user_access_token: str) -> str:
    """List all manufacturing batches of a product.

    Use when: user asks "có những lô nào của sản phẩm X" or wants to pick a
    specific batch by name.

    CRITICAL RULES:
    - `product_id` MUST be the UUID from `search_product[].productId`.
    - DO NOT pass the product NAME (e.g. "Dầu gội ...") — it will be rejected.
    - DO NOT pass a batch NAME (e.g. "LTO:28032026") here — that belongs to
      `get_batch_manufacturing_log` and requires resolving the name → UUID
      from THIS tool's result first.

    Returns: [{"batchId", "batchName", "stage", "manufacturedDate", "expiredDate"}, ...]
    """
    if err := _require_uuid(product_id, "product_id"):
        return err
    params = {"pageNumber": 1, "pageSize": 10, "sortBy": "desc"}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{BASE_URL}/api/v1/product/list-batch/{product_id.strip()}",
                params=params,
                headers={"Authorization": f"Bearer {_strip_bearer_prefix(user_access_token)}"},
            )
        if res.status_code != 200:
            return _err(f"Upstream API error: HTTP {res.status_code}.")
        items = res.json().get("items", [])
        if not items:
            return _err("No batches found for this product.")
        return json.dumps([{
            "batchId": i.get("batchId"),
            "batchName": i.get("batchName"),
            "stage": i.get("stage"),
            "manufacturedDate": i.get("manufacturedDate"),
            "expiredDate": i.get("expiredDate"),
        } for i in items], ensure_ascii=False)
    except Exception:
        return _err("Connection error while calling get_product_batches.")


@mcp.tool()
async def get_batch_manufacturing_log(batch_id: str, user_access_token: str) -> str:
    """Fetch the full step-by-step manufacturing log of a single batch.

    Use when: user asks for "chi tiết lô hàng", "quy trình sản xuất", or
    "nhật ký sản xuất" for a specific batch.

    CRITICAL RULES:
    - `batch_id` MUST be the UUID from `get_product_batches[].batchId`.
    - DO NOT pass the batch NAME (e.g. "LTO:28032026") — it will be rejected.

    On success, the response contains `steps[]` with `stepNumber`, `stepName`,
    `formData` (Địa điểm, Thời gian bắt đầu/kết thúc, Người phụ trách) and
    `listProofOfManufacturing` (signed S3 URLs).

    Render rules for the LLM (also enforced at output time):
    - Render EVERY step in order — do NOT skip or collapse steps.
    - Each step's `listProofOfManufacturing` MUST be rendered as Markdown
      images: `![Bước {stepNumber} – {stepName} – ảnh {i}]({url})`, one line
      per URL with 1-based index `i`, under a sub-heading
      `**Hình ảnh chứng minh (Bước {stepNumber} - {stepName}):**`.
    - Keep S3 URLs intact (they include `?X-Amz-...` query params — copy verbatim).
    """
    if err := _require_uuid(batch_id, "batch_id"):
        return err
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{BASE_URL}/api/v1/batch/authorized/{batch_id.strip()}/manufacturing-log",
                headers={"Authorization": f"Bearer {_strip_bearer_prefix(user_access_token)}"},
            )
        if res.status_code == 404:
            return _err("No manufacturing log exists for this batch.")
        if res.status_code != 200:
            return _err(f"Upstream API error: HTTP {res.status_code}.")
        return json.dumps(res.json(), ensure_ascii=False)
    except Exception:
        return _err("Connection error while calling get_batch_manufacturing_log.")


# ─── AUTH ──────────────────────────────────────────────────────────────────


@mcp.tool()
async def get_my_profile(user_access_token: str) -> str:
    """Get the authenticated user's profile + active company list.

    Use FIRST when the user refers to "my products", "my company", "my batches",
    or when the answer depends on identity/role.

    Returns: {userId, email, role, companyList[], primaryCompany}.
    `primaryCompany` is the first active company — safe to pass forward
    without asking the user.
    """
    if not user_access_token.strip():
        return _err("Missing user_access_token.")
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{BASE_URL}/api/v1/auth/me",
                headers={"Authorization": f"Bearer {_strip_bearer_prefix(user_access_token)}"},
            )
        if res.status_code == 401:
            return _err("Authentication failed: token is invalid or expired.")
        if res.status_code != 200:
            return _err(f"Upstream API error: HTTP {res.status_code}.")
        data = res.json()
        company_list = [{
            "companyId": c.get("companyId"),
            "companyName": c.get("companyName"),
            "companyAddress": c.get("companyAddress"),
            "isActive": c.get("isActive"),
        } for c in data.get("companyList", [])]
        active = [c for c in company_list if c.get("isActive") == 1]
        primary = active[0] if active else (company_list[0] if company_list else None)
        return json.dumps({
            "userId": data.get("userId") or data.get("user_id"),
            "email": data.get("email"),
            "role": data.get("role"),
            "companyList": company_list,
            "primaryCompany": primary,
        }, ensure_ascii=False)
    except Exception:
        return _err("Connection error while calling get_my_profile.")


# ─── DEEPSALEOPS READ-ONLY INFO TOOLS (moved from deepsaleops_mcp_server) ──
# These are pure lookup tools — they do NOT trigger or affect the ordering flow.
# For ordering, the agent must go through deepsaleops.place_order (state machine).


@mcp.tool()
async def get_on_sale_product(on_sale_product_id: str, user_access_token: str) -> str:
    """Get full detail of one on-sale product.

    Use when: user wants to see full description, images, manufacturer info
    of a specific product (after search).

    REQUIRED: on_sale_product_id (onSaleProductId UUID), user_access_token.

    NOTE: For ordering, use deepsaleops.place_order instead — this tool is
    read-only and does NOT trigger the HIL ordering flow.
    """
    if not on_sale_product_id.strip():
        return _err("Missing on_sale_product_id.")
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{BASE_URL}/api/v1/product/on-sale/{on_sale_product_id.strip()}",
                headers={"Authorization": f"Bearer {_strip_bearer_prefix(user_access_token)}"},
            )
        if res.status_code != 200:
            return _err(f"Upstream API error: HTTP {res.status_code}.")
        return json.dumps(res.json(), ensure_ascii=False)
    except Exception:
        return _err("Connection error while calling get_on_sale_product.")


@mcp.tool()
async def get_on_sale_product_variant(on_sale_product_variant_id: str, user_access_token: str) -> str:
    """Get one variant's details (name, weight, packaging, current active price).

    Use when: user asks about a specific variant's pricing or specs.

    REQUIRED: on_sale_product_variant_id (onSaleProductVariantId UUID),
              user_access_token.

    NOTE: For ordering, use deepsaleops.place_order instead.
    """
    if not on_sale_product_variant_id.strip():
        return _err("Missing on_sale_product_variant_id.")
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{BASE_URL}/api/v1/product/on-sale/variant/{on_sale_product_variant_id.strip()}",
                headers={"Authorization": f"Bearer {_strip_bearer_prefix(user_access_token)}"},
            )
        if res.status_code != 200:
            return _err(f"Upstream API error: HTTP {res.status_code}.")
        data = res.json()
        # Enrich with current active pricing
        pricings = data.get("productPricings") or []
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        active = None
        for p in pricings:
            try:
                frm = p.get("effectiveFrom")
                to = p.get("effectiveTo")
                frm_dt = datetime.fromisoformat(frm.replace("Z", "+00:00")) if frm else None
                to_dt = datetime.fromisoformat(to.replace("Z", "+00:00")) if to else None
                if (not frm_dt or frm_dt <= now) and (not to_dt or to_dt >= now):
                    active = p
                    break
            except Exception:
                continue
        if active:
            data["active_price"] = {
                "unitPrice": active.get("unitPrice"),
                "effectiveFrom": active.get("effectiveFrom"),
                "effectiveTo": active.get("effectiveTo"),
            }
        return json.dumps(data, ensure_ascii=False)
    except Exception:
        return _err("Connection error while calling get_on_sale_product_variant.")


@mcp.tool()
async def get_discounts_for_on_sale_product(on_sale_product_id: str, user_access_token: str) -> str:
    """List ACTIVE discounts for an on-sale product.

    Filters out expired/inactive discount codes so the customer sees only
    usable ones.

    REQUIRED: on_sale_product_id, user_access_token.
    """
    if not on_sale_product_id.strip():
        return _err("Missing on_sale_product_id.")
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{BASE_URL}/api/v1/discount/on-sale/{on_sale_product_id.strip()}",
                headers={"Authorization": f"Bearer {_strip_bearer_prefix(user_access_token)}"},
            )
        if res.status_code != 200:
            return _err(f"Upstream API error: HTTP {res.status_code}.")
        data = res.json()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        def _active(items):
            out = []
            for d in items or []:
                try:
                    exp = d.get("expiredDate")
                    if exp:
                        exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                        if exp_dt < now:
                            continue
                    if d.get("status") and d["status"] not in ("ACTIVE", "active", 1, True):
                        continue
                    out.append(d)
                except Exception:
                    out.append(d)
            return out
        return json.dumps({
            "categories": [
                {"id": c.get("id"), "name": c.get("name"), "discounts": _active(c.get("discounts"))}
                for c in (data.get("categories") or [])
            ]
        }, ensure_ascii=False)
    except Exception:
        return _err("Connection error while calling get_discounts_for_on_sale_product.")


@mcp.tool()
async def get_discounts_by_business_category(
    business_category: str, user_access_token: str, page_number: int = 1, page_size: int = 20
) -> str:
    """List ACTIVE campaign codes by business category (CULTIVATION, AQUACULTURE…).

    REQUIRED: business_category, user_access_token. OPTIONAL: page_number, page_size.
    """
    if not business_category.strip():
        return _err("Missing business_category.")
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{BASE_URL}/api/v1/discount/business-category/{business_category.strip()}",
                params={"pageNumber": page_number, "pageSize": page_size},
                headers={"Authorization": f"Bearer {_strip_bearer_prefix(user_access_token)}"},
            )
        if res.status_code != 200:
            return _err(f"Upstream API error: HTTP {res.status_code}.")
        return json.dumps(res.json(), ensure_ascii=False)
    except Exception:
        return _err("Connection error while calling get_discounts_by_business_category.")


@mcp.tool()
async def get_order_items(order_id: str, user_access_token: str) -> str:
    """Fetch a created order: items, payment status, shipping info.

    Use when: user wants to review a past order ("đơn hàng X của tôi").

    REQUIRED: order_id, user_access_token.
    """
    if not order_id.strip():
        return _err("Missing order_id.")
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{BASE_URL}/api/v1/order/items/{order_id.strip()}",
                headers={"Authorization": f"Bearer {_strip_bearer_prefix(user_access_token)}"},
            )
        if res.status_code != 200:
            return _err(f"Upstream API error: HTTP {res.status_code}.")
        return json.dumps(res.json(), ensure_ascii=False)
    except Exception:
        return _err("Connection error while calling get_order_items.")


if __name__ == "__main__":
    mcp.run(transport="stdio")
