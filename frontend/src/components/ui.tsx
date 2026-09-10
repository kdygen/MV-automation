/**
 * Tiny shared UI primitives for the public funnel. Deliberately minimal — a design
 * system can replace these without touching page logic.
 */

import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

export function Card({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
      {children}
    </div>
  );
}

export function Field({
  label,
  error,
  children,
}: {
  label: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-slate-700">{label}</span>
      {children}
      {error ? <span className="mt-1 block text-sm text-red-600">{error}</span> : null}
    </label>
  );
}

const inputClass =
  "w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900 placeholder-slate-400 " +
  "focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200";

export function TextInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={inputClass} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={inputClass} />;
}

export function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={`${inputClass} resize-y`} />;
}

export function Checkbox({
  label,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  return (
    <label className="flex items-center gap-2 text-sm text-slate-700">
      <input
        type="checkbox"
        {...props}
        className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
      />
      {label}
    </label>
  );
}

export function Button({
  variant = "primary",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" }) {
  const styles = {
    primary:
      "bg-blue-600 text-white hover:bg-blue-700 focus:ring-blue-300 disabled:bg-blue-300",
    secondary:
      "bg-white text-slate-700 border border-slate-300 hover:bg-slate-50 focus:ring-slate-200",
    danger: "bg-white text-red-600 border border-red-300 hover:bg-red-50 focus:ring-red-200",
  }[variant];
  return (
    <button
      {...props}
      className={`rounded-lg px-5 py-2.5 text-sm font-semibold focus:outline-none focus:ring-2 ${styles} disabled:cursor-not-allowed`}
    />
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
      {message}
    </div>
  );
}
