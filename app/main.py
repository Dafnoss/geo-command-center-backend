"""
FastAPI application entry.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.seed import init_db
from app.routers import (
    prompts,
    ai_results,
    sources,
    urls,
    recommendations,
    tasks,
    dashboard,
    settings as settings_router,
    monitor as monitor_router,
)


app = FastAPI(
    title="GEO Command Center API",
    version="0.1.0",
    description="Backend for the GEO/SEO intelligence dashboard (OCSiAl / TUBALL).",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_origin_regex=r".*",  # permissive for local file:// and dev hosts
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}


# Routers
app.include_router(dashboard.router)
app.include_router(prompts.router)
app.include_router(ai_results.router)
app.include_router(sources.router)
app.include_router(urls.router)
app.include_router(recommendations.router)
app.include_router(tasks.router)
app.include_router(settings_router.router)
app.include_router(monitor_router.router)
