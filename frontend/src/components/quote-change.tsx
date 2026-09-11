"use client";

/**
 * The deterministic change flows on the quote page: pick a date, or edit move details.
 *
 * Both follow the same three beats — **choose, preview, confirm** — because both are
 * repricing the move and the customer must see the new number before anything is
 * written. Neither component ever computes a price: it renders what
 * `/date-preview` or `/edit-preview` returned, and the confirm endpoint recalculates
 * from scratch anyway.
 *
 * The assistant can open these panels, but it opens *these* panels — there is no
 * AI-specific variant, so a shortcut from chat and a click on the permanent button run
 * identical code.
 */

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import {
  confirmDateChange,
  confirmEdit,
  getAvailability,
  getMoveDetails,
  previewDateChange,
  previewEdit,
} from "@/lib/api";
import {
  type MonthCursor,
  WEEKDAY_LABELS,
  isSelectable,
  monthCursorFromIso,
  monthGrid,
  monthLabel,
  monthRange,
  reasonLabel,
  shiftMonth,
} from "@/lib/calendar";
import {
  type EditFormState,
  HOME_SIZES,
  PACKING_SERVICES,
  SPECIAL_ITEM_CHOICES,
  canSubmitEdit,
  editErrors,
  editFormFromDetails,
  editPayload,
  toggleSpecialItem,
} from "@/lib/edit-move";
import { HOME_SIZE_LABELS, PACKING_LABELS } from "@/lib/quote-form";
import { dollarRange, longDate } from "@/lib/format";
import type { ChangePreview, DayAvailability, MoveDetails, QuotePublic } from "@/lib/types";
import { Button, Card, Checkbox, ErrorBanner, Field, Select, TextInput } from "@/components/ui";

function changeErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    // The backend's message here is customer-safe by design (availability and
    // validation copy), so it is shown; anything else falls back to generic text.
    if (["conflict", "validation_error"].includes(error.code)) return error.message;
    if (error.code === "not_found") return "This quote link is no longer available.";
    if (error.code === "network_error") return "Couldn't reach the server. Please try again.";
    if (error.status === 429) return "Too many attempts. Please wait a moment.";
  }
  return "Something went wrong. Please try again.";
}

/** Shared before/after panel — the customer's last look before anything is written. */
function PreviewPanel({
  preview,
  busy,
  confirmLabel,
  onConfirm,
  onCancel,
}: {
  preview: ChangePreview;
  busy: boolean;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { current, proposed, price_changed: priceChanged } = preview;
  return (
    <div className="mt-4 rounded-xl border border-blue-200 bg-blue-50 p-4">
      <p className="text-sm font-semibold text-slate-900">Review your change</p>
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="rounded-lg bg-white p-3">
          <p className="text-xs uppercase tracking-wide text-slate-500">Now</p>
          <p className="mt-1 font-semibold text-slate-700">
            {dollarRange(current.amount_min_cents, current.amount_max_cents)}
          </p>
          <p className="text-xs text-slate-500">
            {longDate(current.move_date)} · {current.crew_size} movers · ~{current.estimated_hours}h
          </p>
        </div>
        <div className="rounded-lg bg-white p-3 ring-2 ring-blue-400">
          <p className="text-xs uppercase tracking-wide text-blue-600">After this change</p>
          <p className="mt-1 font-semibold text-slate-900">
            {dollarRange(proposed.amount_min_cents, proposed.amount_max_cents)}
          </p>
          <p className="text-xs text-slate-500">
            {longDate(proposed.move_date)} · {proposed.crew_size} movers · ~
            {proposed.estimated_hours}h
          </p>
        </div>
      </div>
      <p className="mt-3 text-sm text-slate-700">
        {priceChanged
          ? "Your estimate changes with this update. Nothing is saved until you confirm."
          : "Your estimate stays the same. Nothing is saved until you confirm."}
      </p>
      <div className="mt-4 flex flex-wrap gap-3">
        <Button onClick={onConfirm} disabled={busy}>
          {busy ? "Saving…" : confirmLabel}
        </Button>
        <Button variant="secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

export function ChangeDatePanel({
  token,
  quote,
  onDone,
  onClose,
}: {
  token: string;
  quote: QuotePublic;
  onDone: (quote: QuotePublic) => void;
  onClose: () => void;
}) {
  const [cursor, setCursor] = useState<MonthCursor>(() => monthCursorFromIso(quote.move_date));
  const [days, setDays] = useState<DayAvailability[]>([]);
  const [loadedMonth, setLoadedMonth] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [preview, setPreview] = useState<ChangePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const monthKey = `${cursor.year}-${cursor.month}`;
  // Derived rather than a state flag: no synchronous setState during the effect, and
  // the spinner can never get stuck out of step with what is actually loaded.
  const loading = loadedMonth !== monthKey;

  useEffect(() => {
    // Guards against a slow response for an earlier month landing after a newer one
    // and repainting the grid with the wrong availability.
    let cancelled = false;
    const { from, to } = monthRange(cursor);
    getAvailability(token, from, to)
      .then((body) => {
        if (cancelled) return;
        setDays(body.days);
        setLoadedMonth(monthKey);
      })
      .catch(() => {
        if (!cancelled) setError("Couldn't load available dates.");
      });
    return () => {
      cancelled = true;
    };
  }, [token, cursor, monthKey]);

  const choose = useCallback(
    async (date: string) => {
      setSelected(date);
      setPreview(null);
      setError(null);
      setBusy(true);
      try {
        setPreview(await previewDateChange(token, date));
      } catch (err) {
        setError(changeErrorMessage(err));
        setSelected(null);
      } finally {
        setBusy(false);
      }
    },
    [token],
  );

  const confirm = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      onDone(await confirmDateChange(token, selected));
    } catch (err) {
      setError(changeErrorMessage(err));
      // The date may have filled up since the preview — reload the month.
      const { from, to } = monthRange(cursor);
      getAvailability(token, from, to)
        .then((b) => setDays(b.days))
        .catch(() => {});
      setPreview(null);
      setSelected(null);
    } finally {
      setBusy(false);
    }
  };

  const weeks = monthGrid(cursor, days, {
    currentMoveDate: quote.move_date,
    selected,
  });

  return (
    <Card>
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-900">Change your move date</h2>
        <button
          type="button"
          onClick={onClose}
          className="text-sm text-slate-500 hover:text-slate-800"
        >
          Close
        </button>
      </div>

      <div className="mt-4 flex items-center justify-between">
        <button
          type="button"
          aria-label="Previous month"
          onClick={() => setCursor((c) => shiftMonth(c, -1))}
          className="rounded-lg border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50"
        >
          ‹
        </button>
        <p className="text-sm font-medium text-slate-800">{monthLabel(cursor)}</p>
        <button
          type="button"
          aria-label="Next month"
          onClick={() => setCursor((c) => shiftMonth(c, 1))}
          className="rounded-lg border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50"
        >
          ›
        </button>
      </div>

      <div className="mt-3 grid grid-cols-7 gap-1 text-center text-xs text-slate-500">
        {WEEKDAY_LABELS.map((label) => (
          <div key={label} className="py-1">
            {label.slice(0, 1)}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1" aria-busy={loading}>
        {weeks.flat().map((cell, index) => {
          if (!cell.date) return <div key={`pad-${index}`} />;
          const day = Number(cell.date.slice(8));
          const selectable = isSelectable(cell);
          const base =
            "aspect-square rounded-lg text-sm transition focus:outline-none focus:ring-2 focus:ring-blue-300";
          const style = cell.isSelected
            ? "bg-blue-600 text-white"
            : cell.isCurrent
              ? "bg-slate-900 text-white"
              : selectable
                ? "bg-white text-slate-800 hover:bg-blue-50 border border-slate-200"
                : "bg-slate-100 text-slate-400 cursor-not-allowed";
          return (
            <button
              key={cell.date}
              type="button"
              disabled={!selectable || busy}
              onClick={() => choose(cell.date!)}
              title={
                cell.isCurrent
                  ? "Your current move date"
                  : cell.isAvailable
                    ? undefined
                    : reasonLabel(cell.reason)
              }
              aria-label={`${cell.date}${cell.isCurrent ? " (current move date)" : cell.isAvailable ? "" : ` — ${reasonLabel(cell.reason)}`}`}
              className={`${base} ${style}`}
            >
              {day}
            </button>
          );
        })}
      </div>

      <p className="mt-3 text-xs text-slate-500">
        Your current date is highlighted. Greyed-out days aren&apos;t available.
      </p>

      {error ? (
        <div className="mt-3">
          <ErrorBanner message={error} />
        </div>
      ) : null}

      {preview ? (
        <PreviewPanel
          preview={preview}
          busy={busy}
          confirmLabel="Confirm new date"
          onConfirm={confirm}
          onCancel={() => {
            setPreview(null);
            setSelected(null);
          }}
        />
      ) : null}
    </Card>
  );
}

export function EditMovePanel({
  token,
  onDone,
  onClose,
}: {
  token: string;
  onDone: (quote: QuotePublic) => void;
  onClose: () => void;
}) {
  const [details, setDetails] = useState<MoveDetails | null>(null);
  const [form, setForm] = useState<EditFormState | null>(null);
  const [preview, setPreview] = useState<ChangePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getMoveDetails(token)
      .then((loaded) => {
        setDetails(loaded);
        setForm(editFormFromDetails(loaded));
      })
      .catch(() => setError("Couldn't load your move details."));
  }, [token]);

  if (error && !form) return <ErrorBanner message={error} />;
  if (!form || !details) {
    return <p className="py-8 text-center text-sm text-slate-500">Loading your move…</p>;
  }

  const errors = editErrors(form);
  const update = (patch: Partial<EditFormState>) => {
    setPreview(null);
    setForm({ ...form, ...patch });
  };

  const showPreview = async () => {
    setBusy(true);
    setError(null);
    try {
      setPreview(await previewEdit(token, editPayload(form, details)));
    } catch (err) {
      setError(changeErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      onDone(await confirmEdit(token, editPayload(form, details)));
    } catch (err) {
      setError(changeErrorMessage(err));
      setPreview(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-900">Edit your move details</h2>
        <button
          type="button"
          onClick={onClose}
          className="text-sm text-slate-500 hover:text-slate-800"
        >
          Close
        </button>
      </div>
      <p className="mt-1 text-xs text-slate-500">
        We&apos;ll show you the updated estimate before anything is saved.
      </p>

      <div className="mt-4 space-y-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Home size" error={errors.homeSize}>
            <Select
              value={form.homeSize}
              onChange={(e) => update({ homeSize: e.target.value as EditFormState["homeSize"] })}
            >
              {HOME_SIZES.map((size) => (
                <option key={size} value={size}>
                  {HOME_SIZE_LABELS[size]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Packing help">
            <Select
              value={form.packingService}
              onChange={(e) =>
                update({ packingService: e.target.value as EditFormState["packingService"] })
              }
            >
              {PACKING_SERVICES.map((service) => (
                <option key={service} value={service}>
                  {PACKING_LABELS[service]}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        <fieldset>
          <legend className="mb-2 text-sm font-medium text-slate-700">Special items</legend>
          <div className="flex flex-wrap gap-2">
            {SPECIAL_ITEM_CHOICES.map((item) => {
              const on = form.specialItems.includes(item);
              return (
                <button
                  key={item}
                  type="button"
                  aria-pressed={on}
                  onClick={() => update({ specialItems: toggleSpecialItem(form.specialItems, item) })}
                  className={`rounded-full border px-3 py-1 text-sm ${
                    on
                      ? "border-blue-500 bg-blue-50 text-blue-700"
                      : "border-slate-300 bg-white text-slate-600 hover:bg-slate-50"
                  }`}
                >
                  {item}
                </button>
              );
            })}
          </div>
        </fieldset>

        {(["origin", "destination"] as const).map((side) => (
          <fieldset key={side} className="rounded-lg border border-slate-200 p-3">
            <legend className="px-1 text-sm font-medium text-slate-700">
              {side === "origin" ? `Pick-up (${details.origin_city})` : `Drop-off (${details.destination_city})`}
            </legend>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <Field label="Floor" error={errors[`${side}Floor`]}>
                <TextInput
                  type="number"
                  inputMode="numeric"
                  min={1}
                  max={100}
                  value={form[side].floor}
                  onChange={(e) => update({ [side]: { ...form[side], floor: e.target.value } } as Partial<EditFormState>)}
                />
              </Field>
              <Field label="Flights of stairs" error={errors[`${side}Stairs`]}>
                <TextInput
                  type="number"
                  inputMode="numeric"
                  min={0}
                  max={20}
                  value={form[side].stairsFlights}
                  onChange={(e) =>
                    update({ [side]: { ...form[side], stairsFlights: e.target.value } } as Partial<EditFormState>)
                  }
                />
              </Field>
              <div className="flex items-end pb-2">
                <Checkbox
                  label="Elevator"
                  checked={form[side].hasElevator}
                  onChange={(e) =>
                    update({ [side]: { ...form[side], hasElevator: e.target.checked } } as Partial<EditFormState>)
                  }
                />
              </div>
            </div>
          </fieldset>
        ))}

        {error ? <ErrorBanner message={error} /> : null}

        {preview ? (
          <PreviewPanel
            preview={preview}
            busy={busy}
            confirmLabel="Confirm changes"
            onConfirm={confirm}
            onCancel={() => setPreview(null)}
          />
        ) : (
          <Button onClick={showPreview} disabled={!canSubmitEdit(form, details, busy)}>
            {busy ? "Checking…" : "See updated estimate"}
          </Button>
        )}
      </div>
    </Card>
  );
}
