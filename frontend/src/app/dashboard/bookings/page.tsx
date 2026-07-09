"use client";

/**
 * Bookings list, soonest move first. Confirmed bookings expose a "Mark completed"
 * action that captures the actual hours/crew/cost — feeding the pricing data flywheel.
 */

import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import { completeBooking, listBookings } from "@/lib/dashboard-api";
import type { BookingRow } from "@/lib/dashboard-types";
import { dollarRange, longDate } from "@/lib/format";
import { EmptyState, Loading, PageHeader, StatusBadge, Table, Td } from "@/components/dashboard";
import { Button, ErrorBanner, Field, TextInput } from "@/components/ui";

export default function BookingsPage() {
  const [bookings, setBookings] = useState<BookingRow[] | null>(null);
  const [completing, setCompleting] = useState<string | null>(null);

  useEffect(() => {
    listBookings().then(setBookings).catch(() => setBookings([]));
  }, []);

  const refresh = () => listBookings().then(setBookings).catch(() => {});

  return (
    <div>
      <PageHeader title="Bookings" />
      {!bookings ? (
        <Loading />
      ) : bookings.length === 0 ? (
        <EmptyState message="No bookings yet — they appear automatically when customers accept quotes." />
      ) : (
        <Table headers={["Move date", "Customer", "Crew", "Quoted range", "Status", ""]}>
          {bookings.map((b) => (
            <BookingRows
              key={b.id}
              booking={b}
              open={completing === b.id}
              onToggle={() => setCompleting(completing === b.id ? null : b.id)}
              onCompleted={() => {
                setCompleting(null);
                refresh();
              }}
            />
          ))}
        </Table>
      )}
    </div>
  );
}

function BookingRows({
  booking,
  open,
  onToggle,
  onCompleted,
}: {
  booking: BookingRow;
  open: boolean;
  onToggle: () => void;
  onCompleted: () => void;
}) {
  return (
    <>
      <tr className="hover:bg-slate-50">
        <Td>{longDate(booking.scheduled_date)}</Td>
        <Td>
          <span className="font-medium text-slate-900">{booking.lead_name}</span>
          <span className="block text-xs text-slate-500">{booking.lead_email}</span>
        </Td>
        <Td>{booking.crew_size} movers</Td>
        <Td>{dollarRange(booking.amount_min_cents, booking.amount_max_cents)}</Td>
        <Td>
          <StatusBadge status={booking.status} />
        </Td>
        <Td>
          {booking.status === "confirmed" ? (
            <button className="text-xs font-medium text-blue-700" onClick={onToggle}>
              {open ? "Cancel" : "Mark completed"}
            </button>
          ) : null}
        </Td>
      </tr>
      {open ? (
        <tr>
          <td colSpan={6} className="bg-slate-50 px-4 py-4">
            <CompleteForm booking={booking} onCompleted={onCompleted} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function CompleteForm({
  booking,
  onCompleted,
}: {
  booking: BookingRow;
  onCompleted: () => void;
}) {
  const [hours, setHours] = useState("");
  const [crew, setCrew] = useState(String(booking.crew_size));
  const [total, setTotal] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setError(null);
    const actualHours = Number(hours);
    const actualCrew = Number(crew);
    const actualTotal = Number(total);
    if (!(actualHours > 0) || !(actualCrew >= 1) || !(actualTotal > 0)) {
      setError("Enter the actual hours, crew size, and final cost.");
      return;
    }
    setBusy(true);
    try {
      await completeBooking(booking.id, {
        actual_hours: actualHours,
        actual_crew_size: actualCrew,
        actual_total_dollars: actualTotal,
      });
      onCompleted();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not complete the booking.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="max-w-2xl">
      <p className="mb-3 text-sm text-slate-600">
        Record how the move actually went — this improves future quote accuracy.
      </p>
      {error ? (
        <div className="mb-3">
          <ErrorBanner message={error} />
        </div>
      ) : null}
      <div className="grid grid-cols-3 gap-4">
        <Field label="Actual hours">
          <TextInput
            type="number"
            min={0.5}
            step={0.5}
            value={hours}
            onChange={(e) => setHours(e.target.value)}
          />
        </Field>
        <Field label="Actual crew size">
          <TextInput
            type="number"
            min={1}
            max={10}
            value={crew}
            onChange={(e) => setCrew(e.target.value)}
          />
        </Field>
        <Field label="Final cost ($)">
          <TextInput
            type="number"
            min={1}
            value={total}
            onChange={(e) => setTotal(e.target.value)}
          />
        </Field>
      </div>
      <div className="mt-4">
        <Button onClick={submit} disabled={busy}>
          {busy ? "Saving…" : "Complete booking"}
        </Button>
      </div>
    </div>
  );
}
