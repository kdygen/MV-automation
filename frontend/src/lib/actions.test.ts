import { describe, expect, it } from "vitest";

import { UI_ACTIONS, acceptLabel, ctaFor, isUiAction, quoteActions } from "./actions";
import type { QuotePublic } from "./types";

const quote = (overrides: Partial<QuotePublic> = {}) =>
  ({ status: "sent", deposit_cents: 0, ...overrides }) as QuotePublic;

describe("the allowlist", () => {
  it("matches the backend enum exactly", () => {
    expect([...UI_ACTIONS]).toEqual([
      "none",
      "change_date",
      "edit_move",
      "accept_quote",
      "contact_company",
    ]);
  });

  it("has no payment action — the frontend decides that from config", () => {
    expect(UI_ACTIONS.some((a) => a.includes("pay"))).toBe(false);
  });

  it("rejects anything not on the list", () => {
    for (const bogus of ["", "refund", "delete_quote", "ACCEPT_QUOTE", 7, null, undefined, {}]) {
      expect(isUiAction(bogus)).toBe(false);
    }
  });
});

describe("rendering an assistant suggestion", () => {
  it("maps each action to its control", () => {
    expect(ctaFor("change_date", quote())).toMatchObject({ label: "Change date", flow: "date" });
    expect(ctaFor("edit_move", quote())).toMatchObject({ flow: "edit" });
    expect(ctaFor("accept_quote", quote())).toMatchObject({ flow: "accept" });
  });

  it("renders nothing for none", () => {
    expect(ctaFor("none", quote())).toBeNull();
  });

  it("renders nothing for an unknown action rather than guessing", () => {
    // A tampered or future value must degrade to a plain reply, not a broken page.
    expect(ctaFor("wire_me_money", quote())).toBeNull();
    expect(ctaFor(undefined, quote())).toBeNull();
  });

  it("offers no control once the quote is no longer live", () => {
    for (const status of ["accepted", "declined", "expired", "superseded", "draft"]) {
      expect(ctaFor("change_date", quote({ status }))).toBeNull();
      expect(ctaFor("accept_quote", quote({ status }))).toBeNull();
    }
  });

  it("contact_company offers no flow to open", () => {
    expect(ctaFor("contact_company", quote())?.flow).toBeNull();
  });
});

describe("the accept label", () => {
  it("says book when no deposit is due", () => {
    expect(acceptLabel({ deposit_cents: 0 })).toBe("Accept & book my move");
  });

  it("names the deposit when one is configured", () => {
    // The amount comes from the server-computed quote payload, never from the model.
    expect(acceptLabel({ deposit_cents: 20000 })).toContain("$200");
    expect(acceptLabel({ deposit_cents: 20000 })).toContain("deposit");
  });
});

describe("the permanent quote-page controls", () => {
  it("offers all three while the quote is live", () => {
    expect(quoteActions(quote()).map((a) => a.action)).toEqual([
      "change_date",
      "edit_move",
      "accept_quote",
    ]);
  });

  it("offers none once the quote is closed", () => {
    expect(quoteActions(quote({ status: "accepted" }))).toEqual([]);
  });

  it("uses the same label the assistant's button would", () => {
    const q = quote({ deposit_cents: 15000 });
    const permanent = quoteActions(q).find((a) => a.flow === "accept");
    expect(permanent?.label).toBe(ctaFor("accept_quote", q)?.label);
  });

  it("opens the same flows the assistant's buttons open", () => {
    const q = quote();
    for (const cta of quoteActions(q)) {
      expect(ctaFor(cta.action, q)?.flow).toBe(cta.flow);
    }
  });
});
