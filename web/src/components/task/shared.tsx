"use client";
import { useState, type ReactNode } from "react";
import { pretty } from "@/lib/format";

export const fmtTokens = (n: number) =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 10_000 ? `${Math.round(n / 1000)}k` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);

export function Stat({ k, v }: { k: string; v: ReactNode }) {
  return <div><dt className="text-muted">{k}</dt><dd className="mt-0.5 font-medium">{v}</dd></div>;
}

export function Tokens({ input, output, className = "" }: { input: number; output: number; className?: string }) {
  return (
    <span className={`tabular-nums ${className}`} title={`${input} input / ${output} output tokens`}>
      <span aria-hidden>↑</span>{fmtTokens(input)} <span aria-hidden>↓</span>{fmtTokens(output)}
      <span className="sr-only"> input {input} output {output} tokens</span>
    </span>
  );
}

/** Toggle button with proper aria-expanded. */
export function Disclosure({
  label, children, defaultOpen = false, className = "",
}: { label: ReactNode; children: ReactNode; defaultOpen?: boolean; className?: string }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={className}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1 rounded text-xs font-medium text-accent hover:underline"
      >
        <span aria-hidden className={`inline-block transition-transform ${open ? "rotate-90" : ""}`}>▸</span>
        {label}
      </button>
      {open && <div className="mt-2">{children}</div>}
    </div>
  );
}

export function JsonBlock({ label, v }: { label: string; v: unknown }) {
  const s = pretty(v);
  if (!s) return null;
  return (
    <div className="mt-1.5">
      <div className="text-[11px] font-medium uppercase tracking-wide text-muted">{label}</div>
      <pre className="mt-0.5 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded bg-line/40 p-2 font-mono text-xs">{s}</pre>
    </div>
  );
}

/** One-line summary of a tool input object. */
export function inputSummary(input: unknown): string {
  if (input == null || input === "") return "";
  if (typeof input === "string") return input.slice(0, 140);
  if (typeof input === "object") {
    const o = input as Record<string, unknown>;
    const pref = ["command", "url", "query", "path", "file_path", "pattern", "text", "summary", "name", "goal_id"].find((k) => typeof o[k] === "string" && o[k]);
    if (pref) return String(o[pref]).replace(/\s+/g, " ").slice(0, 140);
    return JSON.stringify(o).slice(0, 140);
  }
  return String(input);
}

export function outputOk(output: unknown, kind?: string): boolean {
  if (kind === "error") return false;
  if (output && typeof output === "object" && "ok" in output) return (output as { ok?: unknown }).ok !== false;
  return true;
}

export interface ToolRowData { id: string; name: string; ok: boolean; input: unknown; output: unknown }

/** Tool call row, click to expand input/output. */
export function ToolRow({ t, open: openProp, onToggle }: { t: ToolRowData; open?: boolean; onToggle?: () => void }) {
  const [local, setLocal] = useState(false);
  const open = openProp ?? local;
  const toggle = onToggle ?? (() => setLocal((o) => !o));
  const sum = inputSummary(t.input);
  return (
    <li className="rounded-md border border-line">
      <button
        type="button"
        aria-expanded={open}
        onClick={toggle}
        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs hover:bg-line/30"
      >
        <span className={`font-semibold ${t.ok ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
          <span aria-hidden>{t.ok ? "✓" : "✗"}</span><span className="sr-only">{t.ok ? "succeeded" : "failed"}</span>
        </span>
        <code className="shrink-0 font-semibold">{t.name}</code>
        <span className="min-w-0 flex-1 truncate font-mono text-muted">{sum}</span>
        <span aria-hidden className={`text-muted transition-transform ${open ? "rotate-90" : ""}`}>▸</span>
      </button>
      {open && (
        <div className="border-t border-line px-2 pb-2">
          <JsonBlock label="input" v={t.input} />
          <JsonBlock label="output" v={t.output} />
        </div>
      )}
    </li>
  );
}

export const SEVERITY: Record<string, string> = {
  critical: "bg-red-600/15 text-red-700 dark:text-red-300",
  high: "bg-orange-500/15 text-orange-700 dark:text-orange-300",
  medium: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  low: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  info: "bg-stone-500/15 text-stone-600 dark:text-stone-300",
};
export function SeverityBadge({ severity }: { severity: string }) {
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${SEVERITY[severity.toLowerCase()] ?? SEVERITY.info}`}>
      {severity || "info"}
    </span>
  );
}
