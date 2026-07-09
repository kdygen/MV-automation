"""Seed local-development data: a demo company (and optionally a second in review mode).

Usage (from backend/, venv active, after `alembic upgrade head`):

    python -m scripts.seed_dev

Idempotent: running twice won't duplicate companies.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.models import Company


def seed() -> None:
    db = get_sessionmaker()()
    try:
        for name, slug, settings in [
            ("Acme Movers", "acme-movers", {}),
            ("Careful Movers", "careful-movers", {"quote_review_mode": True}),
        ]:
            if db.scalar(select(Company).where(Company.slug == slug)) is None:
                db.add(Company(name=name, slug=slug, email=f"ops@{slug}.test", settings=settings))
                print(f"created company {slug}")
            else:
                print(f"company {slug} already exists")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed()
