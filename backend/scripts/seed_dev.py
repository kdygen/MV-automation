"""Seed local-development data: demo companies plus a realistic knowledge base.

Usage (from backend/, venv active, after `alembic upgrade head`):

    python -m scripts.seed_dev

Idempotent: running twice won't duplicate companies.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.models import Company, CompanyKnowledge

#: Synthetic company knowledge for local development and tests. Written the way a real
#: operator would answer a customer: specific, short, and safe to quote verbatim.
#:
#: ``keywords`` carries the words customers actually use that the title does not — the
#: reason simple keyword search is sufficient at this stage. Tests import this exact set
#: so the content shipped to developers is the content the search tests exercise.
#:
#: Local/dev only. Nothing here is ever inserted into production data.
SEED_KNOWLEDGE: tuple[dict[str, str], ...] = (
    {
        "category": "insurance",
        "title": "Certificate of Insurance",
        "content": (
            "We provide a certificate of insurance at no charge. Send us your "
            "building's requirements at least three business days before the move and "
            "we will issue the COI directly to your building management."
        ),
        "keywords": (
            "COI, certificate of insurance, building management, liability, "
            "proof of insurance"
        ),
    },
    {
        "category": "special_items",
        "title": "Pianos and oversized items",
        "content": (
            "We move upright and baby grand pianos with at least one week's notice so "
            "we can schedule the right crew and equipment. Piano moves carry an "
            "additional handling fee that is confirmed before your move date."
        ),
        "keywords": "piano, upright, grand, safe, gun safe, pool table, oversized, heavy item",
    },
    {
        "category": "packing",
        "title": "Packing materials and packing service",
        "content": (
            "Boxes, tape, and packing paper are available for an additional charge and "
            "are billed only for what you use. Full and partial packing services are "
            "quoted separately from your moving estimate."
        ),
        "keywords": "boxes, cartons, tape, bubble wrap, supplies, materials, packing included",
    },
    {
        "category": "policy",
        "title": "Cancellation and rescheduling",
        "content": (
            "You can cancel or reschedule at no cost up to 48 hours before your move. "
            "Inside 48 hours we charge a one-hour crew fee, which we credit toward your "
            "rescheduled date."
        ),
        "keywords": "cancel, cancellation, reschedule, change date, postpone, refund",
    },
    {
        "category": "policy",
        "title": "Stairs and elevators",
        "content": (
            "Stairs and elevators are already included in your hourly rate — there is "
            "no separate stair fee. If your building requires an elevator reservation, "
            "please book it yourself and tell us the reserved time window."
        ),
        "keywords": (
            "stairs, flights, walk up, elevator, lift, reserve elevator, "
            "freight elevator, extra fee"
        ),
    },
    {
        "category": "policy",
        "title": "If the move takes longer than estimated",
        "content": (
            "Your estimate is a range, not a cap. We bill for actual time at the same "
            "hourly rate, and the crew will tell you on the day if the job is trending "
            "past the estimate so there are no surprises."
        ),
        "keywords": "longer, overtime, over estimate, extra hours, go over, final price",
    },
    {
        "category": "service_area",
        "title": "Areas we serve",
        "content": (
            "We handle local moves across the metro area and surrounding suburbs, "
            "within roughly 100 miles. We do not currently take long-distance or "
            "cross-country moves."
        ),
        "keywords": "areas, service area, coverage, how far, long distance, out of state, region",
    },
    {
        "category": "payment",
        "title": "Payment and deposits",
        "content": (
            "No deposit is required to hold your date. We accept credit card, debit, "
            "and e-transfer, and payment is collected once the crew finishes."
        ),
        "keywords": "deposit, pay, payment, credit card, cash, e-transfer, invoice, tip, tipping",
    },
)


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

        # Knowledge is seeded for the demo company only: it is what the post-quote agent
        # searches during local testing. Keyed on (company, title) to stay idempotent.
        acme = db.scalar(select(Company).where(Company.slug == "acme-movers"))
        if acme is not None:
            existing = set(
                db.scalars(
                    select(CompanyKnowledge.title).where(
                        CompanyKnowledge.company_id == acme.id
                    )
                )
            )
            added = 0
            for entry in SEED_KNOWLEDGE:
                if entry["title"] in existing:
                    continue
                db.add(CompanyKnowledge(company_id=acme.id, **entry))
                added += 1
            db.commit()
            print(f"knowledge entries created: {added} (existing: {len(existing)})")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
