import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import {
  GREETING,
  MAX_CHAT_MESSAGE_LENGTH,
  SUGGESTIONS,
  canSend,
  chatErrorMessage,
  draftError,
  initialMessages,
} from "./chat";

describe("initial state", () => {
  it("opens with a local greeting and no user messages", () => {
    const messages = initialMessages();
    expect(messages).toHaveLength(1);
    expect(messages[0].role).toBe("assistant");
    expect(messages[0].text).toBe(GREETING);
  });

  it("offers a few starter questions", () => {
    expect(SUGGESTIONS.length).toBeGreaterThan(0);
    expect(SUGGESTIONS.length).toBeLessThanOrEqual(3);
  });
});

describe("draft validation", () => {
  it("rejects blank and whitespace-only messages", () => {
    for (const blank of ["", "   ", "\n\t "]) {
      expect(draftError(blank)).not.toBeNull();
      expect(canSend(blank, false)).toBe(false);
    }
  });

  it("accepts a normal message", () => {
    expect(draftError("How much is my quote?")).toBeNull();
    expect(canSend("How much is my quote?", false)).toBe(true);
  });

  it("matches the backend limit and rejects rather than truncates", () => {
    expect(MAX_CHAT_MESSAGE_LENGTH).toBe(2000);
    const atLimit = "x".repeat(MAX_CHAT_MESSAGE_LENGTH);
    const overLimit = "x".repeat(MAX_CHAT_MESSAGE_LENGTH + 1);

    expect(draftError(atLimit)).toBeNull();
    expect(draftError(overLimit)).toContain("2,000");
    expect(canSend(overLimit, false)).toBe(false);
  });

  it("blocks a second send while one is in flight", () => {
    expect(canSend("valid message", true)).toBe(false);
  });
});

describe("error copy", () => {
  const apiError = (status: number, code = "err") =>
    new ApiError(code, "internal detail that must not be shown", status);

  it("explains rate limiting", () => {
    expect(chatErrorMessage(apiError(429))).toMatch(/too quickly/i);
  });

  it("explains an unavailable assistant and reassures about the quote", () => {
    const message = chatErrorMessage(apiError(503));
    expect(message).toMatch(/temporarily unavailable/i);
    expect(message).toMatch(/quote is still available/i);
  });

  it("handles validation, missing quote, and network failure", () => {
    expect(chatErrorMessage(apiError(422))).toMatch(/rephrasing/i);
    expect(chatErrorMessage(apiError(404))).toMatch(/no longer available/i);
    expect(chatErrorMessage(new ApiError("network_error", "boom", 0))).toMatch(/connection/i);
  });

  it("falls back to a generic message for anything else", () => {
    expect(chatErrorMessage(apiError(500))).toMatch(/something went wrong/i);
    expect(chatErrorMessage(new Error("kaboom"))).toMatch(/something went wrong/i);
  });

  it("never surfaces server-side detail to the customer", () => {
    const leaky = new ApiError("upstream_error", "openai timeout sk-abc123 Traceback", 502);
    const shown = chatErrorMessage(leaky);
    for (const secret of ["openai", "sk-abc123", "Traceback", "upstream_error"]) {
      expect(shown).not.toContain(secret);
    }
  });
});
