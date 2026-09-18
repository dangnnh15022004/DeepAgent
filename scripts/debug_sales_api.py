"""Hit /v1/product/{id}/sale directly to inspect sale object schema."""

import asyncio, json
from src.agent.tools.common.api_utils import api_get

ACCESS_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkZWVwdHJhY2VfdXNlcklkIjoiYzNmZTMzZjgtOGI1NS00NmQyLWJmYjAtNWYwOGRmMGY5NjMyIiwiZGVlcHRyYWNlX2VtYWlsIjoidGVzdGN1c3RvbWVyQGdtYWlsLmNvbSIsImRlZXB0cmFjZV9yb2xlIjoiQ3VzdG9tZXIiLCJleHAiOjE3ODk0NzEyMzMsImlzcyI6IkRlZXBUcmFjZSIsImF1ZCI6IkRlZXBUcmFjZUF1ZGllbmNlIn0.VzOyLGVeASpgNC8jOM8FGAQEYl75uylV_P2bQ-5_CdU"
PRODUCT_ID = "f20e81ad-e588-435e-8072-ca06b6bc5841"  # Gạo Jasmine

async def main():
    raw = await api_get(f"/v1/product/{PRODUCT_ID}/sale", ACCESS_TOKEN,
                        params={"pageNumber": 1, "pageSize": 20})
    data = json.loads(raw)
    print("Top-level keys:", list(data.keys()) if isinstance(data, dict) else "LIST")
    items = data.get("userAddresses") if "userAddresses" in data else (data.get("items") or [])
    if not items and isinstance(data, dict):
        # Try other keys
        for k in ("sales", "salesReps", "data"):
            if k in data:
                items = data[k] if isinstance(data[k], list) else []
                print(f"Found list under: {k}")
                break
    print(f"Count: {len(items)}")
    if items:
        print(json.dumps(items[0], indent=2, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(main())
