/**
 * Pure logic for the history dashboard: import-wizard state, formatting, and the
 * readiness rules that decide when a mapping may be submitted.
 *
 * The wizard's rules live here rather than in the component so they can be tested
 * without a DOM, and so the *same* rules the backend enforces are mirrored exactly once.
 * The backend stays authoritative — this only avoids a round trip to learn that the
 * required columns are not mapped yet.
 */

import type {
  Ambiguity,
  FieldInfo,
  HistoricalSignals,
  HistoryRow,
  ImportRequestBody,
  InspectResult,
  RowVerdict,
} from "./dashboard-types";

/** Mirrors REQUIRED_FIELDS in app/history/fields.py. */
export const REQUIRED_FIELDS = ["move_date", "home_size"] as const;

/** Mirrors OUTCOME_FIELDS — at least one is needed or the row records no outcome. */
export const OUTCOME_FIELDS = ["actual_hours", "actual_total_cents"] as const;

export const WIZARD_STEPS = ["choose", "map", "review", "done"] as const;
export type WizardStep = (typeof WIZARD_STEPS)[number];

export interface MappingState {
  /** canonical field -> source header. A field absent here is simply not imported. */
  mapping: Record<string, string>;
  dateOrder: string | null;
  decimalStyle: string | null;
}

export function initialMapping(inspect: InspectResult): MappingState {
  return {
    mapping: { ...inspect.suggested_mapping },
    // Pre-answered when the file itself was unambiguous; null means we must ask.
    dateOrder: inspect.ambiguity.date_ambiguous ? null : inspect.ambiguity.date_order,
    decimalStyle: inspect.ambiguity.money_ambiguous ? null : inspect.ambiguity.decimal_style,
  };
}

/** Assign a header to a field, or clear it. One header may serve only one field. */
export function setFieldMapping(
  state: MappingState,
  field: string,
  header: string | null,
): MappingState {
  const mapping: Record<string, string> = {};
  for (const [name, value] of Object.entries(state.mapping)) {
    // Releasing the header from whatever else claimed it keeps the mapping a bijection,
    // which is what the backend assumes when it reads one column per field.
    if (name !== field && value !== header) mapping[name] = value;
  }
  if (header) mapping[field] = header;
  return { ...state, mapping };
}

export function missingRequired(state: MappingState): string[] {
  return REQUIRED_FIELDS.filter((field) => !state.mapping[field]);
}

export function hasOutcome(state: MappingState): boolean {
  return OUTCOME_FIELDS.some((field) => Boolean(state.mapping[field]));
}

/** Which questions the file forced, and we still have no answer for. */
export function unansweredQuestions(state: MappingState, ambiguity: Ambiguity): string[] {
  const questions: string[] = [];
  if (ambiguity.date_ambiguous && !state.dateOrder) {
    questions.push("Are the dates day/month or month/day?");
  }
  if (ambiguity.money_ambiguous && !state.decimalStyle) {
    questions.push("Do the amounts use 1,234.56 or 1.234,56?");
  }
  return questions;
}

export function mappingBlockers(state: MappingState, ambiguity: Ambiguity): string[] {
  const blockers: string[] = [];
  const missing = missingRequired(state);
  if (missing.length > 0) blockers.push(`Map ${missing.join(" and ")}.`);
  if (!hasOutcome(state)) blockers.push("Map actual hours or a final total.");
  return [...blockers, ...unansweredQuestions(state, ambiguity)];
}

export function canPreview(state: MappingState, ambiguity: Ambiguity): boolean {
  return mappingBlockers(state, ambiguity).length === 0;
}

export function requestBody(state: MappingState): ImportRequestBody {
  const body: ImportRequestBody = { mapping: state.mapping };
  if (state.dateOrder) body.date_order = state.dateOrder;
  if (state.decimalStyle) body.decimal_style = state.decimalStyle;
  return body;
}

/** Headers the company has chosen not to import — shown so the omission is deliberate. */
export function unmappedHeaders(inspect: InspectResult, state: MappingState): string[] {
  const claimed = new Set(Object.values(state.mapping));
  return inspect.columns.map((c) => c.header).filter((h) => !claimed.has(h));
}

/** Group the field catalogue for a readable mapping form. */
export function fieldGroups(fields: FieldInfo[]): { title: string; fields: FieldInfo[] }[] {
  const pick = (test: (f: FieldInfo) => boolean) => fields.filter(test);
  const inGroup = (...names: string[]) => (f: FieldInfo) => names.includes(f.name);
  return [
    { title: "Required", fields: pick((f) => f.required) },
    { title: "Outcome", fields: pick((f) => f.outcome && !f.required) },
    {
      title: "Move & route",
      fields: pick(
        inGroup(
          "external_ref", "move_type", "distance_miles",
          "origin_city", "origin_state", "origin_zip",
          "destination_city", "destination_state", "destination_zip",
        ),
      ),
    },
    {
      title: "Access",
      fields: pick(
        inGroup(
          "origin_floor", "origin_has_elevator", "origin_stairs_flights",
          "destination_floor", "destination_has_elevator", "destination_stairs_flights",
          "long_carry", "parking_difficulty",
        ),
      ),
    },
    {
      title: "Services & operations",
      fields: pick(
        inGroup(
          "packing_service", "special_items", "has_storage",
          "quoted_hours", "quoted_crew_size", "actual_crew_size", "actual_volume_cuft",
          "quoted_total_cents", "additional_charges_cents",
        ),
      ),
    },
    {
      title: "Notes & issues",
      fields: pick(
        inGroup(
          "delay_minutes", "issue_tags", "problem_notes", "building_notes",
          "change_notes", "variance_reason", "notes",
        ),
      ),
    },
    { title: "Street addresses (optional, sensitive)", fields: pick((f) => f.sensitive) },
  ].filter((group) => group.fields.length > 0);
}

export const VERDICT_STYLES: Record<string, string> = {
  ok: "bg-green-100 text-green-700",
  warning: "bg-amber-100 text-amber-700",
  error: "bg-red-100 text-red-700",
  duplicate: "bg-slate-200 text-slate-600",
};

export function verdictLabel(status: string): string {
  switch (status) {
    case "ok":
      return "Ready";
    case "warning":
      return "Imports with a warning";
    case "error":
      return "Rejected";
    case "duplicate":
      return "Already imported";
    default:
      return status;
  }
}

/** First issue on a row — what the owner needs to read to act. */
export function verdictReason(row: RowVerdict): string | null {
  return row.errors[0]?.message ?? row.warnings[0]?.message ?? null;
}

/** "+12%" / "-5%" / "—". Signed, because direction is the whole point of a bias. */
export function signedPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const rounded = Math.round(value * 10) / 10;
  return `${rounded > 0 ? "+" : ""}${rounded}%`;
}

export function hours(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(value * 10) / 10}h`;
}

/** Estimate error for one row, signed. Positive means the job ran over. */
export function rowHoursErrorPct(row: HistoryRow): number | null {
  if (row.actual_hours === null || !row.quoted_hours) return null;
  return ((row.actual_hours - row.quoted_hours) / row.quoted_hours) * 100;
}

export function rowPriceErrorPct(row: HistoryRow): number | null {
  if (row.actual_total_cents === null || !row.quoted_total_cents) return null;
  return ((row.actual_total_cents - row.quoted_total_cents) / row.quoted_total_cents) * 100;
}

export function routeLabel(row: HistoryRow): string {
  const from = [row.origin_city, row.origin_state].filter(Boolean).join(", ");
  const to = [row.destination_city, row.destination_state].filter(Boolean).join(", ");
  if (!from && !to) return "—";
  return `${from || "?"} → ${to || "?"}`;
}

/**
 * One-line plain-English reading of the signals.
 *
 * Deliberately describes what happened and never what to charge: the backend emits no
 * price, and this copy must not invent the implication.
 */
export function signalsSummary(signals: HistoricalSignals): string {
  if (signals.comparable_count === 0) return "No comparable moves in your history yet.";
  const parts = [
    `${signals.comparable_count} comparable move${signals.comparable_count === 1 ? "" : "s"}`,
  ];
  if (signals.median_actual_hours !== null) {
    parts.push(`typically took ${hours(signals.median_actual_hours)}`);
  }
  if (signals.hours_p25 !== null && signals.hours_p75 !== null) {
    parts.push(`(middle half ${hours(signals.hours_p25)}–${hours(signals.hours_p75)})`);
  }
  if (signals.median_hours_error_pct !== null) {
    const bias = signals.median_hours_error_pct;
    parts.push(
      bias > 0
        ? `estimates ran ${signedPct(bias)} low`
        : `estimates ran ${signedPct(Math.abs(bias))} high`,
    );
  }
  return `${parts.join(" · ")}.`;
}

export function sourceLabel(source: string): string {
  return source === "platform" ? "Platform" : "Imported";
}
