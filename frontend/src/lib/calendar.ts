/**
 * Pure calendar maths for the date picker.
 *
 * Availability itself is never computed here — the backend decides, and this module
 * only lays its answer out in a grid. That split is deliberate: a client that could
 * decide which dates are bookable would eventually disagree with the server, and the
 * disagreement would surface as a customer picking a date that then fails.
 *
 * All dates are handled as `yyyy-mm-dd` strings in local terms. Constructing `Date`
 * objects from those strings would reintroduce the UTC-shift bug where a move on the
 * 1st renders on the 31st for anyone west of Greenwich.
 */

import type { DayAvailability } from "./types";

export const WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;

export interface MonthCursor {
  year: number;
  /** 1-12, not the 0-11 that `Date` uses — this is display data, not arithmetic. */
  month: number;
}

export interface DayCell {
  date: string | null; // null pads the grid before the 1st and after the last
  isAvailable: boolean;
  isCurrent: boolean;
  isSelected: boolean;
  reason: string | null;
}

export function toIso(year: number, month: number, day: number): string {
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

export function daysInMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/** Day of week of the 1st, 0 = Sunday. Computed in UTC to stay timezone-independent. */
export function firstWeekday(year: number, month: number): number {
  return new Date(Date.UTC(year, month - 1, 1)).getUTCDay();
}

export function monthCursorFromIso(iso: string): MonthCursor {
  const [year, month] = iso.split("-").map(Number);
  return { year, month };
}

export function shiftMonth(cursor: MonthCursor, delta: number): MonthCursor {
  const zeroBased = cursor.month - 1 + delta;
  return {
    year: cursor.year + Math.floor(zeroBased / 12),
    month: ((zeroBased % 12) + 12) % 12 + 1,
  };
}

/** The `from`/`to` window to request for a month — exactly its own days. */
export function monthRange(cursor: MonthCursor): { from: string; to: string } {
  return {
    from: toIso(cursor.year, cursor.month, 1),
    to: toIso(cursor.year, cursor.month, daysInMonth(cursor.year, cursor.month)),
  };
}

export function monthLabel(cursor: MonthCursor): string {
  return new Date(Date.UTC(cursor.year, cursor.month - 1, 1)).toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

/**
 * Lay a month out as weeks of seven cells.
 *
 * A date the backend did not report is rendered unavailable rather than available:
 * an absent answer is not a yes.
 */
export function monthGrid(
  cursor: MonthCursor,
  days: DayAvailability[],
  options: { currentMoveDate: string; selected: string | null },
): DayCell[][] {
  const byDate = new Map(days.map((day) => [day.date, day]));
  const cells: DayCell[] = [];

  for (let i = 0; i < firstWeekday(cursor.year, cursor.month); i += 1) {
    cells.push({ date: null, isAvailable: false, isCurrent: false, isSelected: false, reason: null });
  }
  for (let day = 1; day <= daysInMonth(cursor.year, cursor.month); day += 1) {
    const iso = toIso(cursor.year, cursor.month, day);
    const info = byDate.get(iso);
    cells.push({
      date: iso,
      isAvailable: info?.is_available ?? false,
      isCurrent: iso === options.currentMoveDate,
      isSelected: iso === options.selected,
      reason: info?.reason ?? null,
    });
  }
  while (cells.length % 7 !== 0) {
    cells.push({ date: null, isAvailable: false, isCurrent: false, isSelected: false, reason: null });
  }

  const weeks: DayCell[][] = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  return weeks;
}

/** Whether a cell can be chosen: real, available, and not already the move date. */
export function isSelectable(cell: DayCell): boolean {
  return cell.date !== null && cell.isAvailable && !cell.isCurrent;
}

/** Customer-facing copy for why a date is closed. Codes come from the backend. */
export function reasonLabel(reason: string | null): string {
  switch (reason) {
    case "fully_booked":
      return "Fully booked";
    case "too_soon":
      return "Too soon to book";
    case "too_far":
      return "Too far ahead";
    case "unavailable":
      return "Unavailable";
    default:
      return "Unavailable";
  }
}
