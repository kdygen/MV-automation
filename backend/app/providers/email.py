"""Email provider: outbound transactional mail behind a one-method interface.

:class:`FakeEmailProvider` (default in dev/tests) records messages in an in-memory
outbox and logs them — tests assert on the outbox; local dev sees mail in the console.
Real providers (Resend/SMTP) land with the deployment milestone; selecting one earlier
raises a configuration error rather than silently dropping mail.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text_body: str
    from_address: str


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


class EmailProvider:
    """Protocol-by-convention: anything with ``send(to=, subject=, text_body=)``."""

    def send(self, *, to: str, subject: str, text_body: str) -> None:  # pragma: no cover
        raise NotImplementedError


class EmailConfigurationError(RuntimeError):
    """Raised when a configured email provider is unknown or not yet available."""


def get_email_provider(settings: Settings) -> FakeEmailProvider:
    """Return the email provider selected by ``settings.email_provider``."""
    name = settings.email_provider.lower()
    if name == "fake":
        return FakeEmailProvider(from_address=settings.email_from)
    if name in {"resend", "smtp"}:
        raise EmailConfigurationError(
            f"Email provider '{name}' is planned but not implemented yet; set EMAIL_PROVIDER=fake"
        )
    raise EmailConfigurationError(f"Unknown email provider '{name}'")
