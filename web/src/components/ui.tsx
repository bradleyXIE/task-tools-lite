import type { ReactNode } from "react";
import type { TaskStatus } from "@/lib/types";

const STATUS: Record<TaskStatus, string> = {
  created: "bg-stone-500/15 text-stone-600 dark:text-stone-300",
  running: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  paused: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  done: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  failed: "bg-red-500/15 text-red-700 dark:text-red-300",
  timeout: "bg-orange-500/15 text-orange-700 dark:text-orange-300",
};

export function StatusBadge({ status }: { status: TaskStatus }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${STATUS[status] ?? STATUS.created}`}>
      {status === "running" && <span className="size-1.5 animate-pulse rounded-full bg-current" aria-hidden />}
      {status}
    </span>
  );
}

export const inputCls =
  "w-full rounded-md border border-line bg-surface px-3 py-1.5 text-sm placeholder:text-muted";

export function Button({
  variant = "default", className = "", ...p
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "primary" | "danger" }) {
  const v = {
    default: "border-line bg-surface hover:bg-line/50",
    primary: "border-transparent bg-accent text-bg hover:opacity-90",
    danger: "border-line bg-surface text-red-600 hover:bg-red-500/10 dark:text-red-400",
  }[variant];
  return (
    <button
      type="button"
      {...p}
      className={`rounded-md border px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${v} ${className}`}
    />
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="block text-xs text-muted">{hint}</span>}
    </label>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-lg border border-line bg-surface p-4 ${className}`}>{children}</section>;
}

export function ErrorNote({ message }: { message: string }) {
  if (!message) return null;
  return <p role="alert" className="rounded-md bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">{message}</p>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-8 text-center text-sm text-muted">{children}</p>;
}

export function Loading() {
  return <p className="py-8 text-center text-sm text-muted" aria-busy="true">Loading…</p>;
}
