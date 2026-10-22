from __future__ import annotations

import logging
import threading

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import settings
from .db import SessionLocal, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("plumb")

app = FastAPI(title="Plumb — Brand CI", version="0.2.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return resp


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):  # never leak stack traces to the browser
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse({"detail": "Something went wrong on our side. Please retry."}, status_code=500)


def _warmup() -> None:
    from .engine import ml
    from .seed import seed_examples

    ml.load()
    if settings.seed_on_start and settings.seed_examples:
        seed_examples()


@app.on_event("startup")
def startup() -> None:
    init_db()
    if settings.seed_on_start:
        from .seed import seed_base

        db = SessionLocal()
        try:
            seed_base(db)
        finally:
            db.close()
    threading.Thread(target=_warmup, daemon=True).start()


# ---- single-page app (built React frontend) ------------------------------------------
_dist = settings.static_dir
if (_dist / "assets").exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    f = (_dist / path).resolve()
    if path and f.is_file() and _dist.resolve() in f.parents:
        return FileResponse(f)
    index = _dist / "index.html"
    if index.exists():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"detail": "Frontend not built. Run `npm run build` in frontend/."}, status_code=404)
