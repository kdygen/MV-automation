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
