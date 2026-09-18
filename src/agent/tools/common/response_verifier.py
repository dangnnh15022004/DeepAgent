"""Shared verification helpers for MCP tool responses.

Mirror of 99g-agent's `response_verifier.py`. Prevents the LLM from reporting
"create order OK" when the upstream API actually returned HTTP 200 but with
`data: null` / silent payload mismatch.

Usage:
    raw = await api_post("/api/v1/order/create-for-customer", access_token, payload)
    return verify_write_response(
        raw,
        expected={"orderId": None, "status": "CREATED"},
        operation="create_order",
    )
"""

import json
from typing import Any


def _lookup(obj: Any, path: str) -> Any:
    """Walk a dotted path inside a nested dict. Returns None if any segment is missing."""
    if obj is None:
        return None
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def verify_write_response(
    raw: str,
    expected: dict[str, Any] | None = None,
    operation: str = "write",
) -> str:
    """Verify that a write API response reflects the intended change.

    Behavior:
    - If `raw` is not JSON or has no top-level payload, return raw unchanged.
    - If `expected` is None, only check that `data` (or the root payload) is non-null.
    - For each `expected[k]`:
        * expected_value is None  → the field must be present and not null/empty.
        * otherwise                → the field must equal expected_value (string compare).
    - On verification failure, return a JSON warning that the LLM is forced to surface.

    Note: DeepSaleOps responses don't wrap under a `data` key (it returns the
    payload directly), so we also accept the root dict as the verification target.
    """
    if expected is None:
        expected = {}

    try:
        parsed = json.loads(raw)
    except Exception:
        return raw

    if not isinstance(parsed, dict):
        return raw

    if parsed.get("error") or parsed.get("errors"):
        return raw

    # DeepSaleOps returns the order object directly (no `data` wrapper).
    # If there's no `data` field, fall back to the root payload.
    target = parsed.get("data") if "data" in parsed else parsed

    if target is None:
        return json.dumps(
            {
                "warning": "WRITE_UNVERIFIED",
                "operation": operation,
                "reason": "API returned 200 but the payload is null/missing.",
                "response": parsed,
            },
            ensure_ascii=False,
        )

    failures = []
    for field_path, expected_value in expected.items():
        actual = _lookup(target, field_path)
        if expected_value is None:
            if actual is None or actual == "":
                failures.append(
                    {"field": field_path, "expected": "non-null", "actual": actual}
                )
        else:
            if actual is None or str(actual) != str(expected_value):
                failures.append(
                    {"field": field_path, "expected": expected_value, "actual": actual}
                )

    if failures:
        return json.dumps(
            {
                "warning": "WRITE_UNVERIFIED",
                "operation": operation,
                "reason": "API returned 200 but the expected updated fields do not match.",
                "expected": expected,
                "mismatches": failures,
                "response": parsed,
            },
            ensure_ascii=False,
        )

    return raw
