"""Link a Supabase Auth user to a company as a staff member.

After creating a user in Supabase Auth (Dashboard → Authentication → Users), the
platform still needs a ``users`` row mapping that auth identity to a company and role —
without it, a valid login gets 403 "not provisioned". This script creates (or updates)
that row.

Usage (from backend/, venv active):

    python -m scripts.provision_staff <auth-user-uuid> <company-slug> <email> \
        [--role owner|admin|staff] [--name "Full Name"]

The auth-user-uuid is the "UID" column in Supabase's Authentication → Users table.
Idempotent: re-running updates the existing row's company/role/name.
"""

from __future__ import annotations

import argparse
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_sessionmaker
from app.models import Company, User, UserRole


def provision_staff(
    db: Session,
    *,
    auth_user_id: uuid.UUID,
    company_slug: str,
    email: str,
    role: UserRole = UserRole.OWNER,
    full_name: str | None = None,
) -> tuple[User, bool]:
    """Create or update the ``users`` row for a Supabase auth identity.

    :returns: ``(user, created)`` — ``created`` is False when an existing row was updated.
    :raises LookupError: if the company slug doesn't exist.
    """
    company = db.scalar(select(Company).where(Company.slug == company_slug))
    if company is None:
        raise LookupError(f"Company '{company_slug}' not found")

    user = db.get(User, auth_user_id)
    created = user is None
    if user is None:
        user = User(
            id=auth_user_id,
            company_id=company.id,
            email=email,
            full_name=full_name,
            role=role,
        )
        db.add(user)
    else:
        user.company_id = company.id
        user.email = email
        user.role = role
        if full_name is not None:
            user.full_name = full_name
    db.commit()
    return user, created


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("auth_user_id", help="Supabase Auth user UUID (the UID column)")
    parser.add_argument("company_slug", help="Company slug, e.g. acme-movers")
    parser.add_argument("email", help="The staff member's email")
    parser.add_argument(
        "--role", choices=[r.value for r in UserRole], default=UserRole.OWNER.value
    )
    parser.add_argument("--name", default=None, help="Full name (optional)")
    args = parser.parse_args()

    try:
        auth_user_id = uuid.UUID(args.auth_user_id)
    except ValueError:
        sys.exit(f"'{args.auth_user_id}' is not a valid UUID")

    db = get_sessionmaker()()
    try:
        user, created = provision_staff(
            db,
            auth_user_id=auth_user_id,
            company_slug=args.company_slug,
            email=args.email,
            role=UserRole(args.role),
            full_name=args.name,
        )
        action = "Provisioned" if created else "Updated"
        print(f"{action} {user.email} as {user.role.value} of '{args.company_slug}'")
        print("They can now sign in to the dashboard with their Supabase credentials.")
    except LookupError as exc:
        sys.exit(str(exc))
    finally:
        db.close()


if __name__ == "__main__":
    main()
