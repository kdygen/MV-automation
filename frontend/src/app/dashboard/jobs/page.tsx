"use client";

/**
 * Jobs: completed-move history (platform + imported), quote-accuracy scorecard, and
 * CSV import of historical jobs — the data that will power smarter pricing.
 */

import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";
import { getAccuracy, importJobsCsv, listJobs } from "@/lib/dashboard-api";
import type { AccuracySummary, ImportResult, JobRow } from "@/lib/dashboard-types";
import { dollars, longDate } from "@/lib/format";
import { EmptyState, Loading, PageHeader, StatCard, Table, Td } from "@/components/dashboard";
import { Button, Card, ErrorBanner } from "@/components/ui";

export default function JobsPage() {
  const [jobs, setJobs] = useState<JobRow[] | null>(null);
  const [accuracy, setAccuracy] = useState<AccuracySummary | null>(null);

  const refresh = () => {
    listJobs().then(setJobs).catch(() => setJobs([]));
    getAccuracy().then(setAccuracy).catch(() => {});
  };
  useEffect(refresh, []);

  return (
    <div>
      <PageHeader title="Completed jobs" />

      {accuracy ? (
        <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <StatCard label="Jobs recorded" value={accuracy.job_count} />
          <StatCard
            label="Quote accuracy (price)"
            value={
              accuracy.total_mape_pct != null ? `±${accuracy.total_mape_pct}%` : "—"
            }
            hint="Average error between quoted and actual totals"
          />
          <StatCard
            label="Hours estimate error"
            value={accuracy.hours_mae != null ? `${accuracy.hours_mae}h` : "—"}
            hint="Average gap between quoted and actual hours"
          />
        </div>
      ) : null}

      <ImportCard onImported={refresh} />

      {!jobs ? (
        <Loading />
      ) : jobs.length === 0 ? (
        <EmptyState message="No completed jobs yet. Complete bookings or import your history above." />
      ) : (
        <Table
          headers={["Move date", "Home", "Distance", "Actual", "Quoted", "Final cost", "Source"]}
        >
          {jobs.map((job) => (
            <tr key={job.id} className="hover:bg-slate-50">
              <Td>{longDate(job.move_date)}</Td>
              <Td>{job.home_size}</Td>
              <Td>{job.distance_miles != null ? `${job.distance_miles} mi` : "—"}</Td>
              <Td>
                {job.actual_hours}h · {job.actual_crew_size} movers
              </Td>
              <Td>{job.quoted_total_cents != null ? dollars(job.quoted_total_cents) : "—"}</Td>
              <Td>{dollars(job.actual_total_cents)}</Td>
              <Td>{job.source}</Td>
            </tr>
          ))}
        </Table>
      )}
    </div>
  );
}

function ImportCard({ onImported }: { onImported: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const upload = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await importJobsCsv(file);
      setResult(r);
      onImported();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Import failed.");
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <div className="mb-6">
      <Card>
        <h2 className="mb-1 text-sm font-semibold text-slate-700">Import job history (CSV)</h2>
        <p className="mb-3 text-xs text-slate-500">
          Columns: <code>move_date, home_size, actual_hours, actual_crew_size,
          actual_total_dollars</code> (optional: <code>packing_service, distance_miles,
          actual_volume_cuft</code>). Your history makes future quotes smarter.
        </p>
        <div className="flex items-center gap-3">
          <input ref={fileRef} type="file" accept=".csv,text/csv" className="text-sm" />
          <Button onClick={upload} disabled={busy}>
            {busy ? "Importing…" : "Import"}
          </Button>
        </div>
        {error ? (
          <div className="mt-3">
            <ErrorBanner message={error} />
          </div>
        ) : null}
        {result ? (
          <div className="mt-3 text-sm">
            <p className="text-green-700">Imported {result.imported} jobs.</p>
            {result.errors.length ? (
              <ul className="mt-1 list-inside list-disc text-red-600">
                {result.errors.slice(0, 5).map((e) => (
                  <li key={e.row}>
                    Row {e.row}: {e.message}
                  </li>
                ))}
                {result.errors.length > 5 ? (
                  <li>…and {result.errors.length - 5} more</li>
                ) : null}
              </ul>
            ) : null}
          </div>
        ) : null}
      </Card>
    </div>
  );
}
