"use client";

/**
 * Dashboard shell: auth guard + sidebar navigation.
 *
 * Every /dashboard page (except /dashboard/login) requires a stored token; missing or
 * rejected tokens redirect to the login page. The token itself is validated by the
 * backend on every request — this guard is UX, not security.
 */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { clearToken, fetchMe, getToken } from "@/lib/dashboard-api";
import type { Me } from "@/lib/dashboard-types";

const NAV = [
  { href: "/dashboard", label: "Overview" },
  { href: "/dashboard/leads", label: "Leads" },
  { href: "/dashboard/quotes", label: "Quotes" },
  { href: "/dashboard/bookings", label: "Bookings" },
  { href: "/dashboard/jobs", label: "Jobs" },
  { href: "/dashboard/settings", label: "Settings" },
];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const isLogin = pathname === "/dashboard/login";

  useEffect(() => {
    if (isLogin) return;
    if (!getToken()) {
      router.replace("/dashboard/login");
      return;
    }
    fetchMe()
      .then(setMe)
      .catch(() => {
        clearToken();
        router.replace("/dashboard/login");
      });
  }, [isLogin, router]);

  if (isLogin) return <>{children}</>;

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-56 flex-col border-r border-slate-200 bg-white">
        <div className="border-b border-slate-100 px-5 py-4">
          <p className="text-sm font-bold text-slate-900">MV Automation</p>
          {me ? <p className="mt-0.5 truncate text-xs text-slate-500">{me.email}</p> : null}
        </div>
        <nav className="flex-1 space-y-1 p-3">
          {NAV.map((item) => {
            const active =
              item.href === "/dashboard"
                ? pathname === "/dashboard"
                : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`block rounded-lg px-3 py-2 text-sm font-medium ${
                  active
                    ? "bg-blue-50 text-blue-700"
                    : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <button
          onClick={() => {
            clearToken();
            router.replace("/dashboard/login");
          }}
          className="border-t border-slate-100 px-5 py-3 text-left text-sm text-slate-500 hover:text-slate-900"
        >
          Sign out
        </button>
      </aside>
      <main className="flex-1 bg-slate-50 p-8">{children}</main>
    </div>
  );
}
