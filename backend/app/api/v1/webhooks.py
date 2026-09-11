"""Provider webhooks. Unauthenticated by necessity, trusted only after verification.

This endpoint is the **only** authority on whether a payment succeeded. It is reachable
by anyone, so nothing in the request is believed until the provider's signature over
the exact raw bytes verifies. The raw body is read before any parsing precisely because
re-serializing JSON would change those bytes and break the signature.

The response is deliberately uninformative. A provider only needs 2xx to stop retrying,
and a caller probing this endpoint learns nothing about which sessions or quotes exist.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from app.api.deps import AnyPaymentProvider, payment_provider_dep
from app.core.config import Settings, get_settings
from app.core.errors import AuthError
from app.core.logging import get_logger
from app.core.ratelimit import rate_limited
from app.db.session import get_db
from app.providers.payment import PaymentVerificationError
from app.services import payments as payment_service

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

#: Generous, because legitimate retry storms are normal, but not unbounded.
_limit_webhook = rate_limited("stripe_webhook", max_requests=300, window_seconds=60)

#: Bounds what an unauthenticated caller can make us buffer and hash.
MAX_WEBHOOK_BYTES = 256 * 1024


@router.post("/stripe", dependencies=[Depends(_limit_webhook)])
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(default="", alias="Stripe-Signature"),
    db: Session = Depends(get_db),
    provider: AnyPaymentProvider = Depends(payment_provider_dep),
    settings: Settings = Depends(get_settings),
) -> dict[str, bool]:
    """Verify, then apply, one provider event.

    A failed signature is a 401 and nothing else happens — no lookup, no write, no hint
    in the response about whether the referenced session exists.
    """
    payload = await request.body()
    if len(payload) > MAX_WEBHOOK_BYTES:
        raise AuthError("Payload too large")

    try:
        event = provider.parse_event(payload=payload, signature=stripe_signature)
    except PaymentVerificationError:
        # Logged without the body: an unverified payload is attacker-controlled.
        logger.warning("Rejected webhook with an invalid signature")
        raise AuthError("Invalid webhook signature") from None

    payment_service.handle_event(db, event, provider_name=settings.payment_provider.lower())
    # Flat acknowledgement: never reveal whether the event matched anything of ours.
    return {"received": True}
