"""Email provider: outbound transactional mail behind a one-method interface.

- :class:`FakeEmailProvider` (default in dev/tests) records messages in an in-memory
  outbox and logs them — tests assert on the outbox; local dev sees mail in the console.
- :class:`ResendEmailProvider` sends real mail through the Resend HTTP API. Selected
  with ``EMAIL_PROVIDER=resend`` + ``EMAIL_API_KEY``; the sending domain of
  ``EMAIL_FROM`` must be verified in Resend or mail lands in spam.

Send failures raise :class:`EmailSendError`; callers (the notification service) treat
mail as best-effort and never let it break a business transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_RESEND_ENDPOINT = "https://api.resend.com/emails"
_SEND_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text_body: str
    from_address: str


class EmailSendError(RuntimeError):
    """A provider failed to hand the message to its delivery service."""


@dataclass
class FakeEmailProvider:
    """Records messages instead of sending them. Default in development and tests."""

    from_address: str = "quotes@example.com"
    outbox: list[EmailMessage] = field(default_factory=list)

    def send(self, *, to: str, subject: str, text_body: str) -> None:
        message = EmailMessage(
            to=to, subject=subject, text_body=text_body, from_address=self.from_address
        )
        self.outbox.append(message)
        logger.info("FakeEmail to=%s subject=%r", to, subject)


class ResendEmailProvider:
    """Sends mail via Resend (https://resend.com).

    :param transport: injectable ``httpx`` transport so tests run without network.
    """

    def __init__(
        self,
        *,
        api_key: str,
        from_address: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self.from_address = from_address
        self._transport = transport

    def send(self, *, to: str, subject: str, text_body: str) -> None:
        try:
            with httpx.Client(
                transport=self._transport, timeout=_SEND_TIMEOUT_SECONDS
            ) as client:
                response = client.post(
                    _RESEND_ENDPOINT,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "from": self.from_address,
                        "to": [to],
                        "subject": subject,
                        "text": text_body,
                    },
                )
        except httpx.HTTPError as exc:
            raise EmailSendError(f"Resend request failed: {exc}") from exc

        if response.status_code >= 400:
            raise EmailSendError(
                f"Resend rejected the message (HTTP {response.status_code}): "
                f"{response.text[:200]}"
            )
        logger.info("Resend accepted email to=%s subject=%r", to, subject)


class EmailProvider:
    """Protocol-by-convention: anything with ``send(to=, subject=, text_body=)``."""

    def send(self, *, to: str, subject: str, text_body: str) -> None:  # pragma: no cover
        raise NotImplementedError


class EmailConfigurationError(RuntimeError):
    """Raised when a configured email provider is unknown or not yet available."""


def get_email_provider(settings: Settings) -> FakeEmailProvider | ResendEmailProvider:
    """Return the email provider selected by ``settings.email_provider``."""
    name = settings.email_provider.lower()
    if name == "fake":
        return FakeEmailProvider(from_address=settings.email_from)
    if name == "resend":
        if not settings.email_api_key:
            raise EmailConfigurationError(
                "EMAIL_PROVIDER=resend requires EMAIL_API_KEY to be set"
            )
        return ResendEmailProvider(
            api_key=settings.email_api_key, from_address=settings.email_from
        )
    if name == "smtp":
        raise EmailConfigurationError(
            "Email provider 'smtp' is planned but not implemented yet; "
            "set EMAIL_PROVIDER=fake or resend"
        )
    raise EmailConfigurationError(f"Unknown email provider '{name}'")
