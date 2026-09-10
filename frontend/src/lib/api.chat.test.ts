import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, sendQuoteChatMessage } from "./api";

function stubFetch(response: { ok: boolean; status?: number; body?: unknown }) {
  // Parameters are typed (and referenced) so `spy.mock.calls` is inspectable
  // rather than inferred as `[]`.
  const spy = vi.fn(async (url: string, init?: RequestInit) => ({
    ok: response.ok,
    status: response.status ?? 200,
    url,
    method: init?.method,
    json: async () => response.body ?? {},
  }));
  vi.stubGlobal("fetch", spy);
  return spy;
}

afterEach(() => vi.unstubAllGlobals());

describe("sendQuoteChatMessage", () => {
  it("posts only the message to the token's chat endpoint", async () => {
    const spy = stubFetch({ ok: true, body: { reply: "Your estimate is $1,734–$2,206." } });

    const result = await sendQuoteChatMessage("tok-123", "How much is my quote?");

    expect(result.reply).toBe("Your estimate is $1,734–$2,206.");
    const [url, init] = spy.mock.calls[0];
    if (!init) throw new Error("fetch called without init");
    expect(url).toMatch(/\/api\/v1\/public\/quotes\/tok-123\/chat$/);
    expect(init.method).toBe("POST");

    // The body must carry the message and nothing else — no identifiers ever.
    const body = JSON.parse(init.body as string);
    expect(Object.keys(body)).toEqual(["message"]);
    expect(body.message).toBe("How much is my quote?");
    for (const forbidden of ["company_id", "quote_id", "conversation_id", "lead_id"]) {
      expect(body).not.toHaveProperty(forbidden);
    }
  });

  it("url-encodes the token", async () => {
    const spy = stubFetch({ ok: true, body: { reply: "hi" } });
    await sendQuoteChatMessage("a b/c", "hello");
    expect(spy.mock.calls[0][0]).toContain("a%20b%2Fc");
  });

  it("surfaces backend failures as ApiError with the status", async () => {
    stubFetch({
      ok: false,
      status: 429,
      body: { error: { code: "rate_limited", message: "slow down" } },
    });

    await expect(sendQuoteChatMessage("tok", "hi")).rejects.toMatchObject({
      name: "ApiError",
      status: 429,
      code: "rate_limited",
    });
  });

  it("turns a network failure into a network ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline"); }));
    await expect(sendQuoteChatMessage("tok", "hi")).rejects.toBeInstanceOf(ApiError);
  });
});
