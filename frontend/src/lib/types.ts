/**
 * API contract types — mirrors the FastAPI schemas (app/schemas/*.py).
 * Keep in sync with the backend; these are the only shapes the UI relies on.
 */

export type HomeSize = "studio" | "1br" | "2br" | "3br" | "4br" | "5br_plus";
export type PackingService = "none" | "partial" | "full";

export interface ContactIn {
  name: string;
  email: string;
  phone?: string | null;
}

export interface AddressIn {
  line1: string;
  city: string;
  state: string;
  zip: string;
  floor: number;
  has_elevator: boolean;
  stairs_flights: number;
}

export interface MovingRequestIn {
  contact: ContactIn;
  origin: AddressIn;
  destination: AddressIn;
  move_date: string; // ISO date
  is_date_flexible: boolean;
  home_size: HomeSize;
  packing_service: PackingService;
  special_items: string[];
  notes?: string | null;
}

export interface LineItem {
  code: string;
  label: string;
  amount_cents: number;
  meta?: Record<string, unknown>;
}

export interface QuoteSummary {
  status: "sent" | "pending_review" | string;
  public_token: string | null;
  currency: string;
  amount_min_cents: number | null;
  amount_max_cents: number | null;
  estimated_hours: number | null;
  crew_size: number | null;
  line_items: LineItem[];
  valid_until: string | null;
}

export interface IntakeResponse {
  lead_id: string;
  request: {
    id: string;
    move_date: string;
    is_date_flexible: boolean;
    home_size: HomeSize;
    packing_service: PackingService;
    special_items: string[];
    distance_miles: number | null;
    status: string;
  };
  quote: QuoteSummary | null;
}

export interface QuotePublic {
  company_name: string;
  status: "sent" | "accepted" | "declined" | "expired" | "draft" | string;
  currency: string;
  amount_min_cents: number;
  amount_max_cents: number;
  estimated_hours: number;
  crew_size: number;
  line_items: LineItem[];
  valid_until: string;
  move_date: string;
  origin_city: string;
  destination_city: string;
  home_size: string;
}

export interface AcceptQuoteResponse {
  booking_id: string;
  scheduled_date: string;
  crew_size: number;
  status: string;
  company_name: string;
}

export interface ApiErrorBody {
  error: { code: string; message: string };
}
