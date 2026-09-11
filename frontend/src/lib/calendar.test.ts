import { describe, expect, it } from "vitest";

import {
  isSelectable,
  monthCursorFromIso,
  monthGrid,
  monthLabel,
  monthRange,
  reasonLabel,
  shiftMonth,
  toIso,
} from "./calendar";
import type { DayAvailability } from "./types";

const day = (date: string, is_available: boolean, reason: string | null = null): DayAvailability => ({
  date,
  is_available,
  reason,
});

describe("month arithmetic", () => {
  it("reads a cursor from an ISO date", () => {
    expect(monthCursorFromIso("2026-09-10")).toEqual({ year: 2026, month: 9 });
  });

  it("rolls forward across a year boundary", () => {
    expect(shiftMonth({ year: 2026, month: 12 }, 1)).toEqual({ year: 2027, month: 1 });
  });

  it("rolls backward across a year boundary", () => {
    expect(shiftMonth({ year: 2026, month: 1 }, -1)).toEqual({ year: 2025, month: 12 });
  });

  it("handles multi-month jumps", () => {
    expect(shiftMonth({ year: 2026, month: 11 }, 4)).toEqual({ year: 2027, month: 3 });
    expect(shiftMonth({ year: 2026, month: 2 }, -14)).toEqual({ year: 2024, month: 12 });
  });

  it("requests exactly the month's own days", () => {
    expect(monthRange({ year: 2026, month: 2 })).toEqual({ from: "2026-02-01", to: "2026-02-28" });
    expect(monthRange({ year: 2024, month: 2 })).toEqual({ from: "2024-02-01", to: "2024-02-29" });
    expect(monthRange({ year: 2026, month: 9 })).toEqual({ from: "2026-09-01", to: "2026-09-30" });
  });

  it("never exceeds the backend's 92-day window", () => {
    const { from, to } = monthRange({ year: 2026, month: 1 });
    const span = (Date.parse(to) - Date.parse(from)) / 86_400_000 + 1;
    expect(span).toBeLessThanOrEqual(92);
  });

  it("labels a month readably", () => {
    expect(monthLabel({ year: 2026, month: 9 })).toBe("September 2026");
  });

  it("pads ISO parts", () => {
    expect(toIso(2026, 3, 7)).toBe("2026-03-07");
  });
});

describe("the grid", () => {
  const cursor = { year: 2026, month: 9 }; // Sept 2026 starts on a Tuesday

  it("lays the month out in whole weeks", () => {
    const weeks = monthGrid(cursor, [], { currentMoveDate: "2026-09-20", selected: null });
    expect(weeks.every((week) => week.length === 7)).toBe(true);
    expect(weeks.flat().filter((c) => c.date !== null)).toHaveLength(30);
  });

  it("pads the days before the 1st", () => {
    const weeks = monthGrid(cursor, [], { currentMoveDate: "2026-09-20", selected: null });
    expect(weeks[0][0].date).toBeNull();
    expect(weeks[0][2].date).toBe("2026-09-01");
  });

  it("marks the current move date and the selection", () => {
    const weeks = monthGrid(cursor, [day("2026-09-18", true)], {
      currentMoveDate: "2026-09-20",
      selected: "2026-09-18",
    });
    const cells = weeks.flat();
    expect(cells.find((c) => c.date === "2026-09-20")?.isCurrent).toBe(true);
    expect(cells.find((c) => c.date === "2026-09-18")?.isSelected).toBe(true);
  });

  it("treats a date the backend did not report as unavailable", () => {
    // An absent answer is not a yes.
    const weeks = monthGrid(cursor, [], { currentMoveDate: "2026-09-20", selected: null });
    expect(weeks.flat().filter((c) => c.date && c.isAvailable)).toHaveLength(0);
  });

  it("carries the backend's reason through untouched", () => {
    const weeks = monthGrid(cursor, [day("2026-09-14", false, "fully_booked")], {
      currentMoveDate: "2026-09-20",
      selected: null,
    });
    expect(weeks.flat().find((c) => c.date === "2026-09-14")?.reason).toBe("fully_booked");
  });

  it("does not shift dates across timezones", () => {
    // Parsing "2026-09-01" as a Date would land on Aug 31 west of Greenwich.
    const weeks = monthGrid(cursor, [day("2026-09-01", true)], {
      currentMoveDate: "2026-09-20",
      selected: null,
    });
    expect(weeks.flat().find((c) => c.date === "2026-09-01")?.isAvailable).toBe(true);
  });
});

describe("selectability", () => {
  const cell = (over = {}) => ({
    date: "2026-09-14",
    isAvailable: true,
    isCurrent: false,
    isSelected: false,
    reason: null,
    ...over,
  });

  it("allows an available, non-current day", () => {
    expect(isSelectable(cell())).toBe(true);
  });

  it("refuses padding, unavailable days, and the current date", () => {
    expect(isSelectable(cell({ date: null }))).toBe(false);
    expect(isSelectable(cell({ isAvailable: false }))).toBe(false);
    expect(isSelectable(cell({ isCurrent: true }))).toBe(false);
  });
});

describe("reason copy", () => {
  it("translates every backend code", () => {
    expect(reasonLabel("fully_booked")).toBe("Fully booked");
    expect(reasonLabel("too_soon")).toBe("Too soon to book");
    expect(reasonLabel("too_far")).toBe("Too far ahead");
    expect(reasonLabel("unavailable")).toBe("Unavailable");
  });

  it("falls back safely for an unknown code", () => {
    expect(reasonLabel("something_new")).toBe("Unavailable");
    expect(reasonLabel(null)).toBe("Unavailable");
  });
});
