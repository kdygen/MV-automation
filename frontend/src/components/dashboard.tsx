/** Shared dashboard UI: tables, badges, headers, empty states. */

import type { ReactNode } from "react";

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="mb-6 flex items-center justify-between">
      <h1 className="text-xl font-bold text-slate-900">{title}</h1>
      {children}
    </div>
  );
}

export function Table({ headers, children }: { headers: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
          <tr>
            {headers.map((h) => (
              <th key={h} className="px-4 py-3 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">{children}</tbody>
      </table>
    </div>
  );
}

export function Td({ children }: { children: ReactNode }) {
  return <td className="px-4 py-3 text-slate-700">{children}</td>;
}

const BADGE_COLORS: Record<string, string> = {
  new: "bg-blue-100 text-blue-700",
  quoted: "bg-indigo-100 text-indigo-700",
  sent: "bg-indigo-100 text-indigo-700",
  pending: "bg-amber-100 text-amber-700",
  draft: "bg-amber-100 text-amber-700",
  accepted: "bg-green-100 text-green-700",
  booked: "bg-green-100 text-green-700",
  confirmed: "bg-green-100 text-green-700",
  completed: "bg-emerald-100 text-emerald-700",
  in_progress: "bg-sky-100 text-sky-700",
  declined: "bg-red-100 text-red-700",
  lost: "bg-red-100 text-red-700",
  cancelled: "bg-slate-200 text-slate-600",
  expired: "bg-slate-200 text-slate-600",
};

export function StatusBadge({ status }: { status: string }) {
  const color = BADGE_COLORS[status] ?? "bg-slate-100 text-slate-600";
  return (
    <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${color}`}>
      {status.replace("_", " ")}
    </span>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-white px-6 py-12 text-center text-sm text-slate-500">
      {message}
    </div>
  );
}

export function StatCard({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5">
      <p className="text-sm text-slate-500">{label}</p>
      <p className="mt-1 text-3xl font-bold text-slate-900">{value}</p>
      {hint ? <p className="mt-1 text-xs text-slate-400">{hint}</p> : null}
    </div>
  );
}

export function Loading() {
  return <p className="py-12 text-center text-sm text-slate-500">Loading…</p>;
}
