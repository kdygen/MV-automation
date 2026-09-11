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
  status: "sent" | "accepted" | "declined" | "expired" | "draft" | "superseded" | string;
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
  /** Which revision this is; > 1 after the customer edited their move. */
  revision: number;
  /** Deposit due to book, in cents. 0 means this company books without payment. */
  deposit_cents: number;
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

export interface ChatReply {
  reply: string;
  /** An allowlisted control to offer, or "none". Never a capability — just a name. */
  ui_action: string;
}

export interface DayAvailability {
  date: string;
  is_available: boolean;
  reason: string | null;
}

export interface AvailabilityResponse {
  days: DayAvailability[];
  current_move_date: string;
  first_bookable_date: string;
  last_bookable_date: string;
}

export interface QuoteSide {
  move_date: string;
  currency: string;
  amount_min_cents: number;
  amount_max_cents: number;
  estimated_hours: number;
  crew_size: number;
  line_items: LineItem[];
}

export interface ChangePreview {
  current: QuoteSide;
  proposed: QuoteSide;
  price_changed: boolean;
  difference_min_cents: number;
  difference_max_cents: number;
  proposed_valid_until: string;
}

export interface MoveDetails {
  move_date: string;
  is_date_flexible: boolean;
  home_size: string;
  packing_service: string;
  special_items: string[];
  origin_city: string;
  origin_floor: number;
  origin_has_elevator: boolean;
  origin_stairs_flights: number;
  destination_city: string;
  destination_floor: number;
  destination_has_elevator: boolean;
  destination_stairs_flights: number;
}

/** Access edits for one end of the move. */
export interface SideAccessPayload {
  floor?: number;
  has_elevator?: boolean;
  stairs_flights?: number;
}

/**
 * Customer edits. Only priced *inputs* — there is deliberately no field for an
 * amount, a crew size, or an hour count: the engine derives those.
 */
export interface EditMovePayload {
  move_date?: string;
  home_size?: HomeSize;
  packing_service?: PackingService;
  special_items?: string[];
  is_date_flexible?: boolean;
  origin?: SideAccessPayload;
  destination?: SideAccessPayload;
}

export interface CheckoutResponse {
  checkout_url: string;
  amount_cents: number;
  currency: string;
}

export interface PaymentStatusResponse {
  status: "none" | "pending" | "succeeded" | "failed" | "cancelled" | string;
  booking_confirmed: boolean;
  amount_cents: number | null;
  currency: string | null;
}
