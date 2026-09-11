"""ORM models.

Importing this package imports every model module so ``Base.metadata`` is fully
populated — required for ``create_all`` in tests and for Alembic autogenerate. Add new
model modules to the imports below.
"""

from app.models.availability import CompanyDateCapacity
from app.models.booking import Booking, BookingStatus
from app.models.change_request import ChangeKind, QuoteChangeRequest
from app.models.company import Company
from app.models.company_knowledge import CompanyKnowledge
from app.models.conversation import (
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
)
from app.models.import_batch import ImportFormat, JobImportBatch
from app.models.job import Job, JobSource, MoveType, ParkingDifficulty
from app.models.lead import Lead, LeadSource, LeadStatus
from app.models.moving_request import (
    ExtractionSource,
    HomeSize,
    MovingRequest,
    PackingService,
    RequestStatus,
)
from app.models.payment import Payment, PaymentStatus, ProcessedWebhookEvent
from app.models.pricing_config import PricingConfigRow
from app.models.quote import Quote, QuoteStatus
from app.models.user import User, UserRole

__all__ = [
    "Booking",
    "BookingStatus",
    "ChangeKind",
    "Company",
    "CompanyDateCapacity",
    "CompanyKnowledge",
    "Conversation",
    "ConversationStatus",
    "ExtractionSource",
    "HomeSize",
    "ImportFormat",
    "Job",
    "JobImportBatch",
    "JobSource",
    "Lead",
    "LeadSource",
    "LeadStatus",
    "Message",
    "MessageRole",
    "MoveType",
    "MovingRequest",
    "PackingService",
    "ParkingDifficulty",
    "Payment",
    "PaymentStatus",
    "PricingConfigRow",
    "ProcessedWebhookEvent",
    "Quote",
    "QuoteChangeRequest",
    "QuoteStatus",
    "RequestStatus",
    "User",
    "UserRole",
]
