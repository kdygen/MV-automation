"use client";

/**
 * Customer quote page (`/quote/{token}`): the tokenized link from the quote email.
 *
 * Shows the estimate and the deterministic controls that can change it — change date,
 * edit details, accept. Every one of those calls a backend endpoint that re-validates
 * from scratch; nothing on this page decides a price, a date's availability, or
 * whether a payment succeeded.
 *
 * The assistant can open the same panels via `ui_action`, but it opens *these* panels.
 * There is no AI-specific business logic anywhere on this page.
 */

import { use, useCallback, useEffect, useState } from "react";

import { ApiError, acceptQuote, declineQuote, getQuote, startCheckout } from "@/lib/api";
import { dollarRange, dollars, longDate } from "@/lib/format";
import { type FlowKey, quoteActions } from "@/lib/actions";
import type { AcceptQuoteResponse, QuotePublic } from "@/lib/types";
import { Button, Card, ErrorBanner } from "@/components/ui";
import { ChangeDatePanel, EditMovePanel } from "@/components/quote-change";
import { ChatPanel } from "@/components/chat";

export default function QuotePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const [quote, setQuote] = useState<QuotePublic | null>(null);
  const [booking, setBooking] = useState<AcceptQuoteResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [flow, setFlow] = useState<FlowKey | null>(null);

  useEffect(() => {
    getQuote(token)
      .then(setQuote)
      .catch((err) =>
        setLoadError(
          err instanceof ApiError && err.code === "not_found"
            ? "This quote link is invalid or no longer available."
            : "Could not load your quote. Please try again.",
        ),
      );
  }, [token]);

  /**
   * Accept, or start checkout when the company takes a deposit.
   *
   * Which of the two happens is decided by `deposit_cents` on the quote payload — a
   * server-computed value. The browser never chooses whether payment applies, and
   * never sends an amount.
   */
  const accept = useCallback(async () => {
    setBusy(true);
    setActionError(null);
    try {
      if (quote && quote.deposit_cents > 0) {
        const { checkout_url: url } = await startCheckout(token);
        window.location.href = url;
        return;
      }
      setBooking(await acceptQuote(token));
      setQuote((q) => (q ? { ...q, status: "accepted" } : q));
    } catch (err) {
      setActionError(
        err instanceof ApiError ? err.message : "Something went wrong. Please try again.",
      );
      getQuote(token).then(setQuote).catch(() => {});
    } finally {
      setBusy(false);
    }
  }, [token, quote]);

  const decline = useCallback(async () => {
    setBusy(true);
    setActionError(null);
    try {
      setQuote(await declineQuote(token));
    } catch (err) {
      setActionError(
        err instanceof ApiError ? err.message : "Something went wrong. Please try again.",
      );
      getQuote(token).then(setQuote).catch(() => {});
    } finally {
      setBusy(false);
    }
  }, [token]);

  /** A change was confirmed: the response is already the new revision. */
  const onChanged = useCallback((updated: QuotePublic) => {
    setQuote(updated);
    setFlow(null);
    setActionError(null);
  }, []);

  const openFlow = useCallback((next: FlowKey | null) => {
    if (next === "accept") return; // handled by the accept button itself
    setFlow(next);
    if (next && typeof document !== "undefined") {
      document.getElementById("quote-actions")?.scrollIntoView({ behavior: "smooth" });
    }
  }, []);

  if (loadError) {
    return (
      <main className="mx-auto max-w-xl px-4 py-16">
        <ErrorBanner message={loadError} />
      </main>
    );
  }
  if (!quote) {
    return (
      <main className="mx-auto max-w-xl px-4 py-16 text-center text-slate-500">
        Loading your quote…
      </main>
    );
  }

  const isLive = quote.status === "sent";
  const actions = quoteActions(quote);

  return (
    <main className="mx-auto max-w-xl px-4 py-10">
      <p className="mb-1 text-sm font-medium uppercase tracking-wide text-blue-600">
        {quote.company_name}
      </p>
      <h1 className="mb-6 text-2xl font-bold text-slate-900">Your moving quote</h1>

      <Card>
        <div className="text-center">
          <p className="text-4xl font-bold text-slate-900">
            {dollarRange(quote.amount_min_cents, quote.amount_max_cents)}
          </p>
          <p className="mt-2 text-sm text-slate-600">
            {quote.crew_size} movers · about {quote.estimated_hours} hours
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {quote.origin_city} → {quote.destination_city} on {longDate(quote.move_date)}
          </p>
          {quote.revision > 1 ? (
            <p className="mt-1 text-xs text-slate-400">Updated estimate (v{quote.revision})</p>
          ) : null}
        </div>

        <dl className="mt-6 divide-y divide-slate-100 border-t border-slate-100">
          {quote.line_items.map((item) => (
            <div key={item.code} className="flex justify-between py-2 text-sm">
              <dt className="text-slate-600">{item.label}</dt>
              <dd className="font-medium text-slate-900">{dollars(item.amount_cents)}</dd>
            </div>
          ))}
        </dl>

        <p className="mt-4 text-xs text-slate-500">
          Non-binding estimate · valid until {longDate(quote.valid_until)}
        </p>

        {actionError ? (
          <div className="mt-4">
            <ErrorBanner message={actionError} />
          </div>
        ) : null}

        <div className="mt-6">
          {booking || quote.status === "accepted" ? (
            <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-4 text-center">
              <p className="font-semibold text-green-800">Booking confirmed 🎉</p>
              <p className="mt-1 text-sm text-green-700">
                {booking
                  ? `Your move is booked for ${longDate(booking.scheduled_date)} with a crew of ${booking.crew_size}.`
                  : "This quote has been accepted and your move is booked."}
              </p>
            </div>
          ) : quote.status === "declined" ? (
            <p className="text-center text-sm text-slate-600">
              You declined this quote. Changed your mind? Contact {quote.company_name} directly.
            </p>
          ) : quote.status === "expired" ? (
            <p className="text-center text-sm text-slate-600">
              This quote has expired. Please submit a new request for updated pricing.
            </p>
          ) : (
            <div id="quote-actions" className="space-y-3">
              <div className="flex flex-wrap justify-center gap-2">
                {actions
                  .filter((cta) => cta.flow !== "accept")
                  .map((cta) => (
                    <Button
                      key={cta.action}
                      variant="secondary"
                      onClick={() => openFlow(cta.flow)}
                      disabled={busy}
                    >
                      {cta.label}
                    </Button>
                  ))}
              </div>
              <div className="flex flex-wrap justify-center gap-3">
                <Button onClick={accept} disabled={busy}>
                  {busy
                    ? "Working…"
                    : actions.find((a) => a.flow === "accept")?.label ?? "Accept"}
                </Button>
                <Button variant="danger" onClick={decline} disabled={busy}>
                  Decline
                </Button>
              </div>
              {quote.deposit_cents > 0 ? (
                <p className="text-center text-xs text-slate-500">
                  A {dollars(quote.deposit_cents)} deposit holds your date. You&apos;ll pay
                  securely on the next screen.
                </p>
              ) : null}
            </div>
          )}
        </div>
      </Card>

      {isLive && flow === "date" ? (
        <div className="mt-6">
          <ChangeDatePanel
            token={token}
            quote={quote}
            onDone={onChanged}
            onClose={() => setFlow(null)}
          />
        </div>
      ) : null}

      {isLive && flow === "edit" ? (
        <div className="mt-6">
          <EditMovePanel token={token} onDone={onChanged} onClose={() => setFlow(null)} />
        </div>
      ) : null}

      <ChatPanel token={token} quote={quote} onAction={openFlow} onAccept={accept} />
    </main>
  );
}
