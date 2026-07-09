"use client";

/**
 * Public quote funnel: multi-step moving-request form for a company, addressed by its
 * slug (`/{company}/quote`). Steps: contact → origin → destination → move details →
 * review-and-confirm. Submitting either shows the instant quote (with a link to the
 * quote page) or a "the company will review" message, mirroring the backend's
 * instant-quote vs review-mode behavior.
 */

import { use, useState } from "react";

import { ApiError, submitMovingRequest } from "@/lib/api";
import { dollarRange, longDate, todayISO } from "@/lib/format";
import {
  type AddressFields,
  type FormState,
  HOME_SIZE_LABELS,
  PACKING_LABELS,
  SPECIAL_ITEM_OPTIONS,
  STEPS,
  type Step,
  buildPayload,
  initialFormState,
  validateStep,
} from "@/lib/quote-form";
import type { HomeSize, IntakeResponse, PackingService } from "@/lib/types";
import { Button, Card, Checkbox, ErrorBanner, Field, Select, TextInput } from "@/components/ui";

const STEP_TITLES: Record<Step, string> = {
  contact: "Your contact details",
  origin: "Where are you moving from?",
  destination: "Where are you moving to?",
  details: "About your move",
  review: "Review and confirm",
};

export default function QuoteFunnelPage({
  params,
}: {
  params: Promise<{ company: string }>;
}) {
  const { company } = use(params);
  const [stepIndex, setStepIndex] = useState(0);
  const [form, setForm] = useState<FormState>(initialFormState);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [result, setResult] = useState<IntakeResponse | null>(null);

  const step = STEPS[stepIndex];

  const update = (patch: Partial<FormState>) => setForm((f) => ({ ...f, ...patch }));
  const updateAddress = (key: "origin" | "destination", patch: Partial<AddressFields>) =>
    setForm((f) => ({ ...f, [key]: { ...f[key], ...patch } }));

  const next = () => {
    const stepErrors = validateStep(step, form, todayISO());
    setErrors(stepErrors);
    if (Object.keys(stepErrors).length === 0) setStepIndex((i) => i + 1);
  };
  const back = () => {
    setErrors({});
    setStepIndex((i) => Math.max(0, i - 1));
  };

  const submit = async () => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      setResult(await submitMovingRequest(company, buildPayload(form)));
    } catch (err) {
      setSubmitError(
        err instanceof ApiError && err.code === "not_found"
          ? "We couldn't find this moving company. Check the link and try again."
          : err instanceof ApiError
            ? err.message
            : "Something went wrong. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  if (result) return <ResultView result={result} />;

  return (
    <main className="mx-auto max-w-xl px-4 py-10">
      <p className="mb-2 text-sm font-medium uppercase tracking-wide text-blue-600">
        Free instant moving quote
      </p>
      <h1 className="mb-6 text-2xl font-bold text-slate-900">{STEP_TITLES[step]}</h1>

      <div className="mb-6 flex gap-1.5">
        {STEPS.map((s, i) => (
          <div
            key={s}
            className={`h-1.5 flex-1 rounded-full ${i <= stepIndex ? "bg-blue-600" : "bg-slate-200"}`}
          />
        ))}
      </div>

      <Card>
        {step === "contact" && (
          <div className="space-y-4">
            <Field label="Full name" error={errors.name}>
              <TextInput
                value={form.name}
                onChange={(e) => update({ name: e.target.value })}
                placeholder="Jane Smith"
                autoComplete="name"
              />
            </Field>
            <Field label="Email" error={errors.email}>
              <TextInput
                type="email"
                value={form.email}
                onChange={(e) => update({ email: e.target.value })}
                placeholder="jane@example.com"
                autoComplete="email"
              />
            </Field>
            <Field label="Phone (optional)">
              <TextInput
                type="tel"
                value={form.phone}
                onChange={(e) => update({ phone: e.target.value })}
                placeholder="555-0100"
                autoComplete="tel"
              />
            </Field>
          </div>
        )}

        {(step === "origin" || step === "destination") && (
          <AddressStep
            prefix={step}
            address={form[step]}
            errors={errors}
            onChange={(patch) => updateAddress(step, patch)}
          />
        )}

        {step === "details" && (
          <div className="space-y-4">
            <Field label="Moving date" error={errors.moveDate}>
              <TextInput
                type="date"
                min={todayISO()}
                value={form.moveDate}
                onChange={(e) => update({ moveDate: e.target.value })}
              />
            </Field>
            <Checkbox
              label="My date is flexible"
              checked={form.isDateFlexible}
              onChange={(e) => update({ isDateFlexible: e.target.checked })}
            />
            <Field label="Home size" error={errors.homeSize}>
              <Select
                value={form.homeSize}
                onChange={(e) => update({ homeSize: e.target.value as HomeSize })}
              >
                <option value="">Select…</option>
                {Object.entries(HOME_SIZE_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Packing service">
              <Select
                value={form.packingService}
                onChange={(e) => update({ packingService: e.target.value as PackingService })}
              >
                {Object.entries(PACKING_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
            </Field>
            <fieldset>
              <legend className="mb-1 text-sm font-medium text-slate-700">
                Special items (heavy or delicate)
              </legend>
              <div className="flex flex-wrap gap-4">
                {SPECIAL_ITEM_OPTIONS.map((item) => (
                  <Checkbox
                    key={item.value}
                    label={item.label}
                    checked={form.specialItems.includes(item.value)}
                    onChange={(e) =>
                      update({
                        specialItems: e.target.checked
                          ? [...form.specialItems, item.value]
                          : form.specialItems.filter((v) => v !== item.value),
                      })
                    }
                  />
                ))}
              </div>
            </fieldset>
            <Field label="Anything else we should know? (optional)">
              <TextInput
                value={form.notes}
                onChange={(e) => update({ notes: e.target.value })}
                placeholder="Gate codes, parking, fragile items…"
              />
            </Field>
          </div>
        )}

        {step === "review" && <ReviewStep form={form} />}

        {submitError ? (
          <div className="mt-4">
            <ErrorBanner message={submitError} />
          </div>
        ) : null}

        <div className="mt-6 flex justify-between">
          {stepIndex > 0 ? (
            <Button variant="secondary" onClick={back} disabled={submitting}>
              Back
            </Button>
          ) : (
            <span />
          )}
          {step === "review" ? (
            <Button onClick={submit} disabled={submitting}>
              {submitting ? "Getting your quote…" : "Get my quote"}
            </Button>
          ) : (
            <Button onClick={next}>Continue</Button>
          )}
        </div>
      </Card>
    </main>
  );
}

function AddressStep({
  prefix,
  address,
  errors,
  onChange,
}: {
  prefix: "origin" | "destination";
  address: AddressFields;
  errors: Record<string, string>;
  onChange: (patch: Partial<AddressFields>) => void;
}) {
  return (
    <div className="space-y-4">
      <Field label="Street address" error={errors[`${prefix}.line1`]}>
        <TextInput
          value={address.line1}
          onChange={(e) => onChange({ line1: e.target.value })}
          placeholder="12 Elm St"
          autoComplete="street-address"
        />
      </Field>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Field label="City" error={errors[`${prefix}.city`]}>
          <TextInput value={address.city} onChange={(e) => onChange({ city: e.target.value })} />
        </Field>
        <Field label="State" error={errors[`${prefix}.state`]}>
          <TextInput
            value={address.state}
            onChange={(e) => onChange({ state: e.target.value })}
            placeholder="IL"
            maxLength={2}
          />
        </Field>
        <Field label="ZIP" error={errors[`${prefix}.zip`]}>
          <TextInput
            value={address.zip}
            onChange={(e) => onChange({ zip: e.target.value })}
            placeholder="62701"
            inputMode="numeric"
          />
        </Field>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Floor" error={errors[`${prefix}.floor`]}>
          <TextInput
            type="number"
            min={1}
            value={address.floor}
            onChange={(e) => onChange({ floor: e.target.value })}
          />
        </Field>
        <Field label="Flights of stairs (if no elevator)" error={errors[`${prefix}.stairsFlights`]}>
          <TextInput
            type="number"
            min={0}
            value={address.stairsFlights}
            onChange={(e) => onChange({ stairsFlights: e.target.value })}
          />
        </Field>
      </div>
      <Checkbox
        label="Building has an elevator"
        checked={address.hasElevator}
        onChange={(e) => onChange({ hasElevator: e.target.checked })}
      />
    </div>
  );
}

function ReviewStep({ form }: { form: FormState }) {
  const rows: [string, string][] = [
    ["Name", form.name],
    ["Email", form.email],
    ["From", `${form.origin.line1}, ${form.origin.city} ${form.origin.state} ${form.origin.zip}`],
    [
      "To",
      `${form.destination.line1}, ${form.destination.city} ${form.destination.state} ${form.destination.zip}`,
    ],
    ["Move date", form.moveDate ? longDate(form.moveDate) : "—"],
    ["Home size", form.homeSize ? HOME_SIZE_LABELS[form.homeSize] : "—"],
    ["Packing", PACKING_LABELS[form.packingService]],
    ["Special items", form.specialItems.length ? form.specialItems.join(", ") : "None"],
  ];
  return (
    <div>
      <p className="mb-4 text-sm text-slate-600">
        Please double-check everything — your quote is calculated from these details.
      </p>
      <dl className="divide-y divide-slate-100">
        {rows.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-4 py-2 text-sm">
            <dt className="font-medium text-slate-500">{label}</dt>
            <dd className="text-right text-slate-900">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function ResultView({ result }: { result: IntakeResponse }) {
  const quote = result.quote;
  return (
    <main className="mx-auto max-w-xl px-4 py-16">
      <Card>
        {quote && quote.status === "sent" && quote.amount_min_cents != null ? (
          <div className="text-center">
            <p className="text-sm font-medium uppercase tracking-wide text-blue-600">
              Your instant estimate
            </p>
            <p className="mt-3 text-4xl font-bold text-slate-900">
              {dollarRange(quote.amount_min_cents, quote.amount_max_cents ?? 0)}
            </p>
            <p className="mt-2 text-sm text-slate-600">
              {quote.crew_size} movers · about {quote.estimated_hours} hours
            </p>
            {quote.valid_until ? (
              <p className="mt-1 text-xs text-slate-500">
                Valid until {longDate(quote.valid_until)} · non-binding estimate
              </p>
            ) : null}
            <p className="mt-6 text-sm text-slate-600">
              We&apos;ve emailed you a link to this quote. Ready to lock in your date?
            </p>
            <a
              href={`/quote/${quote.public_token}`}
              className="mt-4 inline-block rounded-lg bg-blue-600 px-6 py-3 text-sm font-semibold text-white hover:bg-blue-700"
            >
              View & accept your quote
            </a>
          </div>
        ) : (
          <div className="text-center">
            <p className="text-2xl font-semibold text-slate-900">Request received ✅</p>
            <p className="mt-3 text-sm text-slate-600">
              {quote?.status === "pending_review"
                ? "The team is reviewing your details and will email your quote shortly."
                : "We got your details and will email your quote as soon as it's ready."}
            </p>
          </div>
        )}
      </Card>
    </main>
  );
}
