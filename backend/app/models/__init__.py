"""ORM models.

Importing this package imports every model module so ``Base.metadata`` is fully
populated — required for ``create_all`` in tests and for Alembic autogenerate. Add new
model modules to the imports below.
"""

from app.models.booking import Booking, BookingStatus
from app.models.company import Company
from app.models.company_knowledge import CompanyKnowledge
from app.models.conversation import (
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
)
from app.models.job import Job, JobSource
from app.models.lead import Lead, LeadSource, LeadStatus
from app.models.moving_request import (
    ExtractionSource,
    HomeSize,
    MovingRequest,
    PackingService,
    RequestStatus,
)
from app.models.pricing_config import PricingConfigRow
from app.models.quote import Quote, QuoteStatus
from app.models.user import User, UserRole

__all__ = [
    "Booking",
    "BookingStatus",
    "Company",
    "CompanyKnowledge",
    "Conversation",
    "ConversationStatus",
    "ExtractionSource",
    "HomeSize",
    "Job",
    "JobSource",
    "Lead",
    "LeadSource",
    "LeadStatus",
    "Message",
    "MessageRole",
    "MovingRequest",
    "PackingService",
    "PricingConfigRow",
    "Quote",
    "QuoteStatus",
    "RequestStatus",
    "User",
    "UserRole",
]
