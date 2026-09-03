import json
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Deeptrace_Tools_Server")
BASE_URL = "https://dev-api-deeptrace.deepprotech.com"

@mcp.tool()
async def search_company(access_token: str, company_name: str = "", contact_email: str = "", company_id: str = "", tax_code: str = "") -> str:
    """
    Search for a company profile using name, email, ID, or taxCode.
    Use this tool to find basic company details and the exact 'companyId'.
    """
    if not any([company_name, contact_email, company_id, tax_code]):
        return "Error: Missing search criteria."

    payload = {k: v for k, v in {
        "companyName": company_name,
        "contactEmail": contact_email,
        "companyId": company_id,
        "taxCode": tax_code
    }.items() if v}

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(f"{BASE_URL}/api/v1/company/search", json=payload, headers={"Authorization": f"Bearer {access_token}"})
            
        if res.status_code != 200:
            return f"API Error: {res.status_code}"
            
        data = res.json().get("items", [])
        if not data:
            return "Company not found."
        if len(data) > 1:
            return "Multiple companies found. Ask the user for more specific details."
            
        company = data[0]
        clean_data = {
            "companyId": company.get("companyId"),
            "companyName": company.get("companyName"),
            "contactEmail": company.get("contactEmail"),
            "taxCode": company.get("taxCode"),
            "address": company.get("address")
        }
        return json.dumps(clean_data, ensure_ascii=False)
    except Exception:
        return "Connection error."

@mcp.tool()
async def summarize_company_products(company_id: str, access_token: str) -> str:
    """
    Get the total product count and categories for a specific company.
    Requires 'company_id' from search_company tool.
    """
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(f"{BASE_URL}/api/v1/company/list-product/{company_id}", headers={"Authorization": f"Bearer {access_token}"})
        
        if res.status_code != 200:
            return f"API Error: {res.status_code}"
            
        data = res.json()
        categories = {item.get("category").strip().capitalize() for item in data.get("items", []) if item.get("category")}
        clean_categories = [c for c in categories if "test" not in c.lower()]
        
        result = {
            "totalProducts": data.get("totalCount", 0),
            "categories": clean_categories
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        return "Connection error."

@mcp.tool()
async def search_product(product_name: str, access_token: str) -> str:
    """Search products by name. Also serves as the primary detail lookup.

    Use when:
    - The user asks about a specific product by name (e.g. "thông tin sản phẩm X", "GTIN của Y", "thành phần sản phẩm Z").
    - The user wants to inspect full product details — this endpoint returns the same complete record as the detail endpoint, so no follow-up call is needed.
    - You need to resolve a product name into the system 'productId' (required by get_product_batches, etc.).

    CRITICAL RULES:
    - Both 'access_token' and 'product_name' are strictly REQUIRED.
    - DO NOT guess, fabricate, or auto-derive 'product_name'. If the user has not provided it, ask them.
    - This tool returns the full product schema — DO NOT chain another "detail" call after a successful search. Stop here and answer.
    - When the tool succeeds, you MUST surface every field the API returned to the user (except image URLs): productName, gtin, gln, description, countryOfOrigin, ingredientOrigin, mainIngredients (full list), manufacturerName, manufacturingAddress, packagingType, category, netContent, dynamicFieldSchema (each fieldName + fieldValue), created, updated. Keep technical codes (GTIN, GLN, ingredient names, brand names) exactly as-is; translate only labels and free-text.

    Returns JSON array of full product records. Each item contains:
    - productId, productName, gtin, gln
    - description, countryOfOrigin, ingredientOrigin
    - mainIngredients (array of strings)
    - manufacturerName, manufacturingAddress
    - packagingType, category, netContent
    - verifyByProductId (int)
    - dynamicFieldSchema (array of {fieldName, fieldValue})
    - productImageUrl, packagingImageUrl, brandLogoUrl (S3 signed URLs — render as Markdown images only when the user asks for the image; never paste raw URLs as plain text)
    - subCompanyInfo (array — usually empty; ignore unless non-empty and relevant)
    - created, updated (ISO timestamps)

    Args:
        access_token: JWT token from DeepTrace.
        product_name: Free-text product name from the user (e.g. "Cà phê Arabica 500g").
    """
    payload = {"productName": product_name, "pageNumber": 1, "pageSize": 10}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(f"{BASE_URL}/api/v1/product/search", json=payload, headers={"Authorization": f"Bearer {access_token}"})

        if res.status_code != 200:
            return f"API Error: {res.status_code}"

        items = res.json().get("items", [])
        if not items:
            return "Product not found."

        # Return the full record (minus the noisy empty subCompanyInfo bucket)
        # so search_product doubles as the primary product-detail lookup —
        # no extra detail call needed.
        result = [
            {k: v for k, v in i.items() if k != "subCompanyInfo"}
            for i in items
        ]
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        return "Connection error."

@mcp.tool()
async def get_product_batches(product_id: str, access_token: str) -> str:
    """
    List all manufacturing batches of a specific product.
    Returns 'batchId', stage, expected/actual quantities, and expiration dates.
    Requires 'product_id' from search_product tool.
    """
    params = {"pageNumber": 1, "pageSize": 10, "sortBy": "desc"}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(f"{BASE_URL}/api/v1/product/list-batch/{product_id}", params=params, headers={"Authorization": f"Bearer {access_token}"})
            
        if res.status_code != 200:
            return f"API Error: {res.status_code}"
            
        items = res.json().get("items", [])
        if not items:
            return "No batches found for this product."
            
        result = [
            {
                "batchId": i.get("batchId"),
                "batchName": i.get("batchName"),
                "stage": i.get("stage"),
                "manufacturedDate": i.get("manufacturedDate"),
                "expiredDate": i.get("expiredDate")
            }
            for i in items
        ]
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        return "Connection error."

@mcp.tool()
async def get_batch_manufacturing_log(batch_id: str, access_token: str) -> str:
    """
    Fetch the full manufacturing log for a single batch — every step from raw
    material intake to finished goods warehousing, with timestamps, location,
    responsible person, and S3 image proofs.

    Requires 'batchId' from get_product_batches. Typical chain:
        search_product → get_product_batches → get_batch_manufacturing_log

    Use when the user asks for 'chi tiết lô hàng', 'quy trình sản xuất',
    'nhật ký sản xuất', or wants to see the step-by-step history of a batch.
    """
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{BASE_URL}/api/v1/batch/authorized/{batch_id}/manufacturing-log",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if res.status_code == 404:
            return "Manufacturing log not found for this batch."
        if res.status_code != 200:
            return f"API Error: {res.status_code}"

        return json.dumps(res.json(), ensure_ascii=False)
    except Exception:
        return "Connection error."


@mcp.tool()
async def get_my_profile(access_token: str) -> str:
    """
    Get the authenticated user's profile: email, role, and companyList.

    Use FIRST when the user refers to "my products", "my company", "my batches",
    or when the answer depends on identity/role.

    Returns 'companyId' for each company — use it directly as input to other tools
    (e.g. summarize_company_products, search_company). 'primaryCompany' is the
    first active company, ready to pass forward without asking the user.
    """
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{BASE_URL}/api/v1/auth/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if res.status_code == 401:
            return "Authentication required: the access token is invalid or expired."
        if res.status_code != 200:
            return f"API Error: {res.status_code}"

        data = res.json()

        company_list = [
            {
                "companyId": c.get("companyId"),
                "companyName": c.get("companyName"),
                "companyAddress": c.get("companyAddress"),
                "isActive": c.get("isActive"),
            }
            for c in data.get("companyList", [])
        ]
        active_companies = [c for c in company_list if c.get("isActive") == 1]
        primary_company = active_companies[0] if active_companies else (
            company_list[0] if company_list else None
        )

        result = {
            "userId": data.get("userId") or data.get("user_id"),
            "email": data.get("email"),
            "role": data.get("role"),
            "companyList": company_list,
            "primaryCompany": primary_company,
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        return "Connection error."

if __name__ == "__main__":
    mcp.run(transport='stdio')