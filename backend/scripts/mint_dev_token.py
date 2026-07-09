"""Mint a local-development dashboard token (and the owner user it belongs to).

For local dev without a Supabase project: set any SUPABASE_JWT_SECRET in backend/.env,
run this script, and paste the printed token into the dashboard's dev login. In
production, tokens come from Supabase Auth and this script is never used.

Usage (from backend/, venv active, DB migrated + seeded):

    python -m scripts.mint_dev_token [company-slug]
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.models import Company, User, UserRole


def main(slug: str = "acme-movers") -> None:
    settings = get_settings()
    if not settings.supabase_jwt_secret:
        sys.exit("Set SUPABASE_JWT_SECRET in backend/.env first (any value works locally).")

    db = get_sessionmaker()()
    try:
        company = db.scalar(select(Company).where(Company.slug == slug))
        if company is None:
            sys.exit(f"Company '{slug}' not found — run `python -m scripts.seed_dev` first.")

        email = f"dev-owner@{slug}.test"
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(
                id=uuid.uuid4(),
                company_id=company.id,
                email=email,
                full_name="Dev Owner",
                role=UserRole.OWNER,
            )
            db.add(user)
            db.commit()

        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": str(user.id),
                "aud": settings.supabase_jwt_audience,
                "iat": now,
                "exp": now + timedelta(days=7),
                "role": "authenticated",
            },
            settings.supabase_jwt_secret,
            algorithm="HS256",
        )
        print(f"Owner token for {company.name} ({email}), valid 7 days:\n\n{token}")
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "acme-movers")
