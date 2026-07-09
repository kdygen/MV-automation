/**
 * Minimal landing page. Real traffic enters via a company's funnel
 * (`/{company-slug}/quote`) or a quote link (`/quote/{token}`).
 */

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col items-center justify-center px-4 text-center">
      <h1 className="text-3xl font-bold text-slate-900">MV Automation</h1>
      <p className="mt-3 text-slate-600">
        Instant quotes and automated booking for moving companies.
      </p>
      <p className="mt-6 text-sm text-slate-500">
        Looking for a quote? Use the link your moving company gave you, e.g.{" "}
        <code className="rounded bg-slate-100 px-1.5 py-0.5">/acme-movers/quote</code>
      </p>
    </main>
  );
}
