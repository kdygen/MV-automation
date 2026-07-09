"""Per-company pricing configuration.

:class:`PricingConfig` is the tunable half of Stage 1 pricing: hourly rates, crew
sizing, access/packing modifiers, travel fees, date multipliers, and the quote range
spread. It is stored as JSON on a versioned ``pricing_configs`` row — **append-only**,
so every historical quote can name the exact config version that produced it.

All money values are dollars here (human-edited); the engine converts to cents.
:data:`DEFAULT_PRICING_CONFIG` encodes sane industry-typical defaults so a new company
gets instant quotes before touching any settings.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.moving_request import HomeSize, PackingService


class PricingConfig(BaseModel):
    """Tunable rule parameters for Stage 1 pricing (local hourly moves)."""

    model_config = ConfigDict(frozen=True)

    currency: str = "USD"

    # Crew-hour foundations, keyed by home size.
    base_hours_by_home_size: dict[HomeSize, float] = Field(
        default={
            HomeSize.STUDIO: 3.0,
            HomeSize.ONE_BR: 4.0,
            HomeSize.TWO_BR: 5.5,
            HomeSize.THREE_BR: 7.5,
            HomeSize.FOUR_BR: 9.0,
            HomeSize.FIVE_BR_PLUS: 11.0,
        }
    )
    crew_by_home_size: dict[HomeSize, int] = Field(
        default={
            HomeSize.STUDIO: 2,
            HomeSize.ONE_BR: 2,
            HomeSize.TWO_BR: 3,
            HomeSize.THREE_BR: 3,
            HomeSize.FOUR_BR: 4,
            HomeSize.FIVE_BR_PLUS: 5,
        }
    )
    # Dollars per crew-hour, keyed by crew size (truck included).
    hourly_rate_by_crew: dict[int, float] = Field(
        default={2: 140.0, 3: 190.0, 4: 240.0, 5: 290.0}
    )
    min_billable_hours: float = Field(default=3.0, gt=0)

    # Access difficulty (hours added per side of the move).
    stairs_hours_per_flight: float = Field(default=0.4, ge=0)
    elevator_building_hours: float = Field(default=0.3, ge=0)

    # Packing service multiplies labor hours.
    packing_hours_multiplier: dict[PackingService, float] = Field(
        default={PackingService.NONE: 1.0, PackingService.PARTIAL: 1.25, PackingService.FULL: 1.5}
    )

    # Special items add fixed handling hours; unknown items fall back to the default.
    special_item_hours: dict[str, float] = Field(
        default={"piano": 1.5, "safe": 1.0, "pool_table": 1.0}
    )
    special_item_default_hours: float = Field(default=0.5, ge=0)

    # Travel fee (dollars).
    travel_fee_base: float = Field(default=50.0, ge=0)
    travel_fee_per_mile: float = Field(default=2.0, ge=0)

    # Demand-based date multipliers.
    weekend_multiplier: float = Field(default=1.10, ge=1.0)
    month_end_multiplier: float = Field(default=1.10, ge=1.0)
    month_end_from_day: int = Field(default=25, ge=1, le=31)
    peak_season_multiplier: float = Field(default=1.05, ge=1.0)
    peak_season_months: tuple[int, ...] = (5, 6, 7, 8, 9)

    # Quote range: total * (1 ± spread).
    range_spread_pct: float = Field(default=0.12, ge=0, lt=1)

    @model_validator(mode="after")
    def _crew_rates_cover_crew_sizes(self) -> PricingConfig:
        missing = set(self.crew_by_home_size.values()) - set(self.hourly_rate_by_crew)
        if missing:
            raise ValueError(f"hourly_rate_by_crew missing rates for crew sizes: {sorted(missing)}")
        if set(self.base_hours_by_home_size) != set(HomeSize):
            raise ValueError("base_hours_by_home_size must cover every home size")
        if set(self.crew_by_home_size) != set(HomeSize):
            raise ValueError("crew_by_home_size must cover every home size")
        return self


DEFAULT_PRICING_CONFIG = PricingConfig()
