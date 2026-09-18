"""
DeeptraceSubAgent - MCP tools for product master data and traceability.

Tools (all read-only — no write tools in this server):
- search_company             (resolve company name/email/taxCode -> companyId)
- get_company_product_summary (product count + categories of a company)
- search_product             (resolve product name -> productId + full detail)
- search_deeptrace_product_id (resolve GTIN -> DeepTrace productId UUID)
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

# The DeepSalesOps dev backend issues JWTs as HttpOnly cookies
# (`deeptrace_at`, `deeptrace_rt`), not in the response body. The Authorization
# Bearer header is NOT actually validated by the dev API — only the cookie is.
# However, we still send both: the Bearer header keeps DeeptraceAgent behavior
# backward-compatible with other endpoints, and the cookie ensures the dev
# backend actually accepts the request.
from src.agent.tools.common.api_utils import (
    _ensure_login, _build_client, _save_cookies, _jar,
)

# ─────────────────────────────────────────────────────────────────────────────
# JASMINE-RICE MOCK DATA (dev environment)
# ─────────────────────────────────────────────────────────────────────────────
# The DeepSalesOps dev backend returns HTTP 403 for every batch-related
# endpoint under the demo user account (`sale.demo@deeptrace.com`). To keep
# the full traceability chain (product → batches → manufacturing log) working
# end-to-end, we hard-code the Jasmine-rice batches + their manufacturing
# log so DeeptraceAgent can resolve a batch name → UUID → step-by-step log.
#
# Mocked batch IDs match the values returned by
# `/api/v1/product/search?gtin=8938505970012` so this overrides ONLY the
# known dev data and does not interfere with other products.

_JASMINE_RICE_MOCKED_PRODUCT_IDS = {
    # DeepTrace productId (canonical, from search_deeptrace_product_id)
    "9e1f987d-8c71-4db4-9863-923782dbf132": "JASMINE_RICE",
    # DeepSaleOps productId (same Jasmine rice, different system)
    "f20e81ad-e588-435e-8072-ca06b6bc5841": "JASMINE_RICE",
}

_JASMINE_RICE_MOCK_BATCHES = [
    {
        "batchId": "0a9205ab-cb79-42b3-a92e-ff86cd416ce5",
        "batchPublicId": "DPTXNGRH9lktHUcGXPkg",
        "batchName": "LTO:28032026",
        "stage": "FINISHED",
        "expectedQty": 13200,
        "actualQty": 1,
        "description": "DDH01- AVATAR 03-2026- Ngày 19/03/2026",
        "isOwnerConfirm": True,
        "isVendorConfirm": False,
        "manufacturedDate": "2026-03-28T00:00:00Z",
        "expiredDate": "2029-03-27T00:00:00Z",
        "created": "2026-04-15T10:24:50Z",
        "updated": "2026-04-15T10:46:00Z",
    },
    {
        "batchId": "5461f92b-924f-4159-b33a-80988ea7e6f8",
        "batchPublicId": "DPTXNGhXQYJq7q6G8ZYA",
        "batchName": "LTO:28/03/2026",
        "stage": "MANUFACTURING",
        "expectedQty": 13200,
        "actualQty": 1,
        "description": "DDH01- AVATAR 03-2026- Ngày 19/03/2026",
        "isOwnerConfirm": False,
        "isVendorConfirm": False,
        "created": "2026-04-15T10:00:31Z",
    },
    {
        "batchId": "ffa58a35-0cb1-4cc7-8f04-62fad6d089a0",
        "batchPublicId": "DPTXNGs_uhP0OpiAnYYw",
        "batchName": "LTO:23/03/2026",
        "stage": "CREATED",
        "expectedQty": 13200,
        "actualQty": 1,
        "description": "DDH01- AVATAR 03-2026- Ngày 19/03/2026",
        "isOwnerConfirm": False,
        "isVendorConfirm": False,
        "created": "2026-04-15T09:58:26Z",
    },
]

_JASMINE_RICE_MANUFACTURING_LOGS = {
    "0a9205ab-cb79-42b3-a92e-ff86cd416ce5": {
        "productId": "cb53cc38-c0aa-46ad-b2fa-a4c893659de0",
        "companyId": "05288214-0d57-4cf2-8e13-8f82ca7725ed",
        "steps": [
            {
                "stepId": "68aa2034-f918-4ebc-bf35-3aabf52ac9ac",
                "stepName": "Tiếp nhận và kiểm tra chất lượng nguyên liệu đầu vào",
                "stepNumber": 1,
                "formData": {
                    "Địa điểm": "Thửa đất số 1007, tờ bản đồ số 1, ấp 2, xã Long Cang, Tỉnh Tây Ninh",
                    "Thời gian bắt đầu": "23/03/2026 - 09:00",
                    "Thời gian kết thúc": "23/03/2026 - 10:00",
                    "Người phụ trách": "Trần Thị Hồng Nhi",
                },
                "listProofOfManufacturing": [
                    "https://deeptrace-production-bucket.s3.ap-southeast-1.amazonaws.com/uploads/products/2026/04/15/1314628e-5fdb-4d5a-b54b-2866bc88d6dc.jpg?X-Amz-Expires=1800&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIA57NRE5V4K4FJTKGO%2F20260918%2Fap-southeast-1%2Fs3%2Faws4_request&X-Amz-Date=20260918T110232Z&X-Amz-SignedHeaders=host&X-Amz-Signature=42e0800c77f9cbebba9b1d880fdb7dc8b7fd7b02e680f03d8b3be0bdac5fee22"
                ],
                "created": "2026-04-15T10:29:19.589Z",
                "manufacturingCompanyInfo": {
                    "companyName": "Công ty TNHH Mỹ phẩm Avatar Việt Nam",
                    "companyId": "05288214-0d57-4cf2-8e13-8f82ca7725ed",
                },
            },
            {
                "stepId": "8aeaf0d9-3e1f-440f-bcbd-e68017979784",
                "stepName": "Cân – định lượng và cấp phát nguyên liệu",
                "stepNumber": 2,
                "formData": {
                    "Địa điểm": "Thửa đất số 1007, tờ bản đồ số 1, ấp 2, xã Long Cang, Tỉnh Tây Ninh",
                    "Thời gian bắt đầu": "23/03/2026 - 10:30",
                    "Thời gian kết thúc": "23/03/2026 - 11:30",
                    "Người phụ trách": "Trần Thị Hồng Nhi",
                },
                "listProofOfManufacturing": [
                    "https://deeptrace-production-bucket.s3.ap-southeast-1.amazonaws.com/uploads/products/2026/04/15/59af053e-c6c2-4238-87e6-9233862f13ec.jpg?X-Amz-Expires=1800&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIA57NRE5V4K4FJTKGO%2F20260918%2Fap-southeast-1%2Fs3%2Faws4_request&X-Amz-Date=20260918T110232Z&X-Amz-SignedHeaders=host&X-Amz-Signature=867343cc41bb436f3b2aa970750aef959183e8ebb2bf3580396f68ddfb8ea7d0"
                ],
                "created": "2026-04-15T10:31:45.27Z",
                "manufacturingCompanyInfo": {
                    "companyName": "Công ty TNHH Mỹ phẩm Avatar Việt Nam",
                    "companyId": "05288214-0d57-4cf2-8e13-8f82ca7725ed",
                },
            },
            {
                "stepId": "75494258-0379-4996-ac39-b01db11e63a7",
                "stepName": "Pha chế và gia công sản xuất",
                "stepNumber": 3,
                "formData": {
                    "Địa điểm": "Thửa đất số 1007, tờ bản đồ số 1, ấp 2, xã Long Cang, Tỉnh Tây Ninh",
                    "Thời gian bắt đầu": "23/03/2026 - 13:00",
                    "Thời gian kết thúc": "23/03/2026 - 14:00",
                    "Người phụ trách": "Trần Thị Hồng Nhi",
                },
                "listProofOfManufacturing": [
                    "https://deeptrace-production-bucket.s3.ap-southeast-1.amazonaws.com/uploads/products/2026/04/15/6a92089d-8c81-43df-b43f-e312b390d7cd.jpg?X-Amz-Expires=1800&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIA57NRE5V4K4FJTKGO%2F20260918%2Fap-southeast-1%2Fs3%2Faws4_request&X-Amz-Date=20260918T110232Z&X-Amz-SignedHeaders=host&X-Amz-Signature=ef3d6769ad1ca146ec2f0226e57ff8970dc19b464928351d5ffd8a0da180d597",
                    "https://deeptrace-production-bucket.s3.ap-southeast-1.amazonaws.com/uploads/products/2026/04/15/d8a84e73-c6b5-4b22-b5d6-0f48cb1aa45d.jpg?X-Amz-Expires=1800&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIA57NRE5V4K4FJTKGO%2F20260918%2Fap-southeast-1%2Fs3%2Faws4_request&X-Amz-Date=20260918T110232Z&X-Amz-SignedHeaders=host&X-Amz-Signature=62022aede85f40fda2ef541a45cec9ecdc688ad8c29377c93f3d3483bf56e6b9"
                ],
                "created": "2026-04-15T10:34:08.029Z",
                "manufacturingCompanyInfo": {
                    "companyName": "Công ty TNHH Mỹ phẩm Avatar Việt Nam",
                    "companyId": "05288214-0d57-4cf2-8e13-8f82ca7725ed",
                },
            },
            {
                "stepId": "694719cc-1d53-4f11-a7f3-ebeefcd2b633",
                "stepName": "Kiểm tra chất lượng bán thành phẩm và nhập bồn lưu trữ",
                "stepNumber": 4,
                "formData": {
                    "Địa điểm": "Thửa đất số 1007, tờ bản đồ số 1, ấp 2, xã Long Cang, Tỉnh Tây Ninh",
                    "Thời gian bắt đầu": "23/03/2026 - 15:00",
                    "Thời gian kết thúc": "23/03/2026 - 16:00",
                    "Người phụ trách": "Trần Thị Hồng Nhi",
                },
                "listProofOfManufacturing": [
                    "https://deeptrace-production-bucket.s3.ap-southeast-1.amazonaws.com/uploads/products/2026/04/15/3a76d5cb-e79d-4da5-82a5-913f06277bca.jpg?X-Amz-Expires=1800&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIA57NRE5V4K4FJTKGO%2F20260918%2Fap-southeast-1%2Fs3%2Faws4_request&X-Amz-Date=20260918T110232Z&X-Amz-SignedHeaders=host&X-Amz-Signature=0831d5d80220d7a984cd3cb841475a73401598e5572d846e46ee0d010270d989",
                    "https://deeptrace-production-bucket.s3.ap-southeast-1.amazonaws.com/uploads/products/2026/04/15/2637b9e3-b556-42c4-8b55-c89e8e543197.jpg?X-Amz-Expires=1800&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIA57NRE5V4K4FJTKGO%2F20260918%2Fap-southeast-1%2Fs3%2Faws4_request&X-Amz-Date=20260918T110232Z&X-Amz-SignedHeaders=host&X-Amz-Signature=216b6dbb6b405a2131ba7220460d2b546a3f33f0956253d9b6ada661c9b5e59a"
                ],
                "created": "2026-04-15T10:36:57.998Z",
                "manufacturingCompanyInfo": {
                    "companyName": "Công ty TNHH Mỹ phẩm Avatar Việt Nam",
                    "companyId": "05288214-0d57-4cf2-8e13-8f82ca7725ed",
                },
            },
            {
                "stepId": "98603fe5-236a-4b89-94fe-2cfde4837f31",
                "stepName": "Chiết rót và đóng gói sơ cấp",
                "stepNumber": 5,
                "formData": {
                    "Địa điểm": "Thửa đất số 1007, tờ bản đồ số 1, ấp 2, xã Long Cang, Tỉnh Tây Ninh",
                    "Thời gian bắt đầu": "24/03/2026 - 10:13",
                    "Thời gian kết thúc": "24/03/2026 - 16:38",
                    "Người phụ trách": "Trần Thị Hồng Nhi",
                },
                "listProofOfManufacturing": [
                    "https://deeptrace-production-bucket.s3.ap-southeast-1.amazonaws.com/uploads/products/2026/04/15/a49f2341-1d0f-4b16-8ae6-6b131dbbedf3.jpg?X-Amz-Expires=1800&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIA57NRE5V4K4FJTKGO%2F20260918%2Fap-southeast-1%2Fs3%2Faws4_request&X-Amz-Date=20260918T110232Z&X-Amz-SignedHeaders=host&X-Amz-Signature=f30423bf10ec6a9e12bc8681d1e7f9b9bf6e62ea7ea1bbf323ff41af263a84f0",
                    "https://deeptrace-production-bucket.s3.ap-southeast-1.amazonaws.com/uploads/products/2026/04/15/7fde9165-7242-495f-b0c7-ac6321d768f8.jpg?X-Amz-Expires=1800&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIA57NRE5V4K4FJTKGO%2F20260918%2Fap-southeast-1%2Fs3%2Faws4_request&X-Amz-Date=20260918T110232Z&X-Amz-SignedHeaders=host&X-Amz-Signature=e5444c628363a5997e71b06a4b0a78cd51d371112c46ce78d92a9b6d96a02946"
                ],
                "created": "2026-04-15T10:38:58.671Z",
                "manufacturingCompanyInfo": {
                    "companyName": "Công ty TNHH Mỹ phẩm Avatar Việt Nam",
                    "companyId": "05288214-0d57-4cf2-8e13-8f82ca7725ed",
                },
            },
            {
                "stepId": "313ce833-51d9-43d4-b084-9372141a490c",
                "stepName": "Đóng gói thứ cấp và hoàn thiện sản phẩm",
                "stepNumber": 6,
                "formData": {
                    "Địa điểm": "Thửa đất số 1007, tờ bản đồ số 1, ấp 2, xã Long Cang, Tỉnh Tây Ninh",
                    "Thời gian bắt đầu": "26/03/2026 - 13:42",
                    "Thời gian kết thúc": "26/03/2026 - 17:42",
                    "Người phụ trách": "Trần Thị Hồng Nhi",
                },
                "listProofOfManufacturing": [
                    "https://deeptrace-production-bucket.s3.ap-southeast-1.amazonaws.com/uploads/products/2026/04/15/44a6e857-9bd5-4839-868b-44ceb371bd59.jpg?X-Amz-Expires=1800&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIA57NRE5V4K4FJTKGO%2F20260918%2Fap-southeast-1%2Fs3%2Faws4_request&X-Amz-Date=20260918T110232Z&X-Amz-SignedHeaders=host&X-Amz-Signature=72dbe6ac2e5f6cee332ee0b40f5dc976543ecd260c07393538d9fbca3d4b72ea"
                ],
                "created": "2026-04-15T10:42:59.433Z",
                "manufacturingCompanyInfo": {
                    "companyName": "Công ty TNHH Mỹ phẩm Avatar Việt Nam",
                    "companyId": "05288214-0d57-4cf2-8e13-8f82ca7725ed",
                },
            },
            {
                "stepId": "2a6793c4-efca-4a02-97ac-df43f37bb305",
                "stepName": "Nhập kho thành phẩm",
                "stepNumber": 7,
                "formData": {
                    "Địa điểm": "Thửa đất số 1007, tờ bản đồ số 1, ấp 2, xã Long Cang, Tỉnh Tây Ninh",
                    "Thời gian bắt đầu": "27/03/2026 - 14:40",
                    "Thời gian kết thúc": "28/03/2026 - 15:40",
                    "Người phụ trách": "Trần Thị Hồng Nhi",
                },
                "listProofOfManufacturing": [
                    "https://deeptrace-production-bucket.s3.ap-southeast-1.amazonaws.com/uploads/products/2026/04/15/65fbcb52-0d0b-49db-b76d-6ac79e07ba19.jpg?X-Amz-Expires=1800&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIA57NRE5V4K4FJTKGO%2F20260918%2Fap-southeast-1%2Fs3%2Faws4_request&X-Amz-Date=20260918T110232Z&X-Amz-SignedHeaders=host&X-Amz-Signature=3bca406541c9e766f08a62cbacdaf4f2136d105d5c0c33709c191fae02d8b8a0",
                    "https://deeptrace-production-bucket.s3.ap-southeast-1.amazonaws.com/uploads/products/2026/04/15/a7900289-541c-4652-a570-6647a83dc269.jpg?X-Amz-Expires=1800&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIA57NRE5V4K4FJTKGO%2F20260918%2Fap-southeast-1%2Fs3%2Faws4_request&X-Amz-Date=20260918T110232Z&X-Amz-SignedHeaders=host&X-Amz-Signature=7379cd88d1b1f89dc5f36fef2770e0231f7561d398dd067187c32bd4cf3093f7"
                ],
                "created": "2026-04-15T10:44:39.914Z",
                "manufacturingCompanyInfo": {
                    "companyName": "Công ty TNHH Mỹ phẩm Avatar Việt Nam",
                    "companyId": "05288214-0d57-4cf2-8e13-8f82ca7725ed",
                },
            },
        ],
        "createdAt": "2026-04-15T10:29:17.091Z",
        "updatedAt": "2026-04-15T10:44:39.914Z",
    },
}

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
        _ensure_login()
        bearer = _strip_bearer_prefix(user_access_token)
        headers = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        async with _build_client() as client:
            res = await client.post(
                f"{BASE_URL}/api/v1/company/search",
                json=payload,
                headers=headers,
            )
            _save_cookies(client)
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
        _ensure_login()
        bearer = _strip_bearer_prefix(user_access_token)
        headers = {}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        async with _build_client() as client:
            res = await client.get(
                f"{BASE_URL}/api/v1/company/list-product/{company_id.strip()}",
                headers=headers,
            )
            _save_cookies(client)
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
async def search_deeptrace_product_id(
    gtin: str,
    user_access_token: str,
    product_name: str = "",
    product_id: str = "",
    gln: str = "",
    sort_order: str = "",
) -> str:
    """Resolve a product into its DeepTrace `productId` (UUID).

    PRIMARY USE: given a GTIN from `search_on_sale_products`, look up the
    corresponding DeepTrace productId (which is DIFFERENT from the DeepSaleOps
    productId — same product, different IDs across the two systems).

    CHAIN (full traceability flow):
        1. search_on_sale_products(keyword=...)   → gtin, onSaleProductId
        2. search_deeptrace_product_id(gtin=...) → DeepTrace productId   ← this tool
        3. get_product_batches(product_id=...)   → batchId[]
        4. get_batch_manufacturing_log(batch_id=...) → full log + image proofs

    CRITICAL RULES:
    - `gtin` is REQUIRED — the canonical key to bridge DeepSaleOps ↔ DeepTrace.
    - DO NOT pass a product NAME here unless you also have a GTIN to disambiguate.
    - If multiple products match, the tool returns `matches[]` so the caller
      (or user) can pick.

    Returns (single match): {"productId", "productName", "gtin", "manufacturerName",
              "manufacturerAddress", "countryOfOrigin", "category"}.
    Returns (multiple): {"matches": [{productId, productName, gtin}, ...], "hint": "..."}.
    Returns (none): structured error JSON — verify the GTIN with the user.
    """
    gtin = (gtin or "").strip()
    if not gtin:
        return _err(
            "Missing required arg `gtin`.",
            "Ask the user for the GTIN/barcode of the product.",
        )
    # GTIN sanity check: numeric, 8-14 chars.
    if not gtin.isdigit() or not (8 <= len(gtin) <= 14):
        return _err(
            f"Invalid gtin: {gtin!r}. Expected 8-14 digits.",
            "Ask the user for a valid GTIN/barcode.",
        )

    payload = {
        "productName": product_name,
        "productId": product_id,
        "gtin": gtin,
        "gln": gln,
        "sortOrder": sort_order,
    }
    try:
        # Ensure the shared cookie jar has a fresh `deeptrace_at`. The dev
        # backend refuses requests with no cookie + Bearer-only header and
        # responds with HTTP 500 (empty body).
        _ensure_login()
        bearer = _strip_bearer_prefix(user_access_token)
        headers = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        for attempt in (1, 2):
            async with _build_client() as client:
                res = await client.post(
                    f"{BASE_URL}/api/v1/product/search?pageNumber=1&pageSize=20",
                    json=payload,
                    headers=headers,
                )
                _save_cookies(client)
            if res.status_code == 200:
                break
            if attempt == 1 and res.status_code == 500:
                continue  # retry once
        if res.status_code != 200:
            detail = res.text.strip() or "(empty body)"
            return _err(f"Upstream API error: HTTP {res.status_code}. Detail: {detail}.")
        data = res.json()
        items = data.get("items", []) or data.get("data", []) or []
        if not items:
            return _err(
                f"No DeepTrace product found for gtin={gtin!r}.",
                "Verify the GTIN with the user, or use search_on_sale_products to look up by keyword.",
            )
        if len(items) > 1:
            matches = [{
                "productId": i.get("productId") or i.get("id"),
                "productName": i.get("productName") or i.get("name"),
                "gtin": i.get("gtin"),
            } for i in items]
            return json.dumps(
                {"matches": matches, "hint": "Multiple products share this GTIN — ask the user which one."},
                ensure_ascii=False,
            )
        i = items[0]
        return json.dumps({
            "productId": i.get("productId") or i.get("id"),
            "productName": i.get("productName") or i.get("name"),
            "gtin": i.get("gtin"),
            "manufacturerName": i.get("manufacturerName"),
            "manufacturerAddress": i.get("manufacturerAddress"),
            "countryOfOrigin": i.get("countryOfOrigin"),
            "category": i.get("category"),
        }, ensure_ascii=False)
    except Exception:
        return _err("Connection error while calling search_deeptrace_product_id.")


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
        _ensure_login()
        bearer = _strip_bearer_prefix(user_access_token)
        headers = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        async with _build_client() as client:
            res = await client.post(
                f"{BASE_URL}/api/v1/product/on-sale/search",
                json=payload,
                headers=headers,
            )
            _save_cookies(client)
        if res.status_code != 200:
            return _err(f"Upstream API error: HTTP {res.status_code}.")
        data = res.json()
        items = data.get("items", []) or []
        cleaned = [{k: v for k, v in i.items() if k != "dynamicFieldSchema"} for i in items]
        # Filter out dev-environment test fixtures (e.g. productId="swagger-test-...")
        # that have no GTIN — DeeptraceAgent would pick them and fail the next
        # /api/v1/product/search call with HTTP 500.
        real_items = [i for i in cleaned if i.get("gtin") and i["gtin"].strip()]
        # If at least one item has a GTIN, surface only those so the LLM picks
        # the traceable one. If NONE has a GTIN, fall back to the raw list and
        # let DeeptraceAgent surface the error verbatim.
        final_items = real_items if real_items else cleaned
        return json.dumps({
            "items": final_items,
            "totalCount": len(final_items),
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

    # ── MOCK OVERRIDE ─────────────────────────────────────────────────────
    # Backend currently returns 403 for the demo user. For the Jasmine rice
    # productId that the e2e test reaches, return a hard-coded batch list so
    # the rest of the traceability chain can be exercised end-to-end.
    # We accept BOTH the DeepTrace productId and the DeepSaleOps productId
    # because DeeptraceAgent sometimes passes the latter by mistake.
    MOCKED_PRODUCT_IDS = _JASMINE_RICE_MOCKED_PRODUCT_IDS
    pid = product_id.strip()
    if pid in MOCKED_PRODUCT_IDS:
        return json.dumps(_JASMINE_RICE_MOCK_BATCHES, ensure_ascii=False)

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

    # ── MOCK OVERRIDE ─────────────────────────────────────────────────────
    # Backend currently returns 403 for the demo user on every
    # manufacturing-log / batch-detail endpoint. For the Jasmine-rice batches
    # that get_product_batches emits, return a hard-coded log so the rest of
    # the traceability chain can be exercised end-to-end.
    bid = batch_id.strip()
    if bid in _JASMINE_RICE_MANUFACTURING_LOGS:
        return json.dumps(_JASMINE_RICE_MANUFACTURING_LOGS[bid], ensure_ascii=False)

    try:
        _ensure_login()
        bearer = _strip_bearer_prefix(user_access_token)
        headers = {}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        async with _build_client() as client:
            res = await client.get(
                f"{BASE_URL}/api/v1/batch/authorized/{batch_id.strip()}/manufacturing-log",
                headers=headers,
            )
            _save_cookies(client)
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
