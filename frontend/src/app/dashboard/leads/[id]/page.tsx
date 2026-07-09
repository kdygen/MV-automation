"use client";

/** Lead detail: contact info, the structured move request(s), and quote history. */

import Link from "next/link";
import { use, useEffect, useState } from "react";

import { getLead } from "@/lib/dashboard-api";
import type { LeadDetail } from "@/lib/dashboard-types";
import { dollarRange, longDate } from "@/lib/format";
import { EmptyState, Loading, PageHeader, StatusBadge, Table, Td } from "@/components/dashboard";
import { Card } from "@/components/ui";

export default function LeadDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [detail, setDetail] = useState<LeadDetail | null>(null);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    getLead(id).then(setDetail).catch(() => setNotFound(true));
  }, [id]);

  if (notFound) return <EmptyState message="Lead not found." />;
  if (!detail) return <Loading />;

  const { lead, requests, quotes } = detail;

  return (
    <div>
      <PageHeader title={lead.name}>
        <StatusBadge status={lead.status} />
      </PageHeader>

      <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
            Contact
          </h2>
          <dl className="space-y-1 text-sm">
            <div className="flex justify-between">
              <dt className="text-slate-500">Email</dt>
              <dd className="text-slate-900">{lead.email}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-500">Phone</dt>
              <dd className="text-slate-900">{lead.phone ?? "—"}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-500">Source</dt>
              <dd className="text-slate-900">{lead.source}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-500">Received</dt>
              <dd className="text-slate-900">{longDate(lead.created_at)}</dd>
            </div>
          </dl>
        </Card>

        {requests.map((request) => (
          <Card key={request.id}>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
              Move details
            </h2>
            <dl className="space-y-1 text-sm">
              <div className="flex justify-between gap-4">
                <dt className="text-slate-500">From</dt>
                <dd className="text-right text-slate-900">
                  {request.origin_line1}, {request.origin_city} {request.origin_state}{" "}
                  {request.origin_zip}
                </dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-slate-500">To</dt>
                <dd className="text-right text-slate-900">
                  {request.destination_line1}, {request.destination_city}{" "}
                  {request.destination_state} {request.destination_zip}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">Date</dt>
                <dd className="text-slate-900">
                  {longDate(request.move_date)}
                  {request.is_date_flexible ? " (flexible)" : ""}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">Size / packing</dt>
                <dd className="text-slate-900">
                  {request.home_size} · {request.packing_service}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">Distance</dt>
                <dd className="text-slate-900">
                  {request.distance_miles != null ? `${request.distance_miles} mi` : "unknown"}
                </dd>
              </div>
              {request.special_items.length ? (
                <div className="flex justify-between">
                  <dt className="text-slate-500">Special items</dt>
                  <dd className="text-slate-900">{request.special_items.join(", ")}</dd>
                </div>
              ) : null}
              {request.notes ? (
                <div className="flex justify-between gap-4">
                  <dt className="text-slate-500">Notes</dt>
                  <dd className="text-right text-slate-900">{request.notes}</dd>
                </div>
              ) : null}
            </dl>
          </Card>
        ))}
      </div>

      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
        Quotes
      </h2>
      {quotes.length === 0 ? (
        <EmptyState message="No quotes yet for this lead." />
      ) : (
        <Table headers={["Range", "Move date", "Status", "Created"]}>
          {quotes.map((q) => (
            <tr key={q.id} className="hover:bg-slate-50">
              <Td>
                <Link href={`/dashboard/quotes/${q.id}`} className="font-medium text-blue-700">
                  {dollarRange(q.amount_min_cents, q.amount_max_cents)}
                </Link>
              </Td>
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
