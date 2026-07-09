"use client";

/**
 * Settings: company profile (review mode, quote validity) and the pricing
 * configuration. Pricing edits PUT the full config document; the backend stores it as
 * a new version, keeping every historical quote auditable.
 */

import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import {
  getCompanySettings,
  getPricingSettings,
  patchCompanySettings,
  putPricingSettings,
} from "@/lib/dashboard-api";
import type { CompanySettings, PricingConfigDoc, PricingSettings } from "@/lib/dashboard-types";
import { Loading, PageHeader } from "@/components/dashboard";
import { Button, Card, Checkbox, ErrorBanner, Field, TextInput } from "@/components/ui";

const HOME_SIZES = ["studio", "1br", "2br", "3br", "4br", "5br_plus"] as const;

export default function SettingsPage() {
  return (
    <div className="max-w-3xl space-y-8">
      <CompanySection />
      <PricingSection />
    </div>
  );
}

function CompanySection() {
  const [settings, setSettings] = useState<CompanySettings | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getCompanySettings().then(setSettings).catch(() => setError("Could not load settings."));
  }, []);

  if (error && !settings) return <ErrorBanner message={error} />;
  if (!settings) return <Loading />;

  const save = async () => {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      setSettings(
        await patchCompanySettings({
          name: settings.name,
          email: settings.email,
          phone: settings.phone,
          quote_review_mode: settings.quote_review_mode,
          quote_validity_days: settings.quote_validity_days,
        }),
      );
      setStatus("Saved.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Save failed.");
    } finally {
      setBusy(false);
    }
  };

  const update = (patch: Partial<CompanySettings>) =>
    setSettings((s) => (s ? { ...s, ...patch } : s));

  return (
    <div>
      <PageHeader title="Company" />
      <Card>
        <div className="space-y-4">
          <Field label="Company name">
            <TextInput value={settings.name} onChange={(e) => update({ name: e.target.value })} />
          </Field>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Notification email">
              <TextInput
                type="email"
                value={settings.email ?? ""}
                onChange={(e) => update({ email: e.target.value })}
              />
            </Field>
            <Field label="Phone">
              <TextInput
                value={settings.phone ?? ""}
                onChange={(e) => update({ phone: e.target.value })}
              />
            </Field>
          </div>
          <Field label="Quote validity (days)">
            <TextInput
              type="number"
              min={1}
              max={90}
              value={String(settings.quote_validity_days)}
              onChange={(e) => update({ quote_validity_days: Number(e.target.value) })}
            />
          </Field>
          <Checkbox
            label="Review every quote before it's sent to the customer (review mode)"
            checked={settings.quote_review_mode}
            onChange={(e) => update({ quote_review_mode: e.target.checked })}
          />
          <p className="text-xs text-slate-500">
            Your public funnel link: <code>/{settings.slug}/quote</code>
          </p>
          {error ? <ErrorBanner message={error} /> : null}
          <div className="flex items-center gap-3">
            <Button onClick={save} disabled={busy}>
              {busy ? "Saving…" : "Save company settings"}
            </Button>
            {status ? <span className="text-sm text-green-700">{status}</span> : null}
          </div>
        </div>
      </Card>
    </div>
  );
}

function PricingSection() {
  const [pricing, setPricing] = useState<PricingSettings | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getPricingSettings().then(setPricing).catch(() => setError("Could not load pricing."));
  }, []);

  if (error && !pricing) return <ErrorBanner message={error} />;
  if (!pricing) return <Loading />;

  const config = pricing.config;
  const update = (patch: Partial<PricingConfigDoc>) =>
    setPricing((p) => (p ? { ...p, config: { ...p.config, ...patch } } : p));

  const updateMap = (
    key: "hourly_rate_by_crew" | "base_hours_by_home_size" | "crew_by_home_size",
    mapKey: string,
    value: number,
  ) => update({ [key]: { ...config[key], [mapKey]: value } } as Partial<PricingConfigDoc>);

  const save = async () => {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const saved = await putPricingSettings(config);
      setPricing(saved);
      setStatus(`Saved as version ${saved.version}.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Save failed — check the values.");
    } finally {
      setBusy(false);
    }
  };

  const num = (value: string) => Number(value) || 0;

  return (
    <div>
      <PageHeader title={`Pricing (version ${pricing.version})`} />
      <Card>
        <div className="space-y-6">
          <section>
            <h3 className="mb-2 text-sm font-semibold text-slate-700">
              Hourly rates by crew size ($/hour, truck included)
            </h3>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              {Object.entries(config.hourly_rate_by_crew).map(([crew, rate]) => (
                <Field key={crew} label={`${crew} movers`}>
                  <TextInput
                    type="number"
                    min={0}
                    value={String(rate)}
                    onChange={(e) => updateMap("hourly_rate_by_crew", crew, num(e.target.value))}
                  />
                </Field>
              ))}
            </div>
          </section>

          <section>
            <h3 className="mb-2 text-sm font-semibold text-slate-700">
              Base labor hours &amp; crew by home size
            </h3>
            <div className="space-y-2">
              {HOME_SIZES.map((size) => (
                <div key={size} className="grid grid-cols-3 items-center gap-4">
                  <span className="text-sm text-slate-600">{size}</span>
                  <Field label="Hours">
                    <TextInput
                      type="number"
                      min={0.5}
                      step={0.5}
                      value={String(config.base_hours_by_home_size[size] ?? "")}
                      onChange={(e) =>
                        updateMap("base_hours_by_home_size", size, num(e.target.value))
                      }
                    />
                  </Field>
                  <Field label="Crew">
                    <TextInput
                      type="number"
                      min={1}
                      max={5}
                      value={String(config.crew_by_home_size[size] ?? "")}
                      onChange={(e) => updateMap("crew_by_home_size", size, num(e.target.value))}
                    />
                  </Field>
                </div>
              ))}
            </div>
          </section>

          <section className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Field label="Minimum billable hours">
              <TextInput
                type="number"
                min={1}
                step={0.5}
                value={String(config.min_billable_hours)}
                onChange={(e) => update({ min_billable_hours: num(e.target.value) })}
              />
            </Field>
            <Field label="Travel fee base ($)">
              <TextInput
                type="number"
                min={0}
                value={String(config.travel_fee_base)}
                onChange={(e) => update({ travel_fee_base: num(e.target.value) })}
              />
            </Field>
            <Field label="Travel fee per mile ($)">
              <TextInput
                type="number"
                min={0}
                step={0.1}
                value={String(config.travel_fee_per_mile)}
                onChange={(e) => update({ travel_fee_per_mile: num(e.target.value) })}
              />
            </Field>
            <Field label="Weekend multiplier">
              <TextInput
                type="number"
                min={1}
                step={0.01}
                value={String(config.weekend_multiplier)}
                onChange={(e) => update({ weekend_multiplier: num(e.target.value) })}
              />
            </Field>
            <Field label="Month-end multiplier">
              <TextInput
                type="number"
                min={1}
                step={0.01}
                value={String(config.month_end_multiplier)}
                onChange={(e) => update({ month_end_multiplier: num(e.target.value) })}
              />
            </Field>
            <Field label="Peak season multiplier">
              <TextInput
                type="number"
                min={1}
                step={0.01}
                value={String(config.peak_season_multiplier)}
                onChange={(e) => update({ peak_season_multiplier: num(e.target.value) })}
              />
            </Field>
            <Field label="Quote range spread (0–0.5)">
              <TextInput
                type="number"
                min={0}
                max={0.5}
                step={0.01}
                value={String(config.range_spread_pct)}
                onChange={(e) => update({ range_spread_pct: num(e.target.value) })}
              />
            </Field>
            <Field label="Stairs: hours per flight">
              <TextInput
                type="number"
                min={0}
                step={0.1}
                value={String(config.stairs_hours_per_flight)}
                onChange={(e) => update({ stairs_hours_per_flight: num(e.target.value) })}
              />
            </Field>
            <Field label="Elevator building: extra hours">
              <TextInput
                type="number"
                min={0}
                step={0.1}
                value={String(config.elevator_building_hours)}
                onChange={(e) => update({ elevator_building_hours: num(e.target.value) })}
              />
            </Field>
          </section>

          {error ? <ErrorBanner message={error} /> : null}
          <div className="flex items-center gap-3">
            <Button onClick={save} disabled={busy}>
              {busy ? "Saving…" : "Save pricing (new version)"}
            </Button>
            {status ? <span className="text-sm text-green-700">{status}</span> : null}
          </div>
          <p className="text-xs text-slate-500">
            Every save creates a new pricing version. Existing quotes keep the version that
            priced them, so history stays auditable.
          </p>
        </div>
      </Card>
    </div>
  );
}
