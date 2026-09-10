/**
 * Authenticated API client for the company dashboard.
 *
 * The bearer token comes from Supabase Auth in production or the dev-token flow
 * locally; either way it lives in localStorage under `mv_dashboard_token`. A 401/403
 * from the backend raises `AuthRequiredError`, which the dashboard layout turns into a
 * redirect to the login page.
 */

import { ApiError } from "./api";
import type {
  AccuracySummary,
  BookingRow,
  CompanySettings,
  CompleteBookingPayload,
  ImportResult,
  JobRow,
  KnowledgeEntry,
  KnowledgeEntryPatch,
  KnowledgeEntryPayload,
  LeadDetail,
  LeadRow,
  Me,
  PricingConfigDoc,
  PricingSettings,
  QuoteAdmin,
  QuoteRow,
  StarterTopic,
} from "./dashboard-types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

const TOKEN_KEY = "mv_dashboard_token";

export class AuthRequiredError extends Error {
  constructor() {
    super("Sign in required");
    this.name = "AuthRequiredError";
  }
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
}

async function authed<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  if (!token) throw new AuthRequiredError();

  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api/v1${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        ...init?.headers,
      },
    });
  } catch {
    throw new ApiError("network_error", "Could not reach the server.", 0);
  }

  if (response.status === 401) throw new AuthRequiredError();
  if (!response.ok) {
    let code = "unknown_error";
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body?.error) {
        code = body.error.code;
        message = body.error.message;
      }
    } catch {
      /* keep defaults */
    }
    throw new ApiError(code, message, response.status);
  }
  // 204 has no body; parsing it would throw. Callers of such endpoints use Promise<void>.
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const fetchMe = () => authed<Me>("/me");

export const listLeads = (status?: string) =>
  authed<LeadRow[]>(`/leads${status ? `?status=${status}` : ""}`);
export const getLead = (id: string) => authed<LeadDetail>(`/leads/${id}`);

export const listQuotes = (status?: string) =>
  authed<QuoteRow[]>(`/quotes${status ? `?status=${status}` : ""}`);
export const getQuoteAdmin = (id: string) => authed<QuoteAdmin>(`/quotes/${id}`);
export const approveQuote = (
  id: string,
  adjustment?: { amount_min_dollars?: number; amount_max_dollars?: number },
) =>
  authed<QuoteAdmin>(`/quotes/${id}/approve`, {
    method: "POST",
    body: JSON.stringify(adjustment ?? {}),
  });

export const listBookings = (status?: string) =>
  authed<BookingRow[]>(`/bookings${status ? `?status=${status}` : ""}`);

export const getCompanySettings = () => authed<CompanySettings>("/settings/company");
export const patchCompanySettings = (patch: Partial<CompanySettings>) =>
  authed<CompanySettings>("/settings/company", {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const getPricingSettings = () => authed<PricingSettings>("/settings/pricing");
export const putPricingSettings = (config: PricingConfigDoc) =>
  authed<PricingSettings>("/settings/pricing", {
    method: "PUT",
    body: JSON.stringify({ config }),
  });

export const completeBooking = (id: string, payload: CompleteBookingPayload) =>
  authed<JobRow>(`/bookings/${id}/complete`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const listJobs = () => authed<JobRow[]>("/jobs");
export const getAccuracy = () => authed<AccuracySummary>("/jobs/accuracy");

/** Multipart upload — bypasses the JSON `authed` helper. */
export async function importJobsCsv(file: File): Promise<ImportResult> {
  const token = getToken();
  if (!token) throw new AuthRequiredError();
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${API_BASE}/api/v1/jobs/import`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (response.status === 401) throw new AuthRequiredError();
  if (!response.ok) {
    let message = `Import failed (${response.status})`;
    let code = "unknown_error";
    try {
      const body = await response.json();
      if (body?.error) {
        code = body.error.code;
        message = body.error.message;
      }
    } catch {
      /* keep defaults */
    }
    throw new ApiError(code, message, response.status);
  }
  return (await response.json()) as ImportResult;
}

export const listKnowledge = () => authed<KnowledgeEntry[]>("/knowledge");
export const listStarterTopics = () => authed<StarterTopic[]>("/knowledge/starters");

export const createKnowledge = (payload: KnowledgeEntryPayload) =>
  authed<KnowledgeEntry>("/knowledge", { method: "POST", body: JSON.stringify(payload) });

export const updateKnowledge = (id: string, patch: KnowledgeEntryPatch) =>
  authed<KnowledgeEntry>(`/knowledge/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const deleteKnowledge = (id: string) =>
  authed<void>(`/knowledge/${id}`, { method: "DELETE" });
