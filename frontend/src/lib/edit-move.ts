/**
 * Pure logic for the edit-move form: state, validation, and change detection.
 *
 * The payload builder only ever sends fields the customer actually changed. That keeps
 * the backend's audit row honest — `requested_changes` records the edit, not a replay
 * of every field on the form — and it means an accidental no-op submit is caught here
 * rather than creating a pointless quote revision.
 *
 * Note what has no representation in this module: price, hours, and crew size. The
 * customer edits *inputs*; the deterministic engine derives everything else.
 */

import type { EditMovePayload, HomeSize, MoveDetails, PackingService } from "./types";

export const HOME_SIZES: HomeSize[] = ["studio", "1br", "2br", "3br", "4br", "5br_plus"];
export const PACKING_SERVICES: PackingService[] = ["none", "partial", "full"];

/** Mirrors the backend bounds in `SideAccessIn` / `AddressIn`. */
export const MIN_FLOOR = 1;
export const MAX_FLOOR = 100;
export const MAX_STAIRS = 20;
export const MAX_SPECIAL_ITEMS = 20;

export interface SideAccessState {
  floor: string; // strings while editing; parsed on payload build
  hasElevator: boolean;
  stairsFlights: string;
}

export interface EditFormState {
  moveDate: string;
  isDateFlexible: boolean;
  homeSize: HomeSize;
  packingService: PackingService;
  specialItems: string[];
  origin: SideAccessState;
  destination: SideAccessState;
}

export function editFormFromDetails(details: MoveDetails): EditFormState {
  return {
    moveDate: details.move_date,
    isDateFlexible: details.is_date_flexible,
    homeSize: details.home_size as HomeSize,
    packingService: details.packing_service as PackingService,
    specialItems: [...details.special_items],
    origin: {
      floor: String(details.origin_floor),
      hasElevator: details.origin_has_elevator,
      stairsFlights: String(details.origin_stairs_flights),
    },
    destination: {
      floor: String(details.destination_floor),
      hasElevator: details.destination_has_elevator,
      stairsFlights: String(details.destination_stairs_flights),
    },
  };
}

export type EditErrors = Record<string, string>;

function checkSide(side: SideAccessState, prefix: string, errors: EditErrors): void {
  const floor = Number(side.floor);
  if (!Number.isInteger(floor) || floor < MIN_FLOOR || floor > MAX_FLOOR) {
    errors[`${prefix}Floor`] = `Floor must be between ${MIN_FLOOR} and ${MAX_FLOOR}.`;
  }
  const stairs = Number(side.stairsFlights);
  if (!Number.isInteger(stairs) || stairs < 0 || stairs > MAX_STAIRS) {
    errors[`${prefix}Stairs`] = `Flights of stairs must be between 0 and ${MAX_STAIRS}.`;
  }
}

export function editErrors(state: EditFormState): EditErrors {
  const errors: EditErrors = {};
  if (!state.moveDate) errors.moveDate = "Pick a move date.";
  if (!HOME_SIZES.includes(state.homeSize)) errors.homeSize = "Choose a home size.";
  if (state.specialItems.length > MAX_SPECIAL_ITEMS) {
    errors.specialItems = `Up to ${MAX_SPECIAL_ITEMS} special items.`;
  }
  checkSide(state.origin, "origin", errors);
  checkSide(state.destination, "destination", errors);
  return errors;
}

function sameItems(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((item, index) => item === b[index]);
}

/**
 * Only the fields that differ from what the quote was priced from.
 *
 * Access fields are nested per side, and a side is omitted entirely when nothing in it
 * changed — so a customer who only fixed their home size sends exactly one field.
 */
export function editPayload(state: EditFormState, original: MoveDetails): EditMovePayload {
  const payload: EditMovePayload = {};

  if (state.moveDate !== original.move_date) payload.move_date = state.moveDate;
  if (state.isDateFlexible !== original.is_date_flexible) {
    payload.is_date_flexible = state.isDateFlexible;
  }
  if (state.homeSize !== original.home_size) payload.home_size = state.homeSize;
  if (state.packingService !== original.packing_service) {
    payload.packing_service = state.packingService;
  }
  if (!sameItems(state.specialItems, original.special_items)) {
    payload.special_items = state.specialItems;
  }

  const sides = [
    ["origin", state.origin, original.origin_floor, original.origin_has_elevator, original.origin_stairs_flights],
    [
      "destination",
      state.destination,
      original.destination_floor,
      original.destination_has_elevator,
      original.destination_stairs_flights,
    ],
  ] as const;

  for (const [key, side, floor, hasElevator, stairs] of sides) {
    const changes: Record<string, number | boolean> = {};
    if (Number(side.floor) !== floor) changes.floor = Number(side.floor);
    if (side.hasElevator !== hasElevator) changes.has_elevator = side.hasElevator;
    if (Number(side.stairsFlights) !== stairs) changes.stairs_flights = Number(side.stairsFlights);
    if (Object.keys(changes).length > 0) payload[key] = changes;
  }

  return payload;
}

export function hasChanges(state: EditFormState, original: MoveDetails): boolean {
  return Object.keys(editPayload(state, original)).length > 0;
}

export function canSubmitEdit(
  state: EditFormState,
  original: MoveDetails,
  busy: boolean,
): boolean {
  return !busy && hasChanges(state, original) && Object.keys(editErrors(state)).length === 0;
}

export const SPECIAL_ITEM_CHOICES = [
  "piano",
  "gun safe",
  "pool table",
  "aquarium",
  "artwork",
  "gym equipment",
] as const;

export function toggleSpecialItem(items: string[], item: string): string[] {
  return items.includes(item) ? items.filter((i) => i !== item) : [...items, item];
}
