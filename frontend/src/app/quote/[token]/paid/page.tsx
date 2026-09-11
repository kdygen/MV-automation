"use client";

/**
 * Where Stripe returns the customer after checkout.
 *
 * Arriving here proves nothing. The page polls `payment-status`, which reports a
 * booking that only a signature-verified webhook can create — so a customer who edits
 * the URL, hits Back, or replays the redirect sees "still processing", never a
 * confirmation. Nothing on this page writes anything.
 */

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { getPaymentStatus } from "@/lib/api";
import type { PaymentStatusResponse } from "@/lib/types";
import { Card, ErrorBanner } from "@/components/ui";

/** Webhooks normally land in a second or two; give up politely well after that. */
const POLL_INTERVAL_MS = 2000;
const MAX_POLLS = 20;

export default function PaidPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const [status, setStatus] = useState<PaymentStatusResponse | null>(null);
  const [attempts, setAttempts] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const settled = status?.booking_confirmed || ["failed", "cancelled"].includes(status?.status ?? "");
  const givenUp = !settled && attempts >= MAX_POLLS;

  const poll = useCallback(async () => {
    try {
      setStatus(await getPaymentStatus(token));
    } catch {
      setError("Couldn't check your payment. Your card has not been charged twice — refresh to retry.");
    } finally {
      setAttempts((n) => n + 1);
    }
  }, [token]);

  useEffect(() => {
    if (settled || givenUp) return;
    const timer = setTimeout(poll, attempts === 0 ? 0 : POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [poll, attempts, settled, givenUp]);

  return (
    <main className="mx-auto max-w-xl px-4 py-16">
      <Card>
        {error ? <ErrorBanner message={error} /> : null}

        {status?.booking_confirmed ? (
          <div className="text-center">
            <p className="text-2xl font-bold text-green-700">Booking confirmed 🎉</p>
            <p className="mt-2 text-sm text-slate-600">
              Thanks — your deposit is received and your move is booked. The company will be in
              touch to confirm the details.
            </p>
          </div>
        ) : status && ["failed", "cancelled"].includes(status.status) ? (
          <div className="text-center">
            <p className="text-lg font-semibold text-slate-900">Payment didn&apos;t go through</p>
            <p className="mt-2 text-sm text-slate-600">
              Your move hasn&apos;t been booked and you haven&apos;t been charged. You can try
              again from your quote.
            </p>
          </div>
        ) : givenUp ? (
          <div className="text-center">
            <p className="text-lg font-semibold text-slate-900">Still confirming your payment</p>
            <p className="mt-2 text-sm text-slate-600">
              This is taking longer than usual. If your card was charged, your booking will be
              confirmed shortly — refresh this page in a minute.
            </p>
          </div>
        ) : (
          <div className="text-center">
            <p className="text-lg font-semibold text-slate-900">Confirming your payment…</p>
            <p className="mt-2 text-sm text-slate-600">
              Hang tight — this usually takes a few seconds. Don&apos;t close this page.
            </p>
          </div>
        )}

        <p className="mt-6 text-center text-sm">
          <Link href={`/quote/${token}`} className="text-blue-600 hover:text-blue-800">
            Back to your quote
          </Link>
        </p>
      </Card>
    </main>
  );
}
