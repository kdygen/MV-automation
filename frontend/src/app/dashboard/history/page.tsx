"use client";

/**
 * Historical moves — the data-validation page.
 *
 * Its first job is not analytics. It is letting an owner answer "did my import land
 * correctly?", so it leads with counts, provenance, recent imports with an undo, and a
 * table you can scan for nonsense. The only derived numbers shown are medians and signed
 * error percentages, which are exactly the figures that reveal bad data.
 *
 * The similar-moves panel presents **evidence**: durations, spread, and how far estimates
 * missed on comparable jobs. The backend emits no price and no recommended crew, and this
 * page does not imply them.
 */

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import {
  deleteHistoryMove,
  getHistoryMove,
  getHistorySummary,
  getSimilarToMove,
  listHistory,
  listImportBatches,
  revertImportBatch,
} from "@/lib/dashboard-api";
import type {
  HistoricalSignals,
  HistoryDetail,
  HistoryRow,
  HistorySummary,
  ImportBatch,
} from "@/lib/dashboard-types";
import { dollars, longDate } from "@/lib/format";
import {
  hours,
  rowHoursErrorPct,
  rowPriceErrorPct,
  routeLabel,
  signalsSummary,
  signedPct,
  sourceLabel,
} from "@/lib/history";
import { EmptyState, Loading, PageHeader, StatCard, Table, Td } from "@/components/dashboard";
import { Button, Card, ErrorBanner } from "@/components/ui";
import { HistoryImport } from "@/components/history-import";

export default function HistoryPage() {
  const [rows, setRows] = useState<HistoryRow[] | null>(null);
  const [summary, setSummary] = useState<HistorySummary | null>(null);
  const [batches, setBatches] = useState<ImportBatch[]>([]);
  const [selected, setSelected] = useState<HistoryDetail | null>(null);
  const [signals, setSignals] = useState<HistoricalSignals | null>(null);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listHistory().then(setRows).catch(() => setRows([]));
    getHistorySummary().then(setSummary).catch(() => {});
    listImportBatches().then(setBatches).catch(() => {});
  }, []);

  useEffect(refresh, [refresh]);

  const open = async (id: string) => {
    setError(null);
    setSignals(null);
    try {
      setSelected(await getHistoryMove(id));
    } catch {
      setError("Could not load that move.");
    }
  };

  const findSimilar = async (id: string) => {
    setError(null);
    try {
      setSignals(await getSimilarToMove(id));
    } catch {
      setError("Could not look up comparable moves.");
    }
  };

  const undo = async (batch: ImportBatch) => {
    if (!window.confirm(`Remove the ${batch.row_count_imported} moves from ${batch.filename}?`)) {
      return;
    }
    setError(null);
    try {
      await revertImportBatch(batch.id);
      setSelected(null);
      refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not undo that import.");
    }
  };

  const remove = async (move: HistoryDetail) => {
    if (!window.confirm("Delete this historical move? This cannot be undone.")) return;
    try {
      await deleteHistoryMove(move.id);
      setSelected(null);
      refresh();
    } catch {
      setError("Could not delete that move.");
    }
  };

  if (!rows) return <Loading />;

  return (
    <div className="space-y-6">
      <PageHeader title="Historical moves">
        <Button onClick={() => setImporting((v) => !v)}>
          {importing ? "Cancel import" : "Import history"}
        </Button>
      </PageHeader>

      <p className="-mt-4 text-sm text-slate-600">
        Completed moves — imported from your old system and recorded on the platform. This
        is the evidence base for future estimating accuracy.
      </p>

      {error ? <ErrorBanner message={error} /> : null}

      {summary ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <StatCard
            label="Completed moves"
            value={summary.total_moves}
            hint={`${summary.imported_moves} imported · ${summary.platform_moves} platform`}
          />
          <StatCard
            label="Median actual duration"
            value={hours(summary.median_actual_hours)}
            hint={`${summary.moves_with_hours} moves recorded hours`}
          />
          <StatCard
            label="Median estimate error"
            value={signedPct(summary.median_hours_error_pct)}
            hint={
              summary.median_hours_error_pct === null
                ? "Needs moves with an estimate"
                : "Positive means estimates ran low"
            }
          />
        </div>
      ) : null}

      {importing ? <HistoryImport onDone={refresh} onClose={() => setImporting(false)} /> : null}

      {batches.length > 0 ? (
        <section>
          <h2 className="mb-2 text-sm font-semibold text-slate-900">Recent imports</h2>
          <div className="space-y-2">
            {batches.map((batch) => (
              <div
                key={batch.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white p-3"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-slate-900">
                    {batch.filename}
                    {batch.reverted_at ? (
                      <span className="ml-2 rounded-full bg-slate-200 px-2 py-0.5 text-xs text-slate-600">
                        undone
                      </span>
                    ) : null}
                  </p>
                  <p className="text-xs text-slate-500">
                    {longDate(batch.created_at)} · {batch.row_count_imported} imported ·{" "}
                    {batch.row_count_skipped} already present · {batch.row_count_rejected}{" "}
                    rejected
                  </p>
                </div>
                {batch.reverted_at ? null : (
                  <Button variant="danger" onClick={() => undo(batch)}>
                    Undo
                  </Button>
                )}
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {rows.length === 0 ? (
        <EmptyState message="No completed moves yet. Import your history to get started." />
      ) : (
        <Table
          headers={[
            "Date",
            "Source",
            "Route",
            "Home",
            "Crew",
            "Est hrs",
            "Actual hrs",
            "Err",
            "Quote",
            "Final",
            "",
          ]}
        >
          {rows.map((row) => (
            <tr key={row.id} className="hover:bg-slate-50">
              <Td>{longDate(row.move_date)}</Td>
              <Td>
                <span
                  className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                    row.source === "platform"
                      ? "bg-blue-100 text-blue-700"
                      : "bg-slate-100 text-slate-600"
                  }`}
                >
                  {sourceLabel(row.source)}
                </span>
              </Td>
              <Td>{routeLabel(row)}</Td>
              <Td>{row.home_size}</Td>
              <Td>{row.actual_crew_size ?? "—"}</Td>
              <Td>{hours(row.quoted_hours)}</Td>
              <Td>{hours(row.actual_hours)}</Td>
              <Td>{signedPct(rowHoursErrorPct(row))}</Td>
              <Td>{row.quoted_total_cents === null ? "—" : dollars(row.quoted_total_cents)}</Td>
              <Td>{row.actual_total_cents === null ? "—" : dollars(row.actual_total_cents)}</Td>
              <Td>
                <button
                  type="button"
                  onClick={() => open(row.id)}
                  className="text-xs font-medium text-blue-600 hover:text-blue-800"
                >
                  Details
                </button>
              </Td>
            </tr>
          ))}
        </Table>
      )}

      {selected ? (
        <MoveDetail
          move={selected}
          signals={signals}
          onClose={() => {
            setSelected(null);
            setSignals(null);
          }}
          onFindSimilar={() => findSimilar(selected.id)}
          onDelete={() => remove(selected)}
        />
      ) : null}
    </div>
  );
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="text-sm text-slate-900">{value ?? "—"}</dd>
    </div>
  );
}

function yesNo(value: boolean | null): string {
  return value === null ? "—" : value ? "Yes" : "No";
}

function MoveDetail({
  move,
  signals,
  onClose,
  onFindSimilar,
  onDelete,
}: {
  move: HistoryDetail;
  signals: HistoricalSignals | null;
  onClose: () => void;
  onFindSimilar: () => void;
  onDelete: () => void;
}) {
  return (
    <Card>
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">
            {longDate(move.move_date)} · {move.home_size} · {routeLabel(move)}
          </h2>
          <p className="mt-1 text-xs text-slate-500">
            {sourceLabel(move.source)}
            {move.external_ref ? ` · your ref ${move.external_ref}` : ""}
          </p>
        </div>
        <button type="button" onClick={onClose} className="text-sm text-slate-500 hover:text-slate-800">
          Close
        </button>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Fact label="Estimated hours" value={hours(move.quoted_hours)} />
        <Fact label="Actual hours" value={hours(move.actual_hours)} />
        <Fact label="Hours error" value={signedPct(rowHoursErrorPct(move))} />
        <Fact label="Crew" value={move.actual_crew_size} />
        <Fact
          label="Quoted total"
          value={move.quoted_total_cents === null ? "—" : dollars(move.quoted_total_cents)}
        />
        <Fact
          label="Final total"
          value={move.actual_total_cents === null ? "—" : dollars(move.actual_total_cents)}
        />
        <Fact label="Price error" value={signedPct(rowPriceErrorPct(move))} />
        <Fact
          label="Additional charges"
          value={
            move.additional_charges_cents === null
              ? "—"
              : dollars(move.additional_charges_cents)
          }
        />
      </dl>

      <h3 className="mt-6 text-xs font-semibold uppercase tracking-wide text-slate-500">
        Access &amp; services
      </h3>
      <dl className="mt-2 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Fact label="Origin floor" value={move.origin_floor} />
        <Fact label="Origin stairs" value={move.origin_stairs_flights} />
        <Fact label="Origin elevator" value={yesNo(move.origin_has_elevator)} />
        <Fact label="Destination floor" value={move.destination_floor} />
        <Fact label="Destination stairs" value={move.destination_stairs_flights} />
        <Fact label="Destination elevator" value={yesNo(move.destination_has_elevator)} />
        <Fact label="Long carry" value={yesNo(move.long_carry)} />
        <Fact label="Parking" value={move.parking_difficulty} />
        <Fact label="Packing" value={move.packing_service} />
        <Fact label="Storage" value={yesNo(move.has_storage)} />
        <Fact label="Distance" value={move.distance_miles ? `${move.distance_miles} mi` : "—"} />
        <Fact label="Delay" value={move.delay_minutes ? `${move.delay_minutes} min` : "—"} />
        <Fact
          label="Special items"
          value={move.special_items?.length ? move.special_items.join(", ") : "—"}
        />
        <Fact
          label="Issues"
          value={move.issue_tags?.length ? move.issue_tags.join(", ") : "—"}
        />
        {move.origin_line1 ? <Fact label="Origin address" value={move.origin_line1} /> : null}
        {move.destination_line1 ? (
          <Fact label="Destination address" value={move.destination_line1} />
        ) : null}
      </dl>

      {[
        ["Problems", move.problem_notes],
        ["Building notes", move.building_notes],
        ["Customer changes", move.change_notes],
        ["Why the price differed", move.variance_reason],
        ["Notes", move.notes],
      ].filter(([, text]) => Boolean(text)).length > 0 ? (
        <>
          <h3 className="mt-6 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Notes
          </h3>
          <div className="mt-2 space-y-2">
            {(
              [
                ["Problems", move.problem_notes],
                ["Building notes", move.building_notes],
                ["Customer changes", move.change_notes],
                ["Why the price differed", move.variance_reason],
                ["Notes", move.notes],
              ] as const
            )
              .filter(([, text]) => Boolean(text))
              .map(([label, text]) => (
                <p key={label} className="text-sm text-slate-700">
                  <span className="font-medium text-slate-900">{label}:</span> {text}
                </p>
              ))}
          </div>
        </>
      ) : null}

      <div className="mt-6 flex flex-wrap gap-3">
        <Button variant="secondary" onClick={onFindSimilar}>
          Find comparable moves
        </Button>
        <Button variant="danger" onClick={onDelete}>
          Delete move
        </Button>
      </div>

      {signals ? <Signals signals={signals} /> : null}
    </Card>
  );
}

function Signals({ signals }: { signals: HistoricalSignals }) {
  return (
    <div className="mt-6 rounded-xl border border-blue-200 bg-blue-50 p-4">
      <h3 className="text-sm font-semibold text-slate-900">What comparable moves did</h3>
      <p className="mt-1 text-sm text-slate-700">{signalsSummary(signals)}</p>
      <p className="mt-1 text-xs text-slate-500">
        Historical evidence only — this does not set a price or a crew size.
      </p>

      {signals.common_issue_tags.length > 0 ? (
        <p className="mt-2 text-xs text-slate-600">
          Recurring issues: {signals.common_issue_tags.join(", ")}
        </p>
      ) : null}

      {signals.matches.length > 0 ? (
        <div className="mt-3 overflow-x-auto rounded-lg border border-blue-200 bg-white">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase text-slate-500">
              <tr>
                <th className="px-3 py-2 font-medium">Match</th>
                <th className="px-3 py-2 font-medium">Date</th>
                <th className="px-3 py-2 font-medium">Home</th>
                <th className="px-3 py-2 font-medium">Access</th>
                <th className="px-3 py-2 font-medium">Est</th>
                <th className="px-3 py-2 font-medium">Actual</th>
                <th className="px-3 py-2 font-medium">Err</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {signals.matches.map((match) => (
                <tr key={match.id}>
                  <td className="px-3 py-2">
                    <span className="font-medium text-slate-900">
                      {Math.round(match.score * 100)}%
                    </span>
                    <span className="ml-1 text-xs text-slate-400">
                      {match.matched_on.slice(0, 3).join(", ")}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-slate-600">{longDate(match.move_date)}</td>
                  <td className="px-3 py-2 text-slate-600">{match.home_size}</td>
                  <td className="px-3 py-2 text-slate-600">
                    {[
                      match.origin_stairs_flights
                        ? `${match.origin_stairs_flights} flights`
                        : null,
                      match.origin_has_elevator === true ? "elevator" : null,
                      match.long_carry ? "long carry" : null,
                    ]
                      .filter(Boolean)
                      .join(" · ") || "—"}
                  </td>
                  <td className="px-3 py-2 text-slate-600">{hours(match.quoted_hours)}</td>
                  <td className="px-3 py-2 text-slate-900">{hours(match.actual_hours)}</td>
                  <td className="px-3 py-2 text-slate-600">
                    {signedPct(match.hours_error_pct)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
