/**
 * The UI action contract, browser side.
 *
 * The assistant may name one of these; it can operate none of them. Every value maps
 * to a control that already exists on the quote page, so an action is a scroll-and-open
 * shortcut and never a separate code path — clicking the assistant's button and
 * clicking the permanent one run exactly the same handler.
 *
 * `isUiAction` is the allowlist guard: anything unrecognised renders nothing at all,
 * so a tampered or future value degrades to a plain reply rather than a broken page.
 */

import type { QuotePublic } from "./types";
import { dollars } from "./format";

/** Must match `UiAction` in app/agent/actions.py. */
export const UI_ACTIONS = [
  "none",
  "change_date",
  "edit_move",
  "accept_quote",
  "contact_company",
] as const;

export type UiAction = (typeof UI_ACTIONS)[number];

/** Which flows a quote-page control can open. `contact_company` opens none. */
export type FlowKey = "date" | "edit" | "accept";

export function isUiAction(value: unknown): value is UiAction {
  return typeof value === "string" && (UI_ACTIONS as readonly string[]).includes(value);
}

/**
 * Label for the accept control.
 *
 * The deposit comes from the quote payload, which the backend computed — the model is
 * never asked whether money is owed, and never states an amount.
 */
export function acceptLabel(quote: Pick<QuotePublic, "deposit_cents">): string {
  return quote.deposit_cents > 0
    ? `Accept & pay ${dollars(quote.deposit_cents)} deposit`
    : "Accept & book my move";
}

export interface ActionCta {
  action: UiAction;
  label: string;
  flow: FlowKey | null;
}

/**
 * The button to render for an action, or null for none.
 *
 * Returns null once the quote is no longer live: an assistant hint that arrives after
 * the customer accepted or the quote expired must not offer a control that would fail.
 */
export function ctaFor(
  action: unknown,
  quote: Pick<QuotePublic, "deposit_cents" | "status">,
): ActionCta | null {
  if (!isUiAction(action) || action === "none") return null;
  if (quote.status !== "sent") return null;

  switch (action) {
    case "change_date":
      return { action, label: "Change date", flow: "date" };
    case "edit_move":
      return { action, label: "Edit move details", flow: "edit" };
    case "accept_quote":
      return { action, label: acceptLabel(quote), flow: "accept" };
    case "contact_company":
      return { action, label: "Contact the company", flow: null };
  }
}

/** The permanent controls shown on the quote page, in order. */
export function quoteActions(
  quote: Pick<QuotePublic, "deposit_cents" | "status">,
): ActionCta[] {
  if (quote.status !== "sent") return [];
  return [
    { action: "change_date", label: "Change date", flow: "date" },
    { action: "edit_move", label: "Edit move details", flow: "edit" },
    { action: "accept_quote", label: acceptLabel(quote), flow: "accept" },
  ];
}
