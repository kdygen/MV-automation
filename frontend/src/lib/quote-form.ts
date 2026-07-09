/**
 * Pure logic for the multi-step quote form: state shape, per-step validation, and
 * payload assembly. No React in this module — it is unit-tested directly (vitest),
 * and the page component stays a thin rendering layer.
 */

import type { AddressIn, HomeSize, MovingRequestIn, PackingService } from "./types";

export const STEPS = ["contact", "origin", "destination", "details", "review"] as const;
export type Step = (typeof STEPS)[number];

export interface AddressFields {
  line1: string;
  city: string;
  state: string;
  zip: string;
  floor: string; // kept as strings in form state; parsed on payload build
  hasElevator: boolean;
  stairsFlights: string;
}

export interface FormState {
  name: string;
  email: string;
  phone: string;
  origin: AddressFields;
  destination: AddressFields;
  moveDate: string; // yyyy-mm-dd
  isDateFlexible: boolean;
  homeSize: HomeSize | "";
  packingService: PackingService;
  specialItems: string[];
  notes: string;
}

export const emptyAddress = (): AddressFields => ({
  line1: "",
  city: "",
  state: "",
  zip: "",
  floor: "1",
  hasElevator: false,
  stairsFlights: "0",
});

export const initialFormState = (): FormState => ({
  name: "",
  email: "",
  phone: "",
  origin: emptyAddress(),
  destination: emptyAddress(),
  moveDate: "",
  isDateFlexible: false,
  homeSize: "",
  packingService: "none",
  specialItems: [],
  notes: "",
});

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const ZIP_RE = /^\d{5}(-\d{4})?$/;
const STATE_RE = /^[A-Za-z]{2}$/;

export type FieldErrors = Record<string, string>;

export function validateContact(state: FormState): FieldErrors {
  const errors: FieldErrors = {};
  if (!state.name.trim()) errors.name = "Please enter your name";
  if (!EMAIL_RE.test(state.email.trim())) errors.email = "Please enter a valid email";
  return errors;
}

export function validateAddress(address: AddressFields, prefix: string): FieldErrors {
  const errors: FieldErrors = {};
  if (!address.line1.trim()) errors[`${prefix}.line1`] = "Street address is required";
  if (!address.city.trim()) errors[`${prefix}.city`] = "City is required";
  if (!STATE_RE.test(address.state.trim()))
    errors[`${prefix}.state`] = "Use the 2-letter state code (e.g. IL)";
  if (!ZIP_RE.test(address.zip.trim())) errors[`${prefix}.zip`] = "Enter a 5-digit ZIP code";
  const floor = Number(address.floor);
  if (!Number.isInteger(floor) || floor < 1) errors[`${prefix}.floor`] = "Floor must be 1 or more";
  const flights = Number(address.stairsFlights);
  if (!Number.isInteger(flights) || flights < 0)
    errors[`${prefix}.stairsFlights`] = "Flights of stairs can't be negative";
  return errors;
}

export function validateDetails(state: FormState, today: string): FieldErrors {
  const errors: FieldErrors = {};
  if (!state.moveDate) errors.moveDate = "Pick your moving date";
  else if (state.moveDate < today) errors.moveDate = "The move date can't be in the past";
  if (!state.homeSize) errors.homeSize = "Select your home size";
  return errors;
}

/** Validate a single step; `today` is injected for testability (yyyy-mm-dd). */
export function validateStep(step: Step, state: FormState, today: string): FieldErrors {
  switch (step) {
    case "contact":
      return validateContact(state);
    case "origin":
      return validateAddress(state.origin, "origin");
    case "destination":
      return validateAddress(state.destination, "destination");
    case "details":
      return validateDetails(state, today);
    case "review":
      return {};
  }
}

function toAddressIn(address: AddressFields): AddressIn {
  return {
    line1: address.line1.trim(),
    city: address.city.trim(),
    state: address.state.trim().toUpperCase(),
    zip: address.zip.trim(),
    floor: Number(address.floor),
    has_elevator: address.hasElevator,
    stairs_flights: Number(address.stairsFlights),
  };
}

/** Assemble the API payload; call only after every step validates. */
export function buildPayload(state: FormState): MovingRequestIn {
  if (!state.homeSize) throw new Error("buildPayload called with incomplete state");
  return {
    contact: {
      name: state.name.trim(),
      email: state.email.trim(),
      phone: state.phone.trim() || null,
    },
    origin: toAddressIn(state.origin),
    destination: toAddressIn(state.destination),
    move_date: state.moveDate,
    is_date_flexible: state.isDateFlexible,
    home_size: state.homeSize,
    packing_service: state.packingService,
    special_items: state.specialItems,
    notes: state.notes.trim() || null,
  };
}

export const HOME_SIZE_LABELS: Record<HomeSize, string> = {
  studio: "Studio",
  "1br": "1 bedroom",
  "2br": "2 bedrooms",
  "3br": "3 bedrooms",
  "4br": "4 bedrooms",
  "5br_plus": "5+ bedrooms",
};

export const PACKING_LABELS: Record<PackingService, string> = {
  none: "No packing — I'll pack myself",
  partial: "Partial packing help",
  full: "Full packing service",
};

export const SPECIAL_ITEM_OPTIONS = [
  { value: "piano", label: "Piano" },
  { value: "safe", label: "Safe" },
  { value: "pool_table", label: "Pool table" },
] as const;
