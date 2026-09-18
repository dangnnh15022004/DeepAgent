"""Replicate the exact sequence the chatbot does, sequentially, using the
SAME api_utils.py the MCP server uses, with a fresh httpx client for each call
(unlike the chatbot which might reuse connection pools).
"""

import asyncio
import json

from src.agent.tools.common.api_utils import api_get, api_post


TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkZWVwdHJhY2VfdXNlcklkIjoiYzNmZTMzZjgtOGI1NS00NmQyLWJmYjAtNWYwOGRmMGY5NjMyIiwiZGVlcHRyYWNlX2VtYWlsIjoidGVzdGN1c3RvbWVyQGdtYWlsLmNvbSIsImRlZXB0cmFjZV9yb2xlIjoiQ3VzdG9tZXIiLCJleHAiOjE3ODk0NzEyMzMsImlzcyI6IkRlZXBUcmFjZSIsImF1ZCI6IkRlZXBUcmFjZUF1ZGllbmNlIn0.VzOyLGVeASpgNC8jOM8FGAQEYl75uylV_P2bQ-5_CdU"


async def call(label, coro):
    res = await coro
    obj = json.loads(res)
    err = obj.get("error") if isinstance(obj, dict) else None
    status = obj.get("status") if isinstance(obj, dict) else None
    print(f"  {label:40s} → {'ERR ' + err if err else 'OK (' + str(len(json.dumps(obj))) + ' bytes)'}")

    # Also dump the raw auth header for the very first failure
    return obj


async def main():
    print("=== Replicating chatbot sequence ===\n")

    # 1. randomize
    r1 = await call("POST /v1/product/on-sale/randomize",
                    api_post("/v1/product/on-sale/randomize", TOKEN, {"seed": 0}))
    product_id = r1["items"][0]["productId"]
    print(f"  → product_id: {product_id}")

    # 2. get on-sale product detail
    r2 = await call("GET /v1/product/on-sale/{id}",
                    api_get(f"/v1/product/on-sale/{product_id}", TOKEN))

    # Parse variants
    variants = r2.get("onSaleProductVariantIds") or r2.get("variants") or []
    variant_id = variants[0] if variants else "9934240b-9a35-4476-b20c-fdb557160213"
    print(f"  → variant_id: {variant_id}")

    # 3. availability
    r3 = await call("GET /v1/warehouse/.../availability",
                    api_get(f"/v1/warehouse/on-sale-product-variant/{variant_id}/availability", TOKEN))

    # 4. user/address (THIS is the failing one)
    r4 = await call("GET /v1/user/address",
                    api_get("/v1/user/address", TOKEN))


asyncio.run(main())
