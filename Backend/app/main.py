from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from Backend.app.api.routes import router
from Backend.app.core.config import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.on_event("startup")
def startup_event() -> None:
    _ = settings
