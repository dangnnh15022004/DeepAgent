"""Local proxy: receives request, prints it, forwards to real API.

Use this to capture EXACTLY what our tool sends vs what curl sends.
"""

import asyncio
import sys
from aiohttp import web
import httpx


async def proxy_handler(request: web.Request) -> web.Response:
    print(f"\n{'='*60}")
    print(f">>> Proxy received: {request.method} {request.path}")
    print(f"    Headers: {dict(request.headers)}")

    # Forward to the real API
    target_url = "https://deepsalesops-dev-api.deep.com.vn" + request.path_qs

    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        # Forward all relevant headers (drop hop-by-hop)
        fwd_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ('host', 'content-length')
        }
        body = await request.read()

        try:
            res = await client.request(
                method=request.method,
                url=target_url,
                headers=fwd_headers,
                content=body,
            )
            print(f"<<< Real API returned: {res.status_code}")
            print(f"    Body[:200]: {res.text[:200]}")
            return web.Response(
                text=res.text,
                status=res.status_code,
                headers={k: v for k, v in res.headers.items() if k.lower() != 'content-encoding'},
            )
        except Exception as e:
            print(f"!!! Exception: {e}")
            return web.Response(text=str(e), status=500)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8009
    app = web.Application()
    app.router.add_route('*', '/{path:.*}', proxy_handler)
    print(f"Proxy listening on http://localhost:{port}")
    web.run_app(app, port=port, access_log=None)


if __name__ == "__main__":
    main()
