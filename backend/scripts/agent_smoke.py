"""Manual check: drive the agent against the real model.

Uses a **local in-memory fixture**, never production data — no Supabase connection is
opened and nothing is written to any real database. Requires ``OPENAI_API_KEY``.

Usage (from backend/, venv active):

    python -m scripts.agent_smoke

Each question is a fresh conversation, so tool selection is judged independently:
the model should pick get_quote_summary / get_move_details / get_company_info on its
own from the question alone.
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent import build_system_prompt, run_agent_turn
from app.core.config import get_settings
from app.db.base import Base
from app.models import Company, Lead, MovingRequest, PricingConfigRow, Quote, QuoteStatus
from app.models.moving_request import HomeSize, PackingService
from app.pricing import PricingConfig
from app.providers.llm import LLMConfigurationError, get_chat_model
from app.services import agent as agent_service

QUESTIONS = [
    "How much is my quote?",
    "what's this gonna cost me?",
    "how many movers are coming?",
    "what day am I moving?",
    "how do I contact you?",
    "Can you change my move to next Friday?",  # must refuse: read-only
]


def build_fixture() -> tuple[Any, Quote]:
    """A throwaway in-memory database with one company, move, and quote."""
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    company = Company(
        name="Springfield Movers",
        slug="springfield-movers",
        phone="555-0142",
        email="hello@springfieldmovers.test",
        settings={"quote_validity_days": 14},
    )
    db.add(company)
    db.commit()

    lead = Lead(company_id=company.id, name="Dana", email="dana@example.test")
    db.add(lead)
    db.flush()

    request = MovingRequest(
        company_id=company.id,
        lead_id=lead.id,
        origin_line1="12 Elm St",
        origin_city="Springfield",
        origin_state="IL",
        origin_zip="62701",
        origin_floor=3,
        origin_stairs_flights=2,
        destination_line1="99 Oak Ave",
        destination_city="Chatham",
        destination_state="IL",
        destination_zip="62629",
        move_date=date.today() + timedelta(days=21),
        home_size=HomeSize.TWO_BR,
        packing_service=PackingService.PARTIAL,
        special_items=["piano"],
        distance_miles=13.1,
        raw_payload={},
    )
    config = PricingConfigRow(
        company_id=company.id,
        version=1,
        is_active=True,
        config=PricingConfig().model_dump(mode="json"),
    )
    db.add_all([request, config])
    db.flush()

    quote = Quote(
        company_id=company.id,
        moving_request_id=request.id,
        pricing_config_id=config.id,
        status=QuoteStatus.SENT,
        amount_min_cents=173_400,
        amount_max_cents=220_600,
        total_cents=197_000,
        estimated_hours=10.0,
        crew_size=3,
        engine_version="rules-v1.0",
        line_items=[
            {"code": "labor", "label": "Moving labor — 3 movers × 10.0h",
             "amount_cents": 190_000, "meta": {"hourly_rate": 190.0}},
            {"code": "travel", "label": "Travel fee", "amount_cents": 7_620,
             "meta": {"distance_miles": 13.1}},
        ],
        inputs_snapshot={"internal": "should never be shown"},
        public_token=uuid.uuid4().hex,
        valid_until=datetime.now(UTC) + timedelta(days=14),
    )
    db.add(quote)
    db.commit()
    return db, quote


def main() -> None:
    settings = get_settings()
    try:
        model = get_chat_model(settings)
    except LLMConfigurationError as exc:
        sys.exit(
            f"{exc}\n\n"
            "Add OPENAI_API_KEY to backend/.env and re-run:\n"
            "    python -m scripts.agent_smoke"
        )

    print(
        f"model={settings.agent_model} "
        f"reasoning_effort={settings.agent_reasoning_effort}\n"
    )
    db, quote = build_fixture()
    prompt = build_system_prompt()

    for question in QUESTIONS:
        # Fresh conversation per question so tool choice is judged independently.
        db.query(type(quote)).filter_by(id=quote.id).update({"public_token": uuid.uuid4().hex})
        db.commit()
        db.refresh(quote)
        context = agent_service.build_agent_context(db, quote.public_token)

        print(f"CUSTOMER: {question}")
        try:
            result = run_agent_turn(db, context, question, model, system_prompt=prompt)
        except Exception as exc:  # noqa: BLE001 - manual script: surface anything
            print(f"  ERROR: {type(exc).__name__}: {exc}\n")
            continue
        print(f"  tools : {result.tools_used or '(none)'}")
        print(f"  tokens: in={result.usage.input_tokens} out={result.usage.output_tokens}")
        print(f"  REPLY : {result.reply}\n")


if __name__ == "__main__":
    main()
