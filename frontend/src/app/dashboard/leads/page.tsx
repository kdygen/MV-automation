"use client";

/** Leads list with a status filter. */

import Link from "next/link";
import { useEffect, useState } from "react";

import { listLeads } from "@/lib/dashboard-api";
import type { LeadRow } from "@/lib/dashboard-types";
import { longDate } from "@/lib/format";
import { EmptyState, Loading, PageHeader, StatusBadge, Table, Td } from "@/components/dashboard";

const FILTERS = ["all", "new", "quoted", "booked", "completed", "lost"] as const;

export default function LeadsPage() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const [leads, setLeads] = useState<LeadRow[] | null>(null);

  useEffect(() => {
    listLeads(filter === "all" ? undefined : filter)
      .then(setLeads)
      .catch(() => setLeads([]));
  }, [filter]);

  return (
    <div>
      <PageHeader title="Leads" />
      <div className="mb-4 flex gap-2">
        {FILTERS.map((f) => (
          <button
            key={f}
            onClick={() => {
              setLeads(null);
              setFilter(f);
            }}
            className={`rounded-full px-3 py-1 text-xs font-medium capitalize ${
              filter === f ? "bg-blue-600 text-white" : "bg-white text-slate-600 border border-slate-200"
            }`}
          >
            {f}
          </button>
        ))}
      </div>
      {!leads ? (
        <Loading />
      ) : leads.length === 0 ? (
        <EmptyState message="No leads match this filter." />
      ) : (
        <Table headers={["Name", "Email", "Phone", "Source", "Status", "Received"]}>
          {leads.map((lead) => (
            <tr key={lead.id} className="hover:bg-slate-50">
              <Td>
                <Link href={`/dashboard/leads/${lead.id}`} className="font-medium text-blue-700">
                  {lead.name}
                </Link>
              </Td>
              <Td>{lead.email}</Td>
              <Td>{lead.phone ?? "—"}</Td>
              <Td>{lead.source}</Td>
              <Td>
                <StatusBadge status={lead.status} />
              </Td>
              <Td>{longDate(lead.created_at)}</Td>
            </tr>
          ))}
        </Table>
      )}
    </div>
  );
}
