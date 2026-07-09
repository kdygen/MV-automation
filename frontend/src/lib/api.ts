/**
 * Minimal typed API client for the MV Automation backend.
 *
 * All calls go to `NEXT_PUBLIC_API_URL` (defaults to the local backend). Errors are
 * normalized to `ApiError` carrying the backend's stable error code so UI code can
 * branch on `err.code` instead of parsing messages.
 */

import type {
  AcceptQuoteResponse,
  ApiErrorBody,
  IntakeResponse,
  MovingRequestIn,
  QuotePublic,
} from "./types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api/v1${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError("network_error", "Could not reach the server. Please try again.", 0);
  }

  if (!response.ok) {
    let code = "unknown_error";
    let message = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as ApiErrorBody;
      if (body?.error) {
        code = body.error.code;
        message = body.error.message;
      }
    } catch {
      // non-JSON error body; keep defaults
    }
    throw new ApiError(code, message, response.status);
  }
  return (await response.json()) as T;
}

export function submitMovingRequest(
  companySlug: string,
  payload: MovingRequestIn,
): Promise<IntakeResponse> {
  return request<IntakeResponse>(`/public/${encodeURIComponent(companySlug)}/requests`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getQuote(token: string): Promise<QuotePublic> {
  return request<QuotePublic>(`/public/quotes/${encodeURIComponent(token)}`);
}

export function acceptQuote(token: string): Promise<AcceptQuoteResponse> {
  return request<AcceptQuoteResponse>(`/public/quotes/${encodeURIComponent(token)}/accept`, {
    method: "POST",
  });
}

export function declineQuote(token: string): Promise<QuotePublic> {
  return request<QuotePublic>(`/public/quotes/${encodeURIComponent(token)}/decline`, {
    method: "POST",
  });
}
