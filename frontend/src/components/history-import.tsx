"use client";

/**
 * The historical-import wizard: choose a file, map its columns, review, confirm.
 *
 * Three things the UI is careful about, because getting them wrong corrupts a company's
 * whole history rather than merely annoying them:
 *
 * 1. **Suggestions read as suggestions.** Every mapped column shows where it came from
 *    and can be changed or cleared; nothing is applied silently.
 * 2. **Unmapped columns are shown.** Dropping a column is fine, but it should be a
 *    visible decision, not an accident.
 * 3. **Review comes before writing.** The preview step calls an endpoint that writes
 *    nothing, and the copy says so — the Import button is the only thing that commits.
 */

import { useRef, useState } from "react";

import { ApiError } from "@/lib/api";
import {
  confirmHistoryImport,
  inspectHistoryFile,
  previewHistoryImport,
} from "@/lib/dashboard-api";
import type {
  ConfirmResult,
  InspectResult,
  PreviewResult,
} from "@/lib/dashboard-types";
import {
  type MappingState,
  type WizardStep,
  VERDICT_STYLES,
  canPreview,
  fieldGroups,
  initialMapping,
  mappingBlockers,
  requestBody,
  setFieldMapping,
  unmappedHeaders,
  verdictLabel,
  verdictReason,
} from "@/lib/history";
import { Button, Card, ErrorBanner, Select } from "@/components/ui";

function message(error: unknown): string {
  if (error instanceof ApiError) {
    // Import errors are written for the importing user (ambiguous dates, unmapped
    // columns), so they are shown rather than replaced with generic copy.
    if (["validation_error", "conflict"].includes(error.code)) return error.message;
    if (error.code === "forbidden") return "Only owners and admins can import history.";
    if (error.code === "network_error") return "Could not reach the server. Please try again.";
  }
  return "Something went wrong. Please try again.";
}

export function HistoryImport({ onDone, onClose }: { onDone: () => void; onClose: () => void }) {
  const [step, setStep] = useState<WizardStep>("choose");
  const [file, setFile] = useState<File | null>(null);
  const [inspect, setInspect] = useState<InspectResult | null>(null);
  const [state, setState] = useState<MappingState | null>(null);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [result, setResult] = useState<ConfirmResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const choose = async (picked: File) => {
    setBusy(true);
    setError(null);
    try {
      const inspected = await inspectHistoryFile(picked);
      setFile(picked);
      setInspect(inspected);
      setState(initialMapping(inspected));
      setStep("map");
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };

  const review = async () => {
    if (!file || !state) return;
    setBusy(true);
    setError(null);
    try {
      setPreview(await previewHistoryImport(file, requestBody(state)));
      setStep("review");
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };

  const commit = async () => {
    if (!file || !state) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await confirmHistoryImport(file, requestBody(state)));
      setStep("done");
      onDone();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Import past moves</h2>
          <p className="mt-1 text-xs text-slate-500">
            CSV or Excel. We only read the columns you map — everything else is ignored.
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="text-sm text-slate-500 hover:text-slate-800"
        >
          Close
        </button>
      </div>

      {error ? (
        <div className="mt-4">
          <ErrorBanner message={error} />
        </div>
      ) : null}

      {step === "choose" ? (
        <div className="mt-6">
          <input
            ref={fileInput}
            type="file"
            accept=".csv,.xlsx"
            className="hidden"
            onChange={(e) => {
              const picked = e.target.files?.[0];
              if (picked) choose(picked);
            }}
          />
          <Button onClick={() => fileInput.current?.click()} disabled={busy}>
            {busy ? "Reading…" : "Choose a file"}
          </Button>
          <p className="mt-3 text-xs text-slate-500">
            Nothing is saved until you review and confirm.
          </p>
        </div>
      ) : null}

      {step === "map" && inspect && state ? (
        <MappingStep
          inspect={inspect}
          state={state}
          busy={busy}
          onChange={setState}
          onReview={review}
          onBack={() => setStep("choose")}
        />
      ) : null}

      {step === "review" && preview ? (
        <ReviewStep
          preview={preview}
          busy={busy}
          onConfirm={commit}
          onBack={() => setStep("map")}
        />
      ) : null}

      {step === "done" && result ? (
        <div className="mt-6 rounded-lg border border-green-200 bg-green-50 p-4">
          <p className="font-semibold text-green-800">
            Imported {result.batch.row_count_imported} move
            {result.batch.row_count_imported === 1 ? "" : "s"}
          </p>
          <p className="mt-1 text-sm text-green-700">
            {result.batch.row_count_skipped} already in your history ·{" "}
            {result.batch.row_count_rejected} rejected. You can undo this import from the
            list below if something looks wrong.
          </p>
          <div className="mt-3">
            <Button variant="secondary" onClick={onClose}>
              Done
            </Button>
          </div>
        </div>
      ) : null}
    </Card>
  );
}

function MappingStep({
  inspect,
  state,
  busy,
  onChange,
  onReview,
  onBack,
}: {
  inspect: InspectResult;
  state: MappingState;
  busy: boolean;
  onChange: (next: MappingState) => void;
  onReview: () => void;
  onBack: () => void;
}) {
  const blockers = mappingBlockers(state, inspect.ambiguity);
  const ignored = unmappedHeaders(inspect, state);
  const samples = new Map(inspect.columns.map((c) => [c.header, c.sample_values.join(" · ")]));

  return (
    <div className="mt-6 space-y-5">
      <p className="text-sm text-slate-600">
        <span className="font-medium text-slate-900">{inspect.filename}</span> —{" "}
        {inspect.row_count} rows, {inspect.columns.length} columns.
      </p>

      {inspect.ambiguity.date_ambiguous ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
          <p className="text-sm font-medium text-amber-900">
            We can&apos;t tell how your dates are written
          </p>
          <p className="mt-1 text-xs text-amber-800">
            A value like 03/04/2025 could be March 4th or 4 March. Please tell us which —
            we won&apos;t guess.
          </p>
          <div className="mt-2 max-w-xs">
            <Select
              value={state.dateOrder ?? ""}
              onChange={(e) => onChange({ ...state, dateOrder: e.target.value || null })}
            >
              <option value="">Choose…</option>
              <option value="mdy">Month / Day / Year (US)</option>
              <option value="dmy">Day / Month / Year</option>
            </Select>
          </div>
        </div>
      ) : null}

      {inspect.ambiguity.money_ambiguous ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
          <p className="text-sm font-medium text-amber-900">
            We can&apos;t tell how your amounts are written
          </p>
          <p className="mt-1 text-xs text-amber-800">
            1.234 could be one thousand or one and a bit. Please choose the style.
          </p>
          <div className="mt-2 max-w-xs">
            <Select
              value={state.decimalStyle ?? ""}
              onChange={(e) => onChange({ ...state, decimalStyle: e.target.value || null })}
            >
              <option value="">Choose…</option>
              <option value="dot">1,234.56</option>
              <option value="comma">1.234,56</option>
            </Select>
          </div>
        </div>
      ) : null}

      <div className="space-y-4">
        {fieldGroups(inspect.fields).map((group) => (
          <section key={group.title}>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
              {group.title}
            </h3>
            <div className="space-y-2">
              {group.fields.map((field) => {
                const chosen = state.mapping[field.name] ?? "";
                return (
                  <div key={field.name} className="grid grid-cols-2 items-center gap-3">
                    <label
                      htmlFor={`map-${field.name}`}
                      className="text-sm text-slate-700"
                    >
                      {field.label}
                      {field.required ? <span className="text-red-600"> *</span> : null}
                      {chosen && samples.get(chosen) ? (
                        <span className="block truncate text-xs text-slate-400">
                          {samples.get(chosen)}
                        </span>
                      ) : null}
                    </label>
                    <Select
                      id={`map-${field.name}`}
                      value={chosen}
                      onChange={(e) =>
                        onChange(setFieldMapping(state, field.name, e.target.value || null))
                      }
                    >
                      <option value="">Don&apos;t import</option>
                      {inspect.columns.map((column) => (
                        <option key={column.header} value={column.header}>
                          {column.header}
                        </option>
                      ))}
                    </Select>
                  </div>
                );
              })}
            </div>
          </section>
        ))}
      </div>

      {ignored.length > 0 ? (
        <p className="text-xs text-slate-500">
          <span className="font-medium">Not importing:</span> {ignored.join(", ")}. Customer
          names, emails and payment details have no place to go and are always ignored.
        </p>
      ) : null}

      {blockers.length > 0 ? (
        <ul className="list-inside list-disc rounded-lg bg-slate-50 p-3 text-sm text-slate-600">
          {blockers.map((blocker) => (
            <li key={blocker}>{blocker}</li>
          ))}
        </ul>
      ) : null}

      <div className="flex gap-3">
        <Button onClick={onReview} disabled={busy || !canPreview(state, inspect.ambiguity)}>
          {busy ? "Checking…" : "Review before importing"}
        </Button>
        <Button variant="secondary" onClick={onBack} disabled={busy}>
          Back
        </Button>
      </div>
    </div>
  );
}

function ReviewStep({
  preview,
  busy,
  onConfirm,
  onBack,
}: {
  preview: PreviewResult;
  busy: boolean;
  onConfirm: () => void;
  onBack: () => void;
}) {
  const stats = [
    ["Will import", preview.importable, "text-green-700"],
    ["With warnings", preview.warnings, "text-amber-700"],
    ["Already imported", preview.duplicates, "text-slate-600"],
    ["Rejected", preview.rejected, "text-red-700"],
  ] as const;

  return (
    <div className="mt-6 space-y-4">
      <p className="text-sm text-slate-600">
        {preview.total} rows checked. <span className="font-medium">Nothing saved yet.</span>
      </p>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {stats.map(([label, value, tone]) => (
          <div key={label} className="rounded-lg border border-slate-200 p-3">
            <p className="text-xs text-slate-500">{label}</p>
            <p className={`mt-1 text-2xl font-bold ${tone}`}>{value}</p>
          </div>
        ))}
      </div>

      {preview.rows.length > 0 ? (
        <div className="max-h-72 overflow-y-auto rounded-lg border border-slate-200">
          <table className="w-full text-left text-sm">
            <thead className="sticky top-0 bg-slate-50 text-xs uppercase text-slate-500">
              <tr>
                <th className="px-3 py-2 font-medium">Row</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Detail</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {preview.rows.map((row) => (
                <tr key={row.row_number}>
                  <td className="px-3 py-2 text-slate-500">{row.row_number}</td>
                  <td className="px-3 py-2">
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                        VERDICT_STYLES[row.status] ?? "bg-slate-100 text-slate-600"
                      }`}
                    >
                      {verdictLabel(row.status)}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-slate-600">{verdictReason(row) ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {preview.rows_truncated ? (
        <p className="text-xs text-slate-500">
          Showing the first rows that need attention. Counts above cover the whole file.
        </p>
      ) : null}

      <div className="flex gap-3">
        <Button onClick={onConfirm} disabled={busy || preview.importable === 0}>
          {busy ? "Importing…" : `Import ${preview.importable} moves`}
        </Button>
        <Button variant="secondary" onClick={onBack} disabled={busy}>
          Change mapping
        </Button>
      </div>
    </div>
  );
}
