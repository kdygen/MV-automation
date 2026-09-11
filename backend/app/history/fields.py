"""The canonical historical-move field registry.

This list is the import contract and, just as importantly, the **privacy boundary**.
There is no canonical field for a customer name, email, phone, or payment detail — so a
company can upload their entire operational spreadsheet and only movement data can
enter, because unmapped columns have nowhere to go. Nothing needs blocklisting; the
absence of a destination is the defence.

For the same structural reason there is no ``company_id`` field. A CSV column called
``company_id`` is simply unmapped, so a malicious export cannot address another tenant.

Synonyms exist because no moving company uses our column names. They drive *suggestions*
only — a suggestion is always shown for confirmation and never silently applied.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from dataclasses import field as dataclass_field

from app.models.job import MoveType, ParkingDifficulty
from app.models.moving_request import HomeSize, PackingService


class FieldKind(enum.StrEnum):
    DATE = "date"
    TEXT = "text"
    INT = "int"
    FLOAT = "float"
    MONEY = "money"
    BOOL = "bool"
    ENUM = "enum"
    LIST = "list"
    STATE = "state"
    ZIP = "zip"


@dataclass(frozen=True)
class CanonicalField:
    """One importable field.

    :param synonyms: normalized header spellings that suggest this field.
    :param sensitive: held to a higher bar — never suggested automatically, never
        returned on a customer-facing payload. Street addresses only.
    """

    name: str
    kind: FieldKind
    label: str
    synonyms: tuple[str, ...] = ()
    enum: type[enum.StrEnum] | None = None
    minimum: float | None = None
    maximum: float | None = None
    sensitive: bool = False
    #: Accepted spellings for enum values, beyond the enum's own member values.
    value_synonyms: dict[str, str] = dataclass_field(default_factory=dict)


_HOME_SIZE_SYNONYMS = {
    "studio": "studio", "bachelor": "studio", "0br": "studio", "0 bed": "studio",
    "1br": "1br", "1 br": "1br", "1bed": "1br", "1 bed": "1br", "1 bedroom": "1br",
    "one bedroom": "1br", "1bd": "1br",
    "2br": "2br", "2 br": "2br", "2bed": "2br", "2 bed": "2br", "2 bedroom": "2br",
    "two bedroom": "2br", "2bd": "2br",
    "3br": "3br", "3 br": "3br", "3bed": "3br", "3 bed": "3br", "3 bedroom": "3br",
    "three bedroom": "3br", "3bd": "3br",
    "4br": "4br", "4 br": "4br", "4bed": "4br", "4 bed": "4br", "4 bedroom": "4br",
    "four bedroom": "4br", "4bd": "4br",
    "5br_plus": "5br_plus", "5br": "5br_plus", "5 br": "5br_plus", "5+": "5br_plus",
    "5 bedroom": "5br_plus", "5br+": "5br_plus", "6br": "5br_plus", "house": "5br_plus",
}

_PACKING_SYNONYMS = {
    "none": "none", "no": "none", "n": "none", "self": "none", "customer packed": "none",
    "partial": "partial", "part": "partial", "some": "partial", "partial pack": "partial",
    "full": "full", "yes": "full", "y": "full", "full pack": "full", "complete": "full",
}

_MOVE_TYPE_SYNONYMS = {
    "local": "local", "residential": "local", "in town": "local",
    "long distance": "long_distance", "long_distance": "long_distance",
    "interstate": "long_distance", "ld": "long_distance",
    "commercial": "commercial", "office": "commercial", "business": "commercial",
    "storage": "storage", "storage in": "storage", "storage out": "storage",
}

_PARKING_SYNONYMS = {
    "easy": "easy", "good": "easy", "driveway": "easy",
    "moderate": "moderate", "medium": "moderate", "ok": "moderate",
    "difficult": "difficult", "hard": "difficult", "bad": "difficult",
    "street only": "difficult", "no parking": "difficult",
}

#: Bounds that reject the physically impossible rather than merely the unusual. A
#: 400-hour move or a 90-person crew is a unit error or a mis-mapped column, and letting
#: it through would distort every average derived from it.
CANONICAL_FIELDS: tuple[CanonicalField, ...] = (
    # --- identity / timing ---
    CanonicalField(
        "move_date", FieldKind.DATE, "Move date",
        ("move date", "movedate", "jobdate", "job date", "date", "date of move",
         "service date", "scheduled date", "start date"),
    ),
    CanonicalField(
        "external_ref", FieldKind.TEXT, "Your job number",
        ("job number", "jobno", "job id", "job #", "invoice", "invoice number",
         "order number", "order id", "reference", "ref"),
    ),
    # --- characteristics ---
    CanonicalField(
        "home_size", FieldKind.ENUM, "Home size",
        ("home size", "homesize", "size", "bedrooms", "beds", "residence size",
         "apartment size", "property size", "br"),
        enum=HomeSize, value_synonyms=_HOME_SIZE_SYNONYMS,
    ),
    CanonicalField(
        "move_type", FieldKind.ENUM, "Move type",
        ("move type", "movetype", "type", "service type", "job type"),
        enum=MoveType, value_synonyms=_MOVE_TYPE_SYNONYMS,
    ),
    CanonicalField(
        "distance_miles", FieldKind.FLOAT, "Distance (miles)",
        ("distance", "miles", "distance miles", "mileage", "total miles", "trip miles"),
        minimum=0, maximum=5000,
    ),
    # --- geography ---
    CanonicalField("origin_city", FieldKind.TEXT, "Origin city",
                   ("origin city", "from city", "pickup city", "origincity", "city from")),
    CanonicalField("origin_state", FieldKind.STATE, "Origin state",
                   ("origin state", "from state", "pickup state", "state from")),
    CanonicalField("origin_zip", FieldKind.ZIP, "Origin ZIP",
                   ("origin zip", "from zip", "pickup zip", "origin postal",
                    "from postal code", "zip from", "originzip")),
    CanonicalField("destination_city", FieldKind.TEXT, "Destination city",
                   ("destination city", "to city", "dropoff city", "delivery city",
                    "city to")),
    CanonicalField("destination_state", FieldKind.STATE, "Destination state",
                   ("destination state", "to state", "dropoff state", "state to")),
    CanonicalField("destination_zip", FieldKind.ZIP, "Destination ZIP",
                   ("destination zip", "to zip", "dropoff zip", "delivery zip",
                    "destination postal", "to postal code", "zip to")),
    # --- street addresses: opt-in only, never auto-suggested ---
    CanonicalField("origin_line1", FieldKind.TEXT, "Origin street address",
                   sensitive=True),
    CanonicalField("destination_line1", FieldKind.TEXT, "Destination street address",
                   sensitive=True),
    # --- access ---
    CanonicalField("origin_floor", FieldKind.INT, "Origin floor",
                   ("origin floor", "from floor", "pickup floor", "floor from"),
                   minimum=0, maximum=100),
    CanonicalField("origin_has_elevator", FieldKind.BOOL, "Origin elevator",
                   ("origin elevator", "from elevator", "pickup elevator",
                    "elevator from", "has elevator origin")),
    CanonicalField("origin_stairs_flights", FieldKind.INT, "Origin stair flights",
                   ("origin stairs", "from stairs", "stairs from", "flights from",
                    "origin flights"), minimum=0, maximum=40),
    CanonicalField("destination_floor", FieldKind.INT, "Destination floor",
                   ("destination floor", "to floor", "dropoff floor", "floor to"),
                   minimum=0, maximum=100),
    CanonicalField("destination_has_elevator", FieldKind.BOOL, "Destination elevator",
                   ("destination elevator", "to elevator", "dropoff elevator",
                    "elevator to")),
    CanonicalField("destination_stairs_flights", FieldKind.INT, "Destination stair flights",
                   ("destination stairs", "to stairs", "stairs to", "flights to",
                    "destination flights"), minimum=0, maximum=40),
    CanonicalField("long_carry", FieldKind.BOOL, "Long carry",
                   ("long carry", "longcarry", "carry", "long walk")),
    CanonicalField("parking_difficulty", FieldKind.ENUM, "Parking difficulty",
                   ("parking", "parking difficulty", "truck access", "access"),
                   enum=ParkingDifficulty, value_synonyms=_PARKING_SYNONYMS),
    # --- services / items ---
    CanonicalField("packing_service", FieldKind.ENUM, "Packing service",
                   ("packing", "packing service", "pack", "packing type", "packed by us"),
                   enum=PackingService, value_synonyms=_PACKING_SYNONYMS),
    CanonicalField("special_items", FieldKind.LIST, "Special items",
                   ("special items", "specialty items", "heavy items", "bulky items",
                    "items", "extras")),
    CanonicalField("has_storage", FieldKind.BOOL, "Storage used",
                   ("storage", "used storage", "storage in transit", "sit")),
    # --- operations ---
    CanonicalField("quoted_hours", FieldKind.FLOAT, "Estimated hours",
                   ("estimated hours", "quoted hours", "est hours", "est. hrs",
                    "estimate hours", "projected hours"), minimum=0, maximum=200),
    CanonicalField("quoted_crew_size", FieldKind.INT, "Estimated crew",
                   ("estimated crew", "quoted crew", "est crew", "planned crew",
                    "men quoted"), minimum=1, maximum=20),
    CanonicalField("actual_hours", FieldKind.FLOAT, "Actual hours",
                   ("actual hours", "hours", "actualduration", "actual duration",
                    "duration", "total hours", "labor hours", "hrs", "time on job"),
                   minimum=0, maximum=200),
    CanonicalField("actual_crew_size", FieldKind.INT, "Actual crew size",
                   ("crew", "crew size", "crewsize", "men", "movers", "workers",
                    "number of movers", "staff", "actual crew"), minimum=1, maximum=20),
    CanonicalField("actual_volume_cuft", FieldKind.FLOAT, "Volume (cu ft)",
                   ("volume", "cubic feet", "cuft", "cu ft", "volume cuft"),
                   minimum=0, maximum=100_000),
    # --- financial ---
    CanonicalField("quoted_total_cents", FieldKind.MONEY, "Quoted total",
                   ("quoted total", "estimate", "estimated total", "quote", "quoted price",
                    "estimated price", "quoted amount")),
    CanonicalField("actual_total_cents", FieldKind.MONEY, "Final total",
                   ("total", "final total", "finalamount", "final amount", "final price",
                    "amount", "invoice total", "revenue", "charged", "actual total")),
    CanonicalField("additional_charges_cents", FieldKind.MONEY, "Additional charges",
                   ("additional charges", "extra charges", "surcharges", "add ons",
                    "extras charged")),
    # --- outcome / operational intelligence ---
    CanonicalField("delay_minutes", FieldKind.INT, "Delay (minutes)",
                   ("delay", "delay minutes", "late minutes", "delay mins"),
                   minimum=0, maximum=2880),
    CanonicalField("issue_tags", FieldKind.LIST, "Issue tags",
                   ("issues", "issue tags", "problems tags", "flags")),
    CanonicalField("problem_notes", FieldKind.TEXT, "Problems",
                   ("problems", "problem notes", "issues notes", "what went wrong",
                    "exceptions")),
    CanonicalField("building_notes", FieldKind.TEXT, "Building notes",
                   ("building notes", "building", "access notes", "site notes",
                    "property notes")),
    CanonicalField("change_notes", FieldKind.TEXT, "Customer changes",
                   ("changes", "change notes", "customer changes", "change requests")),
    CanonicalField("variance_reason", FieldKind.TEXT, "Why price differed",
                   ("variance", "variance reason", "price difference reason",
                    "reason for difference", "overage reason")),
    CanonicalField("notes", FieldKind.TEXT, "General notes",
                   ("notes", "comments", "remarks", "description", "note")),
)

FIELDS_BY_NAME: dict[str, CanonicalField] = {f.name: f for f in CANONICAL_FIELDS}

#: Without these a row is not a move at all.
REQUIRED_FIELDS: tuple[str, ...] = ("move_date", "home_size")

#: At least one must be present, or the row records no outcome and is not evidence.
OUTCOME_FIELDS: tuple[str, ...] = ("actual_hours", "actual_total_cents")

SENSITIVE_FIELDS: frozenset[str] = frozenset(f.name for f in CANONICAL_FIELDS if f.sensitive)

#: Fields carried into the duplicate fingerprint. Deliberately the identifying facts of
#: a move rather than every column: a company that re-exports with an extra notes column
#: must still be recognised as re-uploading the same history.
FINGERPRINT_FIELDS: tuple[str, ...] = (
    "move_date",
    "home_size",
    "origin_zip",
    "destination_zip",
    "actual_hours",
    "actual_crew_size",
    "actual_total_cents",
    "distance_miles",
)
