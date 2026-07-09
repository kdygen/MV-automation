"use client";

/** Quotes list with status tabs; "Needs review" is the draft-approval queue. */

import Link from "next/link";
import { useEffect, useState } from "react";

import { listQuotes } from "@/lib/dashboard-api";
import type { QuoteRow } from "@/lib/dashboard-types";
import { dollarRange, longDate } from "@/lib/format";
import { EmptyState, Loading, PageHeader, StatusBadge, Table, Td } from "@/components/dashboard";

const TABS = [
  { key: "all", label: "All" },
  { key: "draft", label: "Needs review" },
  { key: "sent", label: "Sent" },
  { key: "accepted", label: "Accepted" },
  { key: "declined", label: "Declined" },
  { key: "expired", label: "Expired" },
] as const;

export default function QuotesPage() {
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("all");
  const [quotes, setQuotes] = useState<QuoteRow[] | null>(null);

  useEffect(() => {
    listQuotes(tab === "all" ? undefined : tab)
      .then(setQuotes)
      .catch(() => setQuotes([]));
  }, [tab]);

  return (
    <div>
      <PageHeader title="Quotes" />
      <div className="mb-4 flex flex-wrap gap-2">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => {
              setQuotes(null);
              setTab(t.key);
            }}
            className={`rounded-full px-3 py-1 text-xs font-medium ${
              tab === t.key
                ? "bg-blue-600 text-white"
                : "border border-slate-200 bg-white text-slate-600"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {!quotes ? (
        <Loading />
      ) : quotes.length === 0 ? (
        <EmptyState
          message={
            tab === "draft" ? "No quotes waiting for review. 🎉" : "No quotes match this filter."
          }
        />
      ) : (
        <Table headers={["Customer", "Range", "Home", "Move date", "Status", "Created"]}>
          {quotes.map((q) => (
            <tr key={q.id} className="hover:bg-slate-50">
              <Td>
                <Link href={`/dashboard/quotes/${q.id}`} className="font-medium text-blue-700">
                  {q.lead_name}
                </Link>
              </Td>
              <Td>
                {dollarRange(q.amount_min_cents, q.amount_max_cents)}
                {q.is_adjusted ? <span className="ml-1 text-xs text-amber-600">adj.</span> : null}
              </Td>
              <Td>{q.home_size}</Td>
              <Td>{longDate(q.move_date)}</Td>
              <Td>
                <StatusBadge status={q.status} />
              </Td>
              <Td>{longDate(q.created_at)}</Td>
            </tr>
          ))}
        </Table>
      )}
    </div>
  );
}
