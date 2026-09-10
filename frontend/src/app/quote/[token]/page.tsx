"use client";

/**
 * Customer quote page (`/quote/{token}`): the tokenized link from the quote email.
 * Shows the estimate breakdown and lets the customer accept (creating a booking) or
 * decline. Handles expired/declined/already-accepted states.
 */

import { use, useCallback, useEffect, useState } from "react";

import { ApiError, acceptQuote, declineQuote, getQuote } from "@/lib/api";
import { dollarRange, dollars, longDate } from "@/lib/format";
import type { AcceptQuoteResponse, QuotePublic } from "@/lib/types";
import { Button, Card, ErrorBanner } from "@/components/ui";
import { ChatPanel } from "@/components/chat";

export default function QuotePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const [quote, setQuote] = useState<QuotePublic | null>(null);
  const [booking, setBooking] = useState<AcceptQuoteResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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

  const act = useCallback(
    async (action: "accept" | "decline") => {
      setBusy(true);
      setActionError(null);
      try {
        if (action === "accept") {
          setBooking(await acceptQuote(token));
          setQuote((q) => (q ? { ...q, status: "accepted" } : q));
        } else {
          setQuote(await declineQuote(token));
        }
      } catch (err) {
        setActionError(
          err instanceof ApiError ? err.message : "Something went wrong. Please try again.",
        );
        // Status may have changed server-side (e.g. expired) — refresh.
        getQuote(token).then(setQuote).catch(() => {});
      } finally {
        setBusy(false);
      }
    },
    [token],
  );

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
            <div className="flex justify-center gap-3">
              <Button onClick={() => act("accept")} disabled={busy}>
                {busy ? "Working…" : "Accept & book my move"}
              </Button>
              <Button variant="danger" onClick={() => act("decline")} disabled={busy}>
                Decline
              </Button>
            </div>
          )}
        </div>
      </Card>

      <ChatPanel token={token} />
    </main>
  );
}
