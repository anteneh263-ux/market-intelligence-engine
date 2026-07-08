"""FastAPI entrypoint for the Market Intelligence Engine.

RESEARCH ONLY. This service studies market behaviour, generates and tests
hypotheses, and writes research reports. It does NOT execute trades and
requires no exchange API keys.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import DISCLAIMER, settings
from app.database.db import init_db
from app.routers import (
    backtests,
    dashboard,
    data,
    hypotheses,
    platform,
    reports,
    strategy_research,
)

app = FastAPI(
    title="Market Intelligence Engine",
    version=__version__,
    description="Research platform for studying crypto market behaviour. " + DISCLAIMER,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin, "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "version": __version__,
        "data_source": settings.data_source,
        "disclaimer": DISCLAIMER,
    }


app.include_router(data.router)
app.include_router(hypotheses.router)
app.include_router(backtests.router)
app.include_router(reports.router)
app.include_router(dashboard.router)
app.include_router(strategy_research.router)
app.include_router(platform.router)
