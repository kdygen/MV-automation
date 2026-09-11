import { describe, expect, it } from "vitest";

import type {
  Ambiguity,
  FieldInfo,
  HistoricalSignals,
  HistoryRow,
  InspectResult,
  RowVerdict,
} from "./dashboard-types";
import {
  OUTCOME_FIELDS,
  REQUIRED_FIELDS,
  canPreview,
  fieldGroups,
  hasOutcome,
  hours,
  initialMapping,
  mappingBlockers,
  missingRequired,
  requestBody,
  rowHoursErrorPct,
  rowPriceErrorPct,
  routeLabel,
  setFieldMapping,
  signalsSummary,
  signedPct,
  sourceLabel,
  unansweredQuestions,
  unmappedHeaders,
  verdictLabel,
  verdictReason,
} from "./history";

const clean: Ambiguity = {
  date_order: "iso",
  decimal_style: "dot",
  date_ambiguous: false,
  money_ambiguous: false,
  ambiguous_money_fields: [],
  needs_input: false,
};

const field = (name: string, over: Partial<FieldInfo> = {}): FieldInfo => ({
  name,
  label: name,
  kind: "text",
  required: false,
  outcome: false,
  sensitive: false,
  ...over,
});

const inspect = (over: Partial<InspectResult> = {}): InspectResult => ({
  filename: "legacy.csv",
  file_format: "csv",
  row_count: 3,
  columns: [
    { header: "Move Date", sample_values: ["2025-01-02"], suggested_field: "move_date", confidence: "exact" },
    { header: "Size", sample_values: ["2br"], suggested_field: "home_size", confidence: "exact" },
    { header: "Hours", sample_values: ["6.5"], suggested_field: "actual_hours", confidence: "exact" },
    { header: "Customer", sample_values: ["Jane"], suggested_field: null, confidence: null },
  ],
  fields: [
    field("move_date", { required: true }),
    field("home_size", { required: true }),
    field("actual_hours", { outcome: true }),
    field("actual_total_cents", { outcome: true }),
    field("origin_line1", { sensitive: true }),
  ],
  suggested_mapping: { move_date: "Move Date", home_size: "Size", actual_hours: "Hours" },
  ambiguity: clean,
  ...over,
});

describe("mirrored rules", () => {
  it("matches the backend's required and outcome sets", () => {
    expect([...REQUIRED_FIELDS]).toEqual(["move_date", "home_size"]);
    expect([...OUTCOME_FIELDS]).toEqual(["actual_hours", "actual_total_cents"]);
  });
});

describe("initial mapping", () => {
  it("adopts the suggestion and the resolved parse options", () => {
    const state = initialMapping(inspect());
    expect(state.mapping.move_date).toBe("Move Date");
    expect(state.dateOrder).toBe("iso");
    expect(state.decimalStyle).toBe("dot");
  });

  it("leaves an ambiguous question unanswered rather than picking one", () => {
    const state = initialMapping(
      inspect({ ambiguity: { ...clean, date_ambiguous: true, date_order: null, needs_input: true } }),
    );
    expect(state.dateOrder).toBeNull();
  });

  it("does not alias the suggested mapping", () => {
    const source = inspect();
    const state = initialMapping(source);
    state.mapping.move_date = "Something else";
    expect(source.suggested_mapping.move_date).toBe("Move Date");
  });
});

describe("changing a mapping", () => {
  it("assigns a header to a field", () => {
    const state = setFieldMapping(initialMapping(inspect()), "actual_total_cents", "Hours");
    expect(state.mapping.actual_total_cents).toBe("Hours");
  });

  it("releases a header from whatever else claimed it", () => {
    // One column cannot feed two fields; the backend reads one column per field.
    const state = setFieldMapping(initialMapping(inspect()), "actual_total_cents", "Hours");
    expect(state.mapping.actual_hours).toBeUndefined();
  });

  it("clears a field with null", () => {
    const state = setFieldMapping(initialMapping(inspect()), "actual_hours", null);
    expect(state.mapping.actual_hours).toBeUndefined();
  });

  it("keeps unrelated fields untouched", () => {
    const state = setFieldMapping(initialMapping(inspect()), "actual_hours", null);
    expect(state.mapping.move_date).toBe("Move Date");
    expect(state.mapping.home_size).toBe("Size");
  });
});

describe("readiness", () => {
  it("accepts a complete mapping", () => {
    const state = initialMapping(inspect());
    expect(missingRequired(state)).toEqual([]);
    expect(hasOutcome(state)).toBe(true);
    expect(mappingBlockers(state, clean)).toEqual([]);
    expect(canPreview(state, clean)).toBe(true);
  });

  it("blocks when a required field is unmapped", () => {
    const state = setFieldMapping(initialMapping(inspect()), "home_size", null);
    expect(missingRequired(state)).toEqual(["home_size"]);
    expect(canPreview(state, clean)).toBe(false);
  });

  it("blocks when no outcome is mapped", () => {
    const state = setFieldMapping(initialMapping(inspect()), "actual_hours", null);
    expect(hasOutcome(state)).toBe(false);
    expect(mappingBlockers(state, clean).some((b) => b.includes("actual hours"))).toBe(true);
  });

  it("blocks until an ambiguous date question is answered", () => {
    const ambiguity = { ...clean, date_ambiguous: true, date_order: null, needs_input: true };
    const state = initialMapping(inspect({ ambiguity }));
    expect(unansweredQuestions(state, ambiguity)).toHaveLength(1);
    expect(canPreview(state, ambiguity)).toBe(false);

    expect(canPreview({ ...state, dateOrder: "dmy" }, ambiguity)).toBe(true);
  });

  it("blocks until an ambiguous money question is answered", () => {
    const ambiguity = { ...clean, money_ambiguous: true, decimal_style: null, needs_input: true };
    const state = initialMapping(inspect({ ambiguity }));
    expect(canPreview(state, ambiguity)).toBe(false);
    expect(canPreview({ ...state, decimalStyle: "comma" }, ambiguity)).toBe(true);
  });
});

describe("request body", () => {
  it("sends the mapping and omits unanswered options", () => {
    const state = { mapping: { move_date: "D" }, dateOrder: null, decimalStyle: null };
    expect(requestBody(state)).toEqual({ mapping: { move_date: "D" } });
  });

  it("includes the answers when given", () => {
    const body = requestBody({ mapping: {}, dateOrder: "dmy", decimalStyle: "comma" });
    expect(body.date_order).toBe("dmy");
    expect(body.decimal_style).toBe("comma");
  });

  it("never carries a company identifier", () => {
    const body = requestBody(initialMapping(inspect()));
    expect(Object.keys(body)).not.toContain("company_id");
    expect(Object.keys(body.mapping)).not.toContain("company_id");
  });
});

describe("unmapped columns", () => {
  it("lists what will be dropped so the omission is visible", () => {
    expect(unmappedHeaders(inspect(), initialMapping(inspect()))).toEqual(["Customer"]);
  });

  it("updates as the mapping changes", () => {
    const state = setFieldMapping(initialMapping(inspect()), "actual_hours", null);
    expect(unmappedHeaders(inspect(), state)).toEqual(["Hours", "Customer"]);
  });
});

describe("field groups", () => {
  it("puts required fields first and sensitive ones last", () => {
    const groups = fieldGroups(inspect().fields);
    expect(groups[0].title).toBe("Required");
    expect(groups[groups.length - 1].title).toContain("sensitive");
  });

  it("drops empty groups", () => {
    const groups = fieldGroups([field("move_date", { required: true })]);
    expect(groups).toHaveLength(1);
  });
});

describe("verdict copy", () => {
  it("labels each status in plain language", () => {
    expect(verdictLabel("ok")).toBe("Ready");
    expect(verdictLabel("warning")).toContain("warning");
    expect(verdictLabel("error")).toBe("Rejected");
    expect(verdictLabel("duplicate")).toBe("Already imported");
  });

  it("prefers an error over a warning as the reason", () => {
    const row: RowVerdict = {
      row_number: 1,
      status: "error",
      errors: [{ field: "home_size", message: "required" }],
      warnings: [{ field: null, message: "minor" }],
      preview: {},
    };
    expect(verdictReason(row)).toBe("required");
  });

  it("returns null when there is nothing to report", () => {
    expect(
      verdictReason({ row_number: 1, status: "ok", errors: [], warnings: [], preview: {} }),
    ).toBeNull();
  });
});

describe("formatting", () => {
  it("signs percentages so direction is unmistakable", () => {
    expect(signedPct(12.34)).toBe("+12.3%");
    expect(signedPct(-5)).toBe("-5%");
    expect(signedPct(0)).toBe("0%");
    expect(signedPct(null)).toBe("—");
  });

  it("formats hours", () => {
    expect(hours(6.55)).toBe("6.6h");
    expect(hours(null)).toBe("—");
  });

  it("labels sources", () => {
    expect(sourceLabel("platform")).toBe("Platform");
    expect(sourceLabel("import")).toBe("Imported");
  });
});

const row = (over: Partial<HistoryRow> = {}): HistoryRow => ({
  id: "1",
  source: "import",
  move_date: "2025-03-04",
  home_size: "2br",
  move_type: null,
  origin_city: "Springfield",
  origin_state: "IL",
  destination_city: "Chatham",
  destination_state: "IL",
  distance_miles: 18,
  quoted_hours: 6,
  quoted_total_cents: 130_000,
  actual_hours: 7.5,
  actual_crew_size: 3,
  actual_total_cents: 145_000,
  ...over,
});

describe("row derivations", () => {
  it("computes signed hours error", () => {
    expect(rowHoursErrorPct(row())).toBeCloseTo(25);
    expect(rowHoursErrorPct(row({ actual_hours: 4.5 }))).toBeCloseTo(-25);
  });

  it("computes signed price error", () => {
    expect(rowPriceErrorPct(row())).toBeCloseTo(11.538, 2);
  });

  it("returns null when either side is missing", () => {
    expect(rowHoursErrorPct(row({ quoted_hours: null }))).toBeNull();
    expect(rowHoursErrorPct(row({ actual_hours: null }))).toBeNull();
    expect(rowPriceErrorPct(row({ quoted_total_cents: 0 }))).toBeNull();
  });

  it("formats a route, tolerating missing ends", () => {
    expect(routeLabel(row())).toBe("Springfield, IL → Chatham, IL");
    expect(routeLabel(row({ destination_city: null, destination_state: null }))).toContain("?");
    expect(
      routeLabel(row({ origin_city: null, origin_state: null, destination_city: null, destination_state: null })),
    ).toBe("—");
  });
});

const signals = (over: Partial<HistoricalSignals> = {}): HistoricalSignals => ({
  comparable_count: 5,
  moves_with_hours: 5,
  moves_with_estimate: 4,
  median_actual_hours: 7.2,
  mean_actual_hours: 7.4,
  hours_p25: 6.5,
  hours_p75: 8.1,
  median_hours_error_pct: 15,
  overrun_share_pct: 75,
  common_issue_tags: ["freight_elevator"],
  matches: [],
  ...over,
});

describe("signals copy", () => {
  it("describes durations and bias without naming a price", () => {
    const text = signalsSummary(signals());
    expect(text).toContain("5 comparable moves");
    expect(text).toContain("7.2h");
    expect(text).toContain("middle half");
    expect(text).toContain("low");
    expect(text).not.toMatch(/\$|price|charge|quote/i);
  });

  it("reads the other direction when estimates ran high", () => {
    expect(signalsSummary(signals({ median_hours_error_pct: -10 }))).toContain("high");
  });

  it("says so plainly when there is nothing comparable", () => {
    expect(signalsSummary(signals({ comparable_count: 0 }))).toContain("No comparable moves");
  });

  it("omits statistics it does not have", () => {
    const text = signalsSummary(
      signals({ median_actual_hours: null, hours_p25: null, hours_p75: null, median_hours_error_pct: null }),
    );
    expect(text).toBe("5 comparable moves.");
  });

  it("has no field that could be mistaken for a price", () => {
    const keys = Object.keys(signals());
    expect(keys.filter((k) => /price|cents|total|cost/.test(k))).toEqual([]);
  });
});
