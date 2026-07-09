"""MoveSpec — the pricing engine's input, decoupled from storage.

A :class:`MoveSpec` is everything the engine may consider about a move, as a frozen
value object. It is built from a :class:`~app.models.moving_request.MovingRequest` (or,
later, directly from LLM extraction), so the engine stays independent of the ORM and
HTTP layers and can be exercised with plain constructor calls in tests.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from app.models.moving_request import HomeSize, PackingService

if TYPE_CHECKING:
    from app.models.moving_request import MovingRequest


class SideAccess(BaseModel):
    """Access difficulty at one end of the move."""

    model_config = ConfigDict(frozen=True)

    floor: int = Field(default=1, ge=1)
    has_elevator: bool = False
    stairs_flights: int = Field(default=0, ge=0)


class MoveSpec(BaseModel):
    """Structured description of a move — the engine's sole input besides config."""

    model_config = ConfigDict(frozen=True)

    home_size: HomeSize
    packing_service: PackingService = PackingService.NONE
    special_items: tuple[str, ...] = ()
    origin: SideAccess = SideAccess()
    destination: SideAccess = SideAccess()
    distance_miles: float | None = None
    move_date: date

    @classmethod
    def from_moving_request(cls, request: MovingRequest) -> MoveSpec:
        """Build a spec from a persisted moving request."""
        return cls(
            home_size=request.home_size,
            packing_service=request.packing_service,
            special_items=tuple(str(item) for item in request.special_items),
            origin=SideAccess(
                floor=request.origin_floor,
                has_elevator=request.origin_has_elevator,
                stairs_flights=request.origin_stairs_flights,
            ),
            destination=SideAccess(
                floor=request.destination_floor,
                has_elevator=request.destination_has_elevator,
                stairs_flights=request.destination_stairs_flights,
            ),
            distance_miles=request.distance_miles,
            move_date=request.move_date,
        )
