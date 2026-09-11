/** Dashboard API contract types — mirrors app/schemas/dashboard.py and quotes.py. */

import type { LineItem } from "./types";

export interface LeadRow {
  id: string;
  name: string;
  email: string;
  phone: string | null;
  source: string;
  status: string;
  created_at: string;
}

export interface RequestAdmin {
  id: string;
  origin_line1: string;
  origin_city: string;
  origin_state: string;
  origin_zip: string;
  destination_line1: string;
  destination_city: string;
  destination_state: string;
  destination_zip: string;
  move_date: string;
  is_date_flexible: boolean;
  home_size: string;
  packing_service: string;
  special_items: string[];
  notes: string | null;
  distance_miles: number | null;
  status: string;
  created_at: string;
}

export interface QuoteRow {
  id: string;
  status: string;
  currency: string;
  amount_min_cents: number;
  amount_max_cents: number;
  is_adjusted: boolean;
  created_at: string;
  valid_until: string;
  lead_name: string;
  lead_email: string;
  move_date: string;
  home_size: string;
}

export interface LeadDetail {
  lead: LeadRow;
  requests: RequestAdmin[];
  quotes: QuoteRow[];
}

export interface QuoteAdmin {
  id: string;
  status: string;
  currency: string;
  amount_min_cents: number;
  amount_max_cents: number;
  total_cents: number;
  estimated_hours: number;
  crew_size: number;
  engine_version: string;
  is_adjusted: boolean;
  line_items: LineItem[];
  public_token: string;
  valid_until: string;
  accepted_at: string | null;
  created_at: string;
}

export interface BookingRow {
  id: string;
  scheduled_date: string;
  time_window: string | null;
  crew_size: number;
  status: string;
  notes: string | null;
  lead_name: string;
  lead_email: string;
  amount_min_cents: number;
  amount_max_cents: number;
  quote_id: string;
}

export interface CompanySettings {
  name: string;
  slug: string;
  email: string | null;
  phone: string | null;
  quote_review_mode: boolean;
  quote_validity_days: number;
}

/** The full pricing config document (see backend app/pricing/config.py). */
export interface PricingConfigDoc {
  currency: string;
  base_hours_by_home_size: Record<string, number>;
  crew_by_home_size: Record<string, number>;
  hourly_rate_by_crew: Record<string, number>;
  min_billable_hours: number;
  stairs_hours_per_flight: number;
  elevator_building_hours: number;
  packing_hours_multiplier: Record<string, number>;
  special_item_hours: Record<string, number>;
  special_item_default_hours: number;
  travel_fee_base: number;
  travel_fee_per_mile: number;
  weekend_multiplier: number;
  month_end_multiplier: number;
  month_end_from_day: number;
  peak_season_multiplier: number;
  peak_season_months: number[];
  range_spread_pct: number;
}

export interface PricingSettings {
  version: number;
  config: PricingConfigDoc;
}

export interface Me {
  id: string;
  company_id: string;
  email: string;
  role: string;
}

export interface JobRow {
  id: string;
  source: string;
  move_date: string;
  home_size: string;
  packing_service: string;
  distance_miles: number | null;
  quoted_hours: number | null;
  quoted_total_cents: number | null;
  actual_hours: number;
  actual_crew_size: number;
  actual_total_cents: number;
  actual_volume_cuft: number | null;
  created_at: string;
}

export interface AccuracySummary {
  job_count: number;
  jobs_with_quote: number;
  total_mape_pct: number | null;
  hours_mae: number | null;
}

export interface ImportResult {
  imported: number;
  errors: { row: number; message: string }[];
}

export interface CompleteBookingPayload {
  actual_hours: number;
  actual_crew_size: number;
  actual_total_dollars: number;
  actual_volume_cuft?: number | null;
  notes?: string | null;
}

/** One company knowledge entry as the dashboard sees it (backend: KnowledgeEntryOut). */
export interface KnowledgeEntry {
  id: string;
  category: string;
  title: string;
  content: string;
  keywords: string | null;
  is_active: boolean;
  updated_at: string;
}

/** POST body. `company_id` is deliberately absent — the backend derives it from auth. */
export interface KnowledgeEntryPayload {
  category: string;
  title: string;
  content: string;
  keywords?: string | null;
  is_active?: boolean;
}

/** PATCH body — only the fields being changed. */
export type KnowledgeEntryPatch = Partial<KnowledgeEntryPayload>;

/**
 * An onboarding template: a question customers ask, plus the search vocabulary for it.
 * Carries no `content` — the owner writes every business fact themselves.
 */
export interface StarterTopic {
  category: string;
  title: string;
  prompt: string;
  keywords: string;
}

// ---------------------------------------------------------------- history (Step 5)

/** One completed move in the history table. No street addresses at list level. */
export interface HistoryRow {
  id: string;
  source: "platform" | "import" | string;
  move_date: string;
  home_size: string;
  move_type: string | null;
  origin_city: string | null;
  origin_state: string | null;
  destination_city: string | null;
  destination_state: string | null;
  distance_miles: number | null;
  quoted_hours: number | null;
  quoted_total_cents: number | null;
  actual_hours: number | null;
  actual_crew_size: number | null;
  actual_total_cents: number | null;
}

export interface HistoryDetail extends HistoryRow {
  packing_service: string;
  special_items: string[] | null;
  has_storage: boolean | null;
  origin_zip: string | null;
  destination_zip: string | null;
  origin_line1: string | null;
  destination_line1: string | null;
  origin_floor: number | null;
  origin_has_elevator: boolean | null;
  origin_stairs_flights: number | null;
  destination_floor: number | null;
  destination_has_elevator: boolean | null;
  destination_stairs_flights: number | null;
  long_carry: boolean | null;
  parking_difficulty: string | null;
  quoted_crew_size: number | null;
  additional_charges_cents: number | null;
  actual_volume_cuft: number | null;
  delay_minutes: number | null;
  issue_tags: string[] | null;
  notes: string | null;
  problem_notes: string | null;
  building_notes: string | null;
  change_notes: string | null;
  variance_reason: string | null;
  external_ref: string | null;
  import_batch_id: string | null;
  created_at: string;
}

export interface HistorySummary {
  total_moves: number;
  imported_moves: number;
  platform_moves: number;
  earliest_move_date: string | null;
  latest_move_date: string | null;
  median_actual_hours: number | null;
  median_hours_error_pct: number | null;
  median_price_error_pct: number | null;
  moves_with_hours: number;
  moves_with_estimate: number;
}

export interface ImportBatch {
  id: string;
  filename: string;
  file_format: string;
  row_count_total: number;
  row_count_imported: number;
  row_count_skipped: number;
  row_count_rejected: number;
  reverted_at: string | null;
  created_at: string;
}

export interface ColumnInfo {
  header: string;
  sample_values: string[];
  suggested_field: string | null;
  confidence: string | null;
}

export interface FieldInfo {
  name: string;
  label: string;
  kind: string;
  required: boolean;
  outcome: boolean;
  sensitive: boolean;
}

export interface Ambiguity {
  date_order: string | null;
  decimal_style: string | null;
  date_ambiguous: boolean;
  money_ambiguous: boolean;
  ambiguous_money_fields: string[];
  needs_input: boolean;
}

export interface InspectResult {
  filename: string;
  file_format: string;
  row_count: number;
  columns: ColumnInfo[];
  fields: FieldInfo[];
  suggested_mapping: Record<string, string>;
  ambiguity: Ambiguity;
}

export interface RowIssue {
  field: string | null;
  message: string;
}

export interface RowVerdict {
  row_number: number;
  status: "ok" | "warning" | "error" | "duplicate" | string;
  errors: RowIssue[];
  warnings: RowIssue[];
  preview: Record<string, unknown>;
}

export interface PreviewResult {
  filename: string;
  total: number;
  importable: number;
  warnings: number;
  rejected: number;
  duplicates: number;
  ambiguity: Ambiguity;
  rows: RowVerdict[];
  rows_truncated: boolean;
}

export interface ConfirmResult {
  batch: ImportBatch;
  rejected_rows: RowVerdict[];
}

/** The mapping plus any answers the file forced us to ask for. */
export interface ImportRequestBody {
  mapping: Record<string, string>;
  date_order?: string;
  decimal_style?: string;
}

export interface SimilarMove {
  id: string;
  source: string;
  score: number;
  coverage: number;
  matched_on: string[];
  move_date: string;
  home_size: string;
  distance_miles: number | null;
  packing_service: string | null;
  special_items: string[] | null;
  origin_floor: number | null;
  origin_stairs_flights: number | null;
  origin_has_elevator: boolean | null;
  destination_floor: number | null;
  destination_stairs_flights: number | null;
  destination_has_elevator: boolean | null;
  long_carry: boolean | null;
  parking_difficulty: string | null;
  actual_hours: number | null;
  actual_crew_size: number | null;
  quoted_hours: number | null;
  hours_error_pct: number | null;
  delay_minutes: number | null;
  issue_tags: string[] | null;
  problem_notes: string | null;
  building_notes: string | null;
}

/**
 * Evidence from comparable history. Deliberately carries no price and no recommended
 * crew — the backend does not produce them, and the UI must not imply them.
 */
export interface HistoricalSignals {
  comparable_count: number;
  moves_with_hours: number;
  moves_with_estimate: number;
  median_actual_hours: number | null;
  mean_actual_hours: number | null;
  hours_p25: number | null;
  hours_p75: number | null;
  median_hours_error_pct: number | null;
  overrun_share_pct: number | null;
  common_issue_tags: string[];
  matches: SimilarMove[];
}
