from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _ = settings
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


@app.get("/")
def root_health_check() -> dict[str, str]:
    """Root-level health check for deployment platforms like Render."""
    return {"status": "ok", "service": "Configurable Document Extraction API"}


# Build a robust origins list combining config variables and hardcoded fallbacks
origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

# Append whatever is configured in your settings safely
if isinstance(settings.frontend_origin_list, list):
    origins.extend(settings.frontend_origin_list)
elif isinstance(settings.frontend_origin_list, str):
    # Fallback if your config parses a comma-separated string
    origins.extend([o.strip() for f in settings.frontend_origin_list.split(",")])

# ⚠️ MANUALLY ADD YOUR EXACT VERCEL PRODUCTION DOMAINS BELOW ⚠️
# Replace these strings with your active .vercel.app domain
origins.extend([
    "https://configurable-document-extraction.vercel.app", 
    "https://configurable-document-extraction-git-makefrontend-peter-o-manufactor.vercel.app"
])

# Remove duplicates while preserving order
origins = list(dict.fromkeys(origins))

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # Uses the comprehensive verified list
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# If your frontend hits /api/... explicitly, ensure your prefix is included here:
app.include_router(router, prefix="/api")