"""FastAPI application entry point."""

import logging
import re

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name, version="1.0.0")


@app.get("/")
def root_health_check() -> dict[str, str]:
    """Root-level health check for deployment platforms like Render."""
    return {"status": "ok", "service": settings.app_name}


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=r"https://configurable-document-extraction.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


class _QuietJobsPollFilter(logging.Filter):
    """Do not emit one access-log line per successful polling request."""
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            return not (args[1] == "GET" and str(args[2]).startswith("/api/jobs/") and str(args[4]) == "200")
        return not bool(re.search(r'"GET /api/jobs/[^ ]+ HTTP/[^ ]+" 200(?: |$)', record.getMessage()))


logging.getLogger("uvicorn.access").addFilter(_QuietJobsPollFilter())
