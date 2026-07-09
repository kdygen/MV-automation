import { describe, expect, it } from "vitest";

import {
  buildPayload,
  emptyAddress,
  initialFormState,
  validateStep,
  type FormState,
} from "./quote-form";

const TODAY = "2026-07-09";

function validState(): FormState {
  return {
    ...initialFormState(),
    name: "Jane Smith",
    email: "jane@example.com",
    phone: "555-0100",
    origin: { ...emptyAddress(), line1: "12 Elm St", city: "Springfield", state: "il", zip: "62701", floor: "3", stairsFlights: "2" },
    destination: { ...emptyAddress(), line1: "99 Oak Ave", city: "Chatham", state: "IL", zip: "62629" },
    moveDate: "2026-08-01",
    homeSize: "2br",
    packingService: "partial",
    specialItems: ["piano"],
    notes: "  gate code 4321  ",
  };
}

describe("validateStep", () => {
  it("passes every step for a valid state", () => {
    const state = validState();
    for (const step of ["contact", "origin", "destination", "details", "review"] as const) {
      expect(validateStep(step, state, TODAY)).toEqual({});
    }
  });

  it("requires name and a valid email on contact", () => {
    const errors = validateStep("contact", { ...validState(), name: " ", email: "nope" }, TODAY);
    expect(errors.name).toBeTruthy();
    expect(errors.email).toBeTruthy();
  });

  it("rejects malformed state and zip on addresses", () => {
    const state = validState();
    state.origin.state = "Illinois";
    state.origin.zip = "abcde";
    const errors = validateStep("origin", state, TODAY);
    expect(errors["origin.state"]).toBeTruthy();
    expect(errors["origin.zip"]).toBeTruthy();
  });

  it("accepts ZIP+4", () => {
    const state = validState();
    state.destination.zip = "62629-1234";
    expect(validateStep("destination", state, TODAY)).toEqual({});
  });

  it("rejects a past move date but accepts today", () => {
    expect(
      validateStep("details", { ...validState(), moveDate: "2026-07-08" }, TODAY).moveDate,
    ).toBeTruthy();
    expect(
      validateStep("details", { ...validState(), moveDate: TODAY }, TODAY).moveDate,
    ).toBeUndefined();
  });

  it("requires a home size", () => {
    expect(
      validateStep("details", { ...validState(), homeSize: "" }, TODAY).homeSize,
    ).toBeTruthy();
  });
});

describe("buildPayload", () => {
  it("normalizes and converts form state to the API shape", () => {
    const payload = buildPayload(validState());
    expect(payload.contact).toEqual({
      name: "Jane Smith",
      email: "jane@example.com",
      phone: "555-0100",
    });
    expect(payload.origin.state).toBe("IL"); // uppercased
    expect(payload.origin.floor).toBe(3); // numeric
    expect(payload.origin.stairs_flights).toBe(2);
    expect(payload.destination.has_elevator).toBe(false);
    expect(payload.move_date).toBe("2026-08-01");
    expect(payload.home_size).toBe("2br");
    expect(payload.special_items).toEqual(["piano"]);
    expect(payload.notes).toBe("gate code 4321"); // trimmed
  });

  it("sends null for empty optional fields", () => {
    const state = validState();
    state.phone = "  ";
    state.notes = "";
    const payload = buildPayload(state);
    expect(payload.contact.phone).toBeNull();
    expect(payload.notes).toBeNull();
  });

  it("throws if called before the form is complete", () => {
    expect(() => buildPayload({ ...validState(), homeSize: "" })).toThrow();
  });
});
