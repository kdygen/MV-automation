"""Liveness and readiness endpoints.

``/health`` is a cheap liveness probe (process is up). ``/ready`` additionally checks
database connectivity, so orchestrators can distinguish "alive" from "able to serve".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}


@router.get("/ready")
def ready(db: Session = Depends(get_db)) -> dict[str, str]:
    """Readiness probe: verifies the database is reachable."""
    db.execute(text("SELECT 1"))
    return {"status": "ready"}
