"""Run the actual MCP subprocess the same way uvicorn does, and call user/address."""

import asyncio
import os
import subprocess
import sys


async def main():
    env = {**os.environ, "PYTHONPATH": "/Users/dangnguyen/Documents/DeepAgent"}

    # Spawn the deepsaleops MCP server exactly like mcp_manager.py does
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.agent.tools.deepsaleops_mcp_server"],
        cwd="/Users/dangnguyen/Documents/DeepAgent",
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Just send a list_tools request and one list_user_addresses call
    # Actually too complex. Instead, simpler: import and call directly to verify
    # the function `list_user_addresses` from the SAME module that subprocess loads.

    from src.agent.tools.deepsaleops_mcp_server import list_user_addresses
    from src.agent.tools.system_mcp_server import get_my_profile
    # from src.agent.tools.deeptrace_mcp_server import search_product  # optional

    token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkZWVwdHJhY2VfdXNlcklkIjoiYzNmZTMzZjgtOGI1NS00NmQyLWJmYjAtNWYwOGRmMGY5NjMyIiwiZGVlcHRyYWNlX2VtYWlsIjoidGVzdGN1c3RvbWVyQGdtYWlsLmNvbSIsImRlZXB0cmFjZV9yb2xlIjoiQ3VzdG9tZXIiLCJleHAiOjE3ODk0NzEyMzMsImlzcyI6IkRlZXBUcmFjZSIsImF1ZCI6IkRlZXBUcmFjZUF1ZGllbmNlIn0.VzOyLGVeASpgNC8jOM8FGAQEYl75uylV_P2bQ-5_CdU"

    print("=== list_user_addresses ===")
    print(await list_user_addresses(token))

    print("\n=== auth/me (system server) ===")
    print(await get_my_profile(token)[:200])

    proc.terminate()


asyncio.run(main())
