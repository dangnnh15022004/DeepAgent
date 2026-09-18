from contextlib import asynccontextmanager
import os
from pathlib import Path
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

# Load .env BEFORE settings is constructed (so POSTGRES_DSN etc. are read).
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=False)
except ImportError:
    pass  # python-dotenv not installed; env must be set externally

from src.config.settings import settings
from src.api.routes.chat import router as chat_router
from src.api.auth.routes import router as auth_router
from src.agent.tools.mcp_manager import mcp_manager
from src.api.db import postgres as db


def _configure_langchain_tracing():
    """Set LangSmith tracing từ settings vào os.environ tại startup."""
    if settings.langchain_api_key:
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key
        if settings.langchain_project:
            os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_langchain_tracing()
    await mcp_manager.start()
    await db.init_pool()  # best-effort; logs warning if POSTGRES_DSN unset
    yield
    await db.close_pool()
    await mcp_manager.stop()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )
    schema.setdefault("components", {})["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Enter your JWT token (without 'Bearer ' prefix).",
        }
    }
    schema["security"] = [{"BearerAuth": []}]
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Image proxy: bypasses CORS and presigned-URL expiry ─────────────────────
# Streamlit cannot fetch AWS presigned URLs directly from the browser due to CORS
# and 30-minute expiry. This endpoint fetches the image server-side and streams
# it back with correct Content-Type so st.image() can render it.
@app.get("/api/v1/proxy-image", tags=["utility"])
async def proxy_image(url: str):
    """Fetch an image from a URL server-side and stream it back to the client."""
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Upstream returned {resp.status_code}")
        content_type = resp.headers.get("content-type", "image/jpeg")
        return Response(content=resp.content, media_type=content_type)
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Image fetch timed out")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch image: {e}")

# ── Redirect root to Streamlit UI (HTML UI deprecated) ─────────────────────
from fastapi.responses import RedirectResponse


@app.get("/", tags=["pages"], include_in_schema=False)
async def root():
    """Root → redirect to Streamlit UI at port 8501."""
    return RedirectResponse(url="http://localhost:8501/")


@app.get("/login", tags=["pages"], include_in_schema=False)
async def login_page():
    return RedirectResponse(url="http://localhost:8501/")


@app.get("/chat", tags=["pages"], include_in_schema=False)
async def chat_page():
    return RedirectResponse(url="http://localhost:8501/")


app.include_router(chat_router)
app.include_router(auth_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        server_header=False,
        date_header=False,
    )
