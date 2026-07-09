"use client";

/**
 * Dashboard sign-in.
 *
 * Production: email/password through Supabase Auth (when NEXT_PUBLIC_SUPABASE_URL and
 * anon key are configured) — the resulting access token is what the backend verifies.
 * Local development: paste a token from `python -m scripts.mint_dev_token` instead.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";

import { fetchMe, setToken } from "@/lib/dashboard-api";
import { Button, Card, ErrorBanner, Field, TextInput } from "@/components/ui";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
const supabaseConfigured = Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [devToken, setDevToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const finishLogin = async (token: string) => {
    setToken(token);
    try {
      await fetchMe(); // validates the token and provisioning before entering
      router.replace("/dashboard");
    } catch {
      setError("That token was rejected by the server. Check it and try again.");
    }
  };

  const supabaseLogin = async () => {
    setBusy(true);
    setError(null);
    try {
      const { createClient } = await import("@supabase/supabase-js");
      const supabase = createClient(SUPABASE_URL!, SUPABASE_ANON_KEY!);
      const { data, error: authError } = await supabase.auth.signInWithPassword({
        email,
        password,
      });
      if (authError || !data.session) {
        setError(authError?.message ?? "Sign-in failed.");
        return;
      }
      await finishLogin(data.session.access_token);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-4">
      <h1 className="mb-6 text-center text-2xl font-bold text-slate-900">
        Sign in to your dashboard
      </h1>
      <Card>
        {error ? (
          <div className="mb-4">
            <ErrorBanner message={error} />
          </div>
        ) : null}

        {supabaseConfigured ? (
          <div className="space-y-4">
            <Field label="Email">
              <TextInput
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
              />
            </Field>
            <Field label="Password">
              <TextInput
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </Field>
            <Button onClick={supabaseLogin} disabled={busy || !email || !password}>
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </div>
        ) : (
          <div className="space-y-4">
            <p className="text-sm text-slate-600">
              Supabase auth isn&apos;t configured in this environment. Paste a developer
              token instead (run{" "}
              <code className="rounded bg-slate-100 px-1 py-0.5 text-xs">
                python -m scripts.mint_dev_token
              </code>{" "}
              in the backend).
            </p>
            <Field label="Developer token">
              <TextInput
                value={devToken}
                onChange={(e) => setDevToken(e.target.value)}
                placeholder="eyJhbGciOi…"
              />
            </Field>
            <Button onClick={() => finishLogin(devToken.trim())} disabled={!devToken.trim()}>
              Continue
            </Button>
          </div>
        )}
      </Card>
    </main>
  );
}
