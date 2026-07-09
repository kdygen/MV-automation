"use client";

/**
 * Quote detail: full breakdown, audit info, and — for drafts — the approve/adjust
 * action that releases the price to the customer.
 */

import { use, useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import { approveQuote, getQuoteAdmin } from "@/lib/dashboard-api";
import type { QuoteAdmin } from "@/lib/dashboard-types";
import { dollarRange, dollars, longDate } from "@/lib/format";
import { EmptyState, Loading, PageHeader, StatusBadge } from "@/components/dashboard";
import { Button, Card, ErrorBanner, Field, TextInput } from "@/components/ui";

export default function QuoteDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [quote, setQuote] = useState<QuoteAdmin | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [minDollars, setMinDollars] = useState("");
  const [maxDollars, setMaxDollars] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    getQuoteAdmin(id)
      .then((q) => {
        setQuote(q);
        setMinDollars(String(q.amount_min_cents / 100));
        setMaxDollars(String(q.amount_max_cents / 100));
      })
      .catch(() => setNotFound(true));
  }, [id]);

  if (notFound) return <EmptyState message="Quote not found." />;
  if (!quote) return <Loading />;

  const approve = async (withAdjustment: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const adjustment = withAdjustment
        ? { amount_min_dollars: Number(minDollars), amount_max_dollars: Number(maxDollars) }
        : undefined;
      setQuote(await approveQuote(quote.id, adjustment));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Approval failed. Try again.");
    } finally {
      setBusy(false);
    }
  };

  const quoteUrl = `${window.location.origin}/quote/${quote.public_token}`;

  return (
    <div className="max-w-2xl">
      <PageHeader title={`Quote — ${dollarRange(quote.amount_min_cents, quote.amount_max_cents)}`}>
        <StatusBadge status={quote.status} />
      </PageHeader>

      <Card>
        <dl className="space-y-1 text-sm">
          <div className="flex justify-between">
            <dt className="text-slate-500">Estimate</dt>
            <dd className="text-slate-900">
              {quote.crew_size} movers · {quote.estimated_hours}h · total{" "}
              {dollars(quote.total_cents)}
            </dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-slate-500">Engine</dt>
            <dd className="text-slate-900">
              {quote.engine_version}
              {quote.is_adjusted ? " · manually adjusted" : ""}
            </dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-slate-500">Valid until</dt>
            <dd className="text-slate-900">{longDate(quote.valid_until)}</dd>
          </div>
        </dl>

        <h2 className="mb-2 mt-5 text-sm font-semibold uppercase tracking-wide text-slate-500">
          Line items
        </h2>
        <dl className="divide-y divide-slate-100 border-t border-slate-100">
          {quote.line_items.map((item) => (
            <div key={item.code} className="flex justify-between py-2 text-sm">
              <dt className="text-slate-600">{item.label}</dt>
              <dd className="font-medium text-slate-900">{dollars(item.amount_cents)}</dd>
            </div>
          ))}
        </dl>

        <div className="mt-5 flex items-center gap-2">
          <span className="truncate rounded bg-slate-100 px-2 py-1 text-xs text-slate-600">
            {quoteUrl}
          </span>
          <button
            className="text-xs font-medium text-blue-700"
            onClick={() => {
              navigator.clipboard.writeText(quoteUrl);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}
          >
            {copied ? "Copied!" : "Copy link"}
          </button>
        </div>
      </Card>

      {quote.status === "draft" ? (
        <div className="mt-6">
          <Card>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
              Review &amp; approve
            </h2>
            <p className="mb-4 text-sm text-slate-600">
              The customer hasn&apos;t seen a price yet. Approve as-is, or adjust the range
              first — your adjustment is recorded.
            </p>
            {error ? (
              <div className="mb-4">
                <ErrorBanner message={error} />
              </div>
            ) : null}
            <div className="mb-4 grid grid-cols-2 gap-4">
              <Field label="Min ($)">
                <TextInput
                  type="number"
                  min={1}
                  value={minDollars}
                  onChange={(e) => setMinDollars(e.target.value)}
                />
              </Field>
              <Field label="Max ($)">
                <TextInput
                  type="number"
                  min={1}
                  value={maxDollars}
                  onChange={(e) => setMaxDollars(e.target.value)}
                />
              </Field>
            </div>
            <div className="flex gap-3">
              <Button onClick={() => approve(true)} disabled={busy}>
                {busy ? "Working…" : "Approve with adjusted range"}
              </Button>
              <Button variant="secondary" onClick={() => approve(false)} disabled={busy}>
                Approve as calculated
              </Button>
            </div>
          </Card>
        </div>
      ) : null}
    </div>
  );
}
