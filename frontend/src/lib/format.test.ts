import { describe, expect, it } from "vitest";

import { dollarRange, dollars, longDate } from "./format";

describe("dollars", () => {
  it("formats cents as whole dollars", () => {
    expect(dollars(173400)).toBe("$1,734");
    expect(dollars(60476)).toBe("$605"); // rounds to nearest dollar for display
    expect(dollars(0)).toBe("$0");
  });
});

describe("dollarRange", () => {
  it("joins min and max", () => {
    expect(dollarRange(173400, 220600)).toBe("$1,734 – $2,206");
  });
});

describe("longDate", () => {
  it("formats a plain date without timezone drift", () => {
    expect(longDate("2026-08-01")).toBe("August 1, 2026");
  });
  it("formats an ISO datetime", () => {
    expect(longDate("2026-08-01T12:00:00Z")).toContain("2026");
  });
});
