/** Display formatting helpers (pure, unit-tested). */

/** Format integer cents as a whole-dollar string: 173400 -> "$1,734". */
export function dollars(cents: number): string {
  return (cents / 100).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

/** Format a cents range: "$1,734 – $2,206". */
export function dollarRange(minCents: number, maxCents: number): string {
  return `${dollars(minCents)} – ${dollars(maxCents)}`;
}

/** Format an ISO date (or datetime) as e.g. "July 9, 2026". */
export function longDate(iso: string): string {
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  return d.toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" });
}

/** Today's date as yyyy-mm-dd in the user's timezone (for date input min/validation). */
export function todayISO(): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}
