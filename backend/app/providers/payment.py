"""Payment provider interface: hosted checkout, plus an offline fake.

Same shape as the distance, email and LLM providers — one thin adapter around the
vendor SDK, and a fake that lets the entire suite run with no key, no network, and no
money. This module is the only place ``stripe`` is imported.

Two rules the interface enforces by shape:

* **Amounts are passed in, never negotiated.** A provider is told how many cents to
  collect; it has no access to a quote and cannot derive a price.
* **Nothing here decides that a payment succeeded.** A provider creates a session and
  verifies a signature. Whether money arrived is decided by
  :mod:`app.services.payments` acting on a verified webhook event.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class CheckoutSession:
    """A hosted checkout the customer is redirected to."""

    session_id: str
    url: str


@dataclass(frozen=True)
class PaymentEvent:
    """A verified provider event, normalized.

    :param event_id: the provider's own id — the idempotency key for webhook delivery.
    :param kind: ``"succeeded"``, ``"failed"``, ``"expired"``, or ``"ignored"``.
    """

    event_id: str
    kind: str
    session_id: str | None
    payment_intent_id: str | None = None


class PaymentProvider(Protocol):
    """Everything the payment service needs from a checkout provider."""

    def create_checkout(
        self,
        *,
        amount_cents: int,
        currency: str,
        description: str,
        success_url: str,
        cancel_url: str,
        reference: str,
    ) -> CheckoutSession: ...

    def parse_event(self, *, payload: bytes, signature: str) -> PaymentEvent: ...


class PaymentVerificationError(RuntimeError):
    """The event could not be verified as genuinely from the provider."""


class PaymentConfigurationError(RuntimeError):
    """A configured payment provider is unknown or missing its credentials."""


class FakePaymentProvider:
    """Offline provider used by tests and local development.

    Produces deterministic session ids and verifies webhooks with an HMAC over the body
    using the configured secret — the same *shape* of check Stripe performs, so the
    service's verification path is genuinely exercised offline rather than stubbed out.

    The checkout URL is the caller's own ``success_url``, which stands in for "the
    customer finished paying": the browser lands on the return page exactly as it would
    from Stripe, and the booking still waits on a signed webhook. A fake that redirected
    somewhere unreachable could not exercise the return flow at all.
    """

    def __init__(self, *, secret: str = "fake-webhook-secret") -> None:
        self._secret = secret
        self.sessions: list[CheckoutSession] = []

    def create_checkout(
        self,
        *,
        amount_cents: int,
        currency: str,
        description: str,
        success_url: str,
        cancel_url: str,
        reference: str,
    ) -> CheckoutSession:
        session = CheckoutSession(
            session_id=f"cs_fake_{uuid.uuid4().hex[:24]}",
            url=success_url,
        )
        self.sessions.append(session)
        logger.info(
            "FakePayment checkout amount=%d %s ref=%s", amount_cents, currency, reference
        )
        return session

    def sign(self, payload: bytes) -> str:
        """Test helper: the signature this provider would accept for ``payload``."""
        return hmac.new(self._secret.encode(), payload, hashlib.sha256).hexdigest()

    def parse_event(self, *, payload: bytes, signature: str) -> PaymentEvent:
        if not hmac.compare_digest(self.sign(payload), signature or ""):
            raise PaymentVerificationError("Signature mismatch")
        body = json.loads(payload)
        return PaymentEvent(
            event_id=str(body["id"]),
            kind=str(body.get("kind", "ignored")),
            session_id=body.get("session_id"),
            payment_intent_id=body.get("payment_intent_id"),
        )


#: Stripe event types we act on. Anything else verifies fine and is then ignored —
#: an account emits dozens of event types and reacting to an unmodelled one is how
#: bookings get confirmed by accident.
_SUCCESS_EVENTS = frozenset(
    {"checkout.session.completed", "checkout.session.async_payment_succeeded"}
)
_FAILURE_EVENTS = frozenset({"checkout.session.async_payment_failed"})
_EXPIRY_EVENTS = frozenset({"checkout.session.expired"})


class StripePaymentProvider:
    """Stripe hosted Checkout. The only place the Stripe SDK is touched."""

    def __init__(self, *, secret_key: str, webhook_secret: str) -> None:
        import stripe

        # A per-instance client rather than the module-global ``stripe.api_key``: the
        # global is process-wide state that any other import could overwrite, and the
        # SDK's own guidance is to construct a client. The ``v1`` namespace is the
        # current path — the bare ``client.checkout`` alias is deprecated.
        #
        # Typed loosely on purpose: this adapter is the boundary where the vendor's
        # shapes stop, exactly as in the LLM adapter.
        self._client: Any = stripe.StripeClient(api_key=secret_key)
        self._webhook: Any = stripe.Webhook
        self._webhook_secret = webhook_secret

    def create_checkout(
        self,
        *,
        amount_cents: int,
        currency: str,
        description: str,
        success_url: str,
        cancel_url: str,
        reference: str,
    ) -> CheckoutSession:
        session = self._client.v1.checkout.sessions.create(
            {
                "mode": "payment",
                "line_items": [
                    {
                        "quantity": 1,
                        "price_data": {
                            "currency": currency.lower(),
                            "unit_amount": amount_cents,
                            "product_data": {"name": description},
                        },
                    }
                ],
                "success_url": success_url,
                "cancel_url": cancel_url,
                "client_reference_id": reference,
            }
        )
        return CheckoutSession(session_id=str(session.id), url=str(session.url))

    def parse_event(self, *, payload: bytes, signature: str) -> PaymentEvent:
        try:
            event = self._webhook.construct_event(payload, signature, self._webhook_secret)
        except Exception as exc:  # SDK raises its own signature/parse errors
            raise PaymentVerificationError(str(exc)) from exc

        event_type = str(event["type"])
        obj = event["data"]["object"]
        kind = "ignored"
        if event_type in _SUCCESS_EVENTS:
            # `completed` fires for unpaid sessions too (e.g. delayed methods), so the
            # payment status is what decides, not the event name.
            kind = "succeeded" if obj.get("payment_status") == "paid" else "ignored"
        elif event_type in _FAILURE_EVENTS:
            kind = "failed"
        elif event_type in _EXPIRY_EVENTS:
            kind = "expired"

        return PaymentEvent(
            event_id=str(event["id"]),
            kind=kind,
            session_id=obj.get("id"),
            payment_intent_id=obj.get("payment_intent"),
        )


def get_payment_provider(settings: Settings) -> FakePaymentProvider | StripePaymentProvider:
    """Return the provider selected by ``settings.payment_provider``."""
    name = settings.payment_provider.lower()
    if name == "fake":
        return FakePaymentProvider(secret=settings.stripe_webhook_secret or "fake-webhook-secret")
    if name == "stripe":
        if not settings.stripe_secret_key:
            raise PaymentConfigurationError(
                "PAYMENT_PROVIDER=stripe requires STRIPE_SECRET_KEY to be set"
            )
        if not settings.stripe_webhook_secret:
            raise PaymentConfigurationError(
                "PAYMENT_PROVIDER=stripe requires STRIPE_WEBHOOK_SECRET to be set"
            )
        return StripePaymentProvider(
            secret_key=settings.stripe_secret_key,
            webhook_secret=settings.stripe_webhook_secret,
        )
    raise PaymentConfigurationError(f"Unknown payment provider: {settings.payment_provider!r}")
