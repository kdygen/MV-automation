import { describe, expect, it } from "vitest";

import {
  MAX_STAIRS,
  canSubmitEdit,
  editErrors,
  editFormFromDetails,
  editPayload,
  hasChanges,
  toggleSpecialItem,
} from "./edit-move";
import type { MoveDetails } from "./types";

const details = (overrides: Partial<MoveDetails> = {}): MoveDetails => ({
  move_date: "2026-10-10",
  is_date_flexible: false,
  home_size: "2br",
  packing_service: "none",
  special_items: ["piano"],
  origin_city: "Springfield",
  origin_floor: 2,
  origin_has_elevator: false,
  origin_stairs_flights: 1,
  destination_city: "Chatham",
  destination_floor: 1,
  destination_has_elevator: true,
  destination_stairs_flights: 0,
  ...overrides,
});

describe("loading the form", () => {
  it("round-trips the priced inputs", () => {
    const form = editFormFromDetails(details());
    expect(form.homeSize).toBe("2br");
    expect(form.origin.floor).toBe("2");
    expect(form.destination.hasElevator).toBe(true);
    expect(form.specialItems).toEqual(["piano"]);
  });

  it("copies special items rather than aliasing them", () => {
    const original = details();
    const form = editFormFromDetails(original);
    form.specialItems.push("aquarium");
    expect(original.special_items).toEqual(["piano"]);
  });
});

describe("the payload", () => {
  it("is empty when nothing changed", () => {
    const original = details();
    expect(editPayload(editFormFromDetails(original), original)).toEqual({});
    expect(hasChanges(editFormFromDetails(original), original)).toBe(false);
  });

  it("sends only the field that changed", () => {
    const original = details();
    const form = { ...editFormFromDetails(original), homeSize: "4br" as const };
    expect(editPayload(form, original)).toEqual({ home_size: "4br" });
  });

  it("nests access edits under the side that changed", () => {
    const original = details();
    const form = editFormFromDetails(original);
    form.origin = { ...form.origin, floor: "5" };
    expect(editPayload(form, original)).toEqual({ origin: { floor: 5 } });
  });

  it("omits a side entirely when nothing in it changed", () => {
    const original = details();
    const form = editFormFromDetails(original);
    form.destination = { ...form.destination, stairsFlights: "3" };
    const payload = editPayload(form, original);
    expect(payload.origin).toBeUndefined();
    expect(payload.destination).toEqual({ stairs_flights: 3 });
  });

  it("parses numeric strings into numbers", () => {
    const original = details();
    const form = editFormFromDetails(original);
    form.origin = { ...form.origin, floor: "7", stairsFlights: "2" };
    expect(editPayload(form, original).origin).toEqual({ floor: 7, stairs_flights: 2 });
  });

  it("detects a cleared special-items list", () => {
    const original = details();
    const form = { ...editFormFromDetails(original), specialItems: [] };
    expect(editPayload(form, original)).toEqual({ special_items: [] });
  });

  it("never carries a price, crew size, or hour count", () => {
    // The customer edits inputs; the deterministic engine derives everything else.
    const original = details();
    const form = { ...editFormFromDetails(original), homeSize: "4br" as const };
    const keys = Object.keys(editPayload(form, original));
    for (const forbidden of ["amount_min_cents", "total_cents", "crew_size", "estimated_hours"]) {
      expect(keys).not.toContain(forbidden);
    }
  });

  it("carries no identifiers", () => {
    const original = details();
    const form = { ...editFormFromDetails(original), homeSize: "4br" as const };
    const keys = Object.keys(editPayload(form, original));
    for (const forbidden of ["quote_id", "company_id", "id", "token"]) {
      expect(keys).not.toContain(forbidden);
    }
  });

  it("combines several edits", () => {
    const original = details();
    const form = editFormFromDetails(original);
    form.homeSize = "3br";
    form.packingService = "full";
    form.moveDate = "2026-10-17";
    expect(editPayload(form, original)).toEqual({
      home_size: "3br",
      packing_service: "full",
      move_date: "2026-10-17",
    });
  });
});

describe("validation", () => {
  it("accepts a loaded form as-is", () => {
    expect(editErrors(editFormFromDetails(details()))).toEqual({});
  });

  it("rejects an out-of-range floor", () => {
    const form = editFormFromDetails(details());
    for (const floor of ["0", "-1", "101", "", "abc"]) {
      expect(editErrors({ ...form, origin: { ...form.origin, floor } }).originFloor).toBeDefined();
    }
  });

  it("rejects out-of-range stairs on either side", () => {
    const form = editFormFromDetails(details());
    expect(
      editErrors({ ...form, origin: { ...form.origin, stairsFlights: "-1" } }).originStairs,
    ).toBeDefined();
    expect(
      editErrors({
        ...form,
        destination: { ...form.destination, stairsFlights: String(MAX_STAIRS + 1) },
      }).destinationStairs,
    ).toBeDefined();
  });

  it("requires a move date", () => {
    expect(editErrors({ ...editFormFromDetails(details()), moveDate: "" }).moveDate).toBeDefined();
  });
});

describe("submitting", () => {
  it("is blocked with no changes", () => {
    const original = details();
    expect(canSubmitEdit(editFormFromDetails(original), original, false)).toBe(false);
  });

  it("is blocked while a request is in flight", () => {
    const original = details();
    const form = { ...editFormFromDetails(original), homeSize: "4br" as const };
    expect(canSubmitEdit(form, original, true)).toBe(false);
  });

  it("is blocked when a field is invalid", () => {
    const original = details();
    const form = editFormFromDetails(original);
    form.homeSize = "4br";
    form.origin = { ...form.origin, floor: "0" };
    expect(canSubmitEdit(form, original, false)).toBe(false);
  });

  it("is allowed for a valid change", () => {
    const original = details();
    const form = { ...editFormFromDetails(original), homeSize: "4br" as const };
    expect(canSubmitEdit(form, original, false)).toBe(true);
  });
});

describe("special item toggles", () => {
  it("adds and removes", () => {
    expect(toggleSpecialItem([], "piano")).toEqual(["piano"]);
    expect(toggleSpecialItem(["piano"], "piano")).toEqual([]);
    expect(toggleSpecialItem(["piano"], "aquarium")).toEqual(["piano", "aquarium"]);
  });
});
