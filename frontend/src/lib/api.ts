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
  AvailabilityResponse,
  ChangePreview,
  ChatReply,
  CheckoutResponse,
  EditMovePayload,
  IntakeResponse,
  MoveDetails,
  MovingRequestIn,
  PaymentStatusResponse,
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

/**
 * Send one message to the post-quote assistant.
 *
 * The body carries the message and nothing else — scope comes from the token in the
 * URL, which the backend resolves server-side. No identifiers, model names, or
 * credentials are ever sent from the browser.
 */
export function sendQuoteChatMessage(token: string, message: string): Promise<ChatReply> {
  return request<ChatReply>(`/public/quotes/${encodeURIComponent(token)}/chat`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

/** Which dates this company can take the move on. Read-only. */
export function getAvailability(
  token: string,
  from: string,
  to: string,
): Promise<AvailabilityResponse> {
  const query = new URLSearchParams({ from, to });
  return request<AvailabilityResponse>(
    `/public/quotes/${encodeURIComponent(token)}/availability?${query}`,
  );
}

/** The priced inputs behind the quote, for the edit form. */
export function getMoveDetails(token: string): Promise<MoveDetails> {
  return request<MoveDetails>(`/public/quotes/${encodeURIComponent(token)}/move-details`);
}

/** Price a proposed change. Writes nothing — safe to call as the customer explores. */
export function previewDateChange(token: string, moveDate: string): Promise<ChangePreview> {
  return request<ChangePreview>(`/public/quotes/${encodeURIComponent(token)}/date-preview`, {
    method: "POST",
    body: JSON.stringify({ move_date: moveDate }),
  });
}

export function previewEdit(token: string, edits: EditMovePayload): Promise<ChangePreview> {
  return request<ChangePreview>(`/public/quotes/${encodeURIComponent(token)}/edit-preview`, {
    method: "POST",
    body: JSON.stringify(edits),
  });
}

/** Persist a change the customer has explicitly confirmed. */
export function confirmDateChange(token: string, moveDate: string): Promise<QuotePublic> {
  return request<QuotePublic>(`/public/quotes/${encodeURIComponent(token)}/date-change`, {
    method: "POST",
    body: JSON.stringify({ move_date: moveDate }),
  });
}

export function confirmEdit(token: string, edits: EditMovePayload): Promise<QuotePublic> {
  return request<QuotePublic>(`/public/quotes/${encodeURIComponent(token)}/edit`, {
    method: "POST",
    body: JSON.stringify(edits),
  });
}

/**
 * Begin hosted checkout. Deliberately sends no body: the amount is computed by the
 * backend from the quote, and nothing the browser could send would be trusted.
 */
export function startCheckout(token: string): Promise<CheckoutResponse> {
  return request<CheckoutResponse>(`/public/quotes/${encodeURIComponent(token)}/checkout`, {
    method: "POST",
  });
}

/** Polled by the return page. Only a verified webhook can make this confirmed. */
export function getPaymentStatus(token: string): Promise<PaymentStatusResponse> {
  return request<PaymentStatusResponse>(
    `/public/quotes/${encodeURIComponent(token)}/payment-status`,
  );
}
