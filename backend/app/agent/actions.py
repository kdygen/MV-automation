"""The UI action contract: what the assistant may point a customer at.

The assistant never performs a transactional action. It may only name one of these,
and the frontend decides what to render and which deterministic flow to open. Every
value here corresponds to a control that already exists on the quote page, so an
``ui_action`` is a navigation hint — never a new capability.

Deliberately absent: a ``payment`` value. Whether money is owed depends on the
company's deposit configuration and the quote's current state, both server-side facts.
Letting the model choose between "accept" and "pay" would make it guess a commercial
question from conversational context; instead it always says :data:`ACCEPT_QUOTE` and
the frontend renders "Accept & book" or "Accept & pay the deposit" from configuration
it fetched itself.

:data:`NONE` is the default when the model suggests nothing. It is not offered to the
model — a turn with no action is expressed by not calling the tool at all, which costs
nothing, rather than by spending a tool call to say "nothing".
"""

from __future__ import annotations

import enum


class UiAction(enum.StrEnum):
    """Allowlisted navigation hints. The model may select one; it can execute none."""

    NONE = "none"
    CHANGE_DATE = "change_date"
    EDIT_MOVE = "edit_move"
    ACCEPT_QUOTE = "accept_quote"
    CONTACT_COMPANY = "contact_company"


#: What the model may choose, in the order shown to it. ``NONE`` is excluded on purpose.
SELECTABLE_ACTIONS: tuple[UiAction, ...] = (
    UiAction.CHANGE_DATE,
    UiAction.EDIT_MOVE,
    UiAction.ACCEPT_QUOTE,
    UiAction.CONTACT_COMPANY,
)

#: The model-visible enum values, as JSON Schema needs them.
SELECTABLE_VALUES: tuple[str, ...] = tuple(action.value for action in SELECTABLE_ACTIONS)
