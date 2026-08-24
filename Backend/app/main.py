from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.1.0")


@app.get("/")
def root_health_check() -> dict[str, str]:
    """Root-level health check for deployment platforms like Render."""
    return {"status": "ok", "service": "Configurable Document Extraction API"}


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # Vercel generates a unique hash-based preview URL on every push
    # (e.g. ...-ltjrc0rwh-...), so exact-match origins cannot cover previews.
    allow_origin_regex=r"https://configurable-document-extraction.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
