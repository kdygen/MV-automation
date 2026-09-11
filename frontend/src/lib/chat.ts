/**
 * Pure logic for the quote-page chat: message shape, draft validation, and the
 * mapping from an API failure to safe customer-facing copy.
 *
 * No React here, mirroring `quote-form.ts` — the component stays a thin rendering
 * layer and this module is unit-tested directly.
 */

import { ApiError } from "./api";

/** Matches the backend's own limit (`MAX_MESSAGE_LENGTH` in schemas/chat.py). */
export const MAX_CHAT_MESSAGE_LENGTH = 2000;

export type ChatRole = "assistant" | "user";

export interface ChatMessage {
  id: string;
  role: ChatRole;
  text: string;
  /**
   * An allowlisted control the assistant offered with this reply.
   *
   * Stored per message rather than on the panel so a button stays attached to the
   * answer that produced it, and an older suggestion does not linger beside a newer,
   * unrelated reply.
   */
  uiAction?: string;
}

/**
 * The opening line, rendered locally.
 *
 * Deliberately not a model call: the page must not spend money (or add latency)
 * just because someone opened their quote.
 */
export const GREETING =
  "Hi! I can help with questions about your quote, your move details, or the moving company.";

export const SUGGESTIONS = [
  "How much is my quote?",
  "Can I change my move date?",
  "What's your cancellation policy?",
] as const;

export function initialMessages(): ChatMessage[] {
  return [{ id: "greeting", role: "assistant", text: GREETING }];
}

let counter = 0;
export function nextMessageId(prefix: ChatRole): string {
  counter += 1;
  return `${prefix}-${counter}`;
}

/**
 * Why a draft cannot be sent, or null when it can.
 *
 * Client-side checks are for UX only — the backend re-validates and stays
 * authoritative. Over-long messages are refused rather than truncated, so a
 * customer is never silently answered on half a question.
 */
export function draftError(draft: string): string | null {
  if (!draft.trim()) return "Type a message first.";
  if (draft.length > MAX_CHAT_MESSAGE_LENGTH) {
    return `Messages are limited to ${MAX_CHAT_MESSAGE_LENGTH.toLocaleString()} characters.`;
  }
  return null;
}

/** Whether Send should be enabled. */
export function canSend(draft: string, isSending: boolean): boolean {
  return !isSending && draftError(draft) === null;
}

/**
 * Safe customer-facing text for a failed send.
 *
 * Copy is owned here rather than echoed from the response, so nothing
 * server-side — provider wording, internal codes, stack traces — can reach the
 * page through an error path.
 */
export function chatErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "network_error" || error.status === 0) {
      return "Couldn't reach the server. Check your connection and try again.";
    }
    switch (error.status) {
      case 429:
        return "You're sending messages too quickly. Please wait a moment and try again.";
      case 503:
        return "The chat assistant is temporarily unavailable. Your quote is still available above.";
      case 422:
        return "That message couldn't be sent. Try rephrasing your question about the move.";
      case 404:
        return "This quote link is no longer available.";
    }
  }
  return "Something went wrong sending your message. Please try again.";
}
