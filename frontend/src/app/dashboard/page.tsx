"use client";

/** Overview: headline counts + the most recent leads. */

import Link from "next/link";
import { useEffect, useState } from "react";

import { listBookings, listLeads, listQuotes } from "@/lib/dashboard-api";
import type { BookingRow, LeadRow, QuoteRow } from "@/lib/dashboard-types";
import { dollarRange, longDate } from "@/lib/format";
import { EmptyState, Loading, PageHeader, StatCard, StatusBadge, Table, Td } from "@/components/dashboard";

export default function OverviewPage() {
  const [leads, setLeads] = useState<LeadRow[] | null>(null);
  const [quotes, setQuotes] = useState<QuoteRow[] | null>(null);
  const [bookings, setBookings] = useState<BookingRow[] | null>(null);

  useEffect(() => {
    listLeads().then(setLeads).catch(() => setLeads([]));
    listQuotes().then(setQuotes).catch(() => setQuotes([]));
    listBookings().then(setBookings).catch(() => setBookings([]));
  }, []);

  if (!leads || !quotes || !bookings) return <Loading />;

  const drafts = quotes.filter((q) => q.status === "draft").length;
  const upcoming = bookings.filter((b) => b.status === "confirmed").length;
  const recentLeads = leads.slice(0, 8);

  return (
    <div>
      <PageHeader title="Overview" />
      <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="Total leads" value={leads.length} />
        <StatCard
          label="Quotes awaiting review"
          value={drafts}
          hint={drafts ? "Approve them in Quotes → Needs review" : undefined}
        />
        <StatCard label="Upcoming moves" value={upcoming} />
      </div>

      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
        Recent leads
      </h2>
      {recentLeads.length === 0 ? (
        <EmptyState message="No leads yet — share your quote funnel link to start collecting them." />
      ) : (
        <Table headers={["Name", "Email", "Status", "Received"]}>
          {recentLeads.map((lead) => (
            <tr key={lead.id} className="hover:bg-slate-50">
              <Td>
                <Link href={`/dashboard/leads/${lead.id}`} className="font-medium text-blue-700">
                  {lead.name}
                </Link>
              </Td>
              <Td>{lead.email}</Td>
              <Td>
                <StatusBadge status={lead.status} />
              </Td>
              <Td>{longDate(lead.created_at)}</Td>
            </tr>
          ))}
        </Table>
      )}

      {quotes.length > 0 ? (
        <>
          <h2 className="mb-3 mt-8 text-sm font-semibold uppercase tracking-wide text-slate-500">
            Latest quotes
          </h2>
          <Table headers={["Customer", "Range", "Move date", "Status"]}>
            {quotes.slice(0, 5).map((q) => (
              <tr key={q.id} className="hover:bg-slate-50">
                <Td>
                  <Link href={`/dashboard/quotes/${q.id}`} className="font-medium text-blue-700">
                    {q.lead_name}
                  </Link>
                </Td>
                <Td>{dollarRange(q.amount_min_cents, q.amount_max_cents)}</Td>
                <Td>{longDate(q.move_date)}</Td>
                <Td>
                  <StatusBadge status={q.status} />
                </Td>
              </tr>
            ))}
          </Table>
        </>
      ) : null}
    </div>
  );
}
