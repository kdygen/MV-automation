"""Aggregate router for the v1 API.

Each domain (intake, quotes, bookings, jobs, settings, public funnel) contributes its own
``APIRouter``; they are included here and mounted under the ``/api/v1`` prefix by the app
factory. Domain routers are added as their milestones land.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, get_current_user
from app.api.v1.bookings import router as bookings_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.leads import router as leads_router
from app.api.v1.public import router as public_router
from app.api.v1.quotes import router as quotes_router
from app.api.v1.settings import router as settings_router

api_router = APIRouter()
api_router.include_router(public_router)
api_router.include_router(quotes_router)
api_router.include_router(leads_router)
api_router.include_router(bookings_router)
api_router.include_router(jobs_router)
api_router.include_router(settings_router)


@api_router.get("/me", tags=["auth"])
def read_me(user: CurrentUser = Depends(get_current_user)) -> dict[str, str]:
    """Return the authenticated user's identity and tenant — a smoke test for auth."""
    return {
        "id": str(user.id),
        "company_id": str(user.company_id),
        "email": user.email,
        "role": user.role.value,
    }
