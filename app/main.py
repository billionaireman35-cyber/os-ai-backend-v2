import app.patch  # Apply ForwardRef patch before any other imports

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.router import api_router
from app.core.database import init_db
from app.core.config import settings

app = FastAPI(
    title="OS AI API",
    version="2.0.0",
    description="The Operating System for Intelligence",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup():
    init_db()

app.include_router(api_router, prefix="/api")

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": app.version,
        "environment": settings.ENVIRONMENT,
        "chains": settings.SUPPORTED_CHAINS,
    }
