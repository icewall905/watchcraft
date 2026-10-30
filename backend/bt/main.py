"""FastAPI app: API under /api, SPA (static build) everywhere else."""
from contextlib import asynccontextmanager, suppress
import asyncio

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from .hub import api as hub_api, engine as hub_engine
from .api import agent as agent_api
from .api import core
from .api import proxy as proxy_api
from .api import video
from .api import ytimport
from .api import downloads
from .api import playback
from .api import speech
from .config import STATIC_DIR
from .db import init_db

@asynccontextmanager
async def lifespan(_app):
    init_db()
    task = asyncio.create_task(hub_engine.scheduler())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

app = FastAPI(title="WatchCraft", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)

app.add_middleware(auth.AuthMiddleware)
app.include_router(auth.router, prefix="/api")
app.include_router(core.router, prefix="/api")
app.include_router(hub_api.router, prefix="/api")
app.include_router(proxy_api.router, prefix="/api")
app.include_router(ytimport.router, prefix="/api")
app.include_router(video.router, prefix="/api")
app.include_router(downloads.router, prefix="/api")
app.include_router(playback.router, prefix="/api")
app.include_router(speech.router, prefix="/api")

app.include_router(agent_api.router, prefix="/api")

@app.get("/api/health")
def health():
    return {"ok": True}

if STATIC_DIR.exists():
    if (STATIC_DIR / "_app").exists():
        app.mount("/_app", StaticFiles(directory=STATIC_DIR / "_app"), name="app-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        f = (STATIC_DIR / path).resolve()
        if path and f.is_file() and STATIC_DIR.resolve() in f.parents:
            headers = {"Cache-Control": "no-cache"} if path in ("sw.js", "service-worker.js", "manifest.webmanifest") else {}
            return FileResponse(f, headers=headers)
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
