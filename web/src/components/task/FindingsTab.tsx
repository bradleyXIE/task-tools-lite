"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { time } from "@/lib/format";
import { usePoll } from "@/lib/usePoll";
import type { Task } from "@/lib/types";
import { Markdown } from "../Markdown";
import { Card, Empty, ErrorNote, Loading } from "../ui";
import { Disclosure, SeverityBadge } from "./shared";

const SUBS = ["findings", "facts", "assets"] as const;
type Sub = (typeof SUBS)[number];
const RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };

export function FindingsTab({ task }: { task: Task }) {
  const ms = task.status === "running" ? 4000 : 0;
  const findings = usePoll(() => api.tasks.findings(task.id), ms, [task.id, task.status]);
  const facts = usePoll(() => api.tasks.facts(task.id), ms, [task.id, task.status]);
  const assets = usePoll(() => api.tasks.assets(task.id), ms, [task.id, task.status]);
  const [sub, setSub] = useState<Sub>("findings");
  const counts: Record<Sub, number | undefined> = {
    findings: findings.data?.length, facts: facts.data?.length, assets: assets.data?.length,
  };
  const cur = { findings, facts, assets }[sub];

  return (
    <div className="space-y-3">
      <div role="group" aria-label="Collections" className="flex gap-2">
        {SUBS.map((s) => (
          <button
            key={s}
            type="button"
            aria-pressed={sub === s}
            onClick={() => setSub(s)}
            className={`rounded-full border px-3 py-1 text-sm capitalize transition ${sub === s ? "border-accent bg-accent/10 font-medium" : "border-line bg-surface text-muted hover:text-fg"}`}
          >
            {s}{counts[s] != null && <span className="ml-1.5 text-xs text-muted">{counts[s]}</span>}
          </button>
        ))}
      </div>
      <ErrorNote message={cur.error} />
      {cur.loading && !cur.data ? <Loading /> : sub === "findings" ? (
        <FindingList items={findings.data ?? []} />
      ) : sub === "facts" ? (
        <FactList items={facts.data ?? []} />
      ) : (
        <AssetTable items={assets.data ?? []} />
      )}
    </div>
  );
}

function FindingList({ items }: { items: import("@/lib/types").Finding[] }) {
  if (!items.length) return <Card><Empty>No findings reported.</Empty></Card>;
  const sorted = [...items].sort((a, b) => (RANK[a.severity.toLowerCase()] ?? 9) - (RANK[b.severity.toLowerCase()] ?? 9));
  return (
    <ul className="space-y-3">
      {sorted.map((f) => (
        <li key={f.id}>
          <Card className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <SeverityBadge severity={f.severity} />
              <h3 className="min-w-0 break-words text-sm font-semibold">{f.name}</h3>
              {f.vulnclass && <span className="rounded bg-line/60 px-1.5 py-0.5 text-xs text-muted">{f.vulnclass}</span>}
              <span className="ml-auto text-xs text-muted">{f.status} · {time(f.created_at)}</span>
            </div>
            {f.summary && <p className="text-sm">{f.summary}</p>}
            {f.evidence && (
              <Disclosure label="Evidence"><div className="rounded-md border border-line bg-bg p-3"><Markdown className="prose-md-sm">{f.evidence}</Markdown></div></Disclosure>
            )}
            {f.report && (
              <Disclosure label="Report"><div className="rounded-md border border-line bg-bg p-3"><Markdown className="prose-md-sm">{f.report}</Markdown></div></Disclosure>
            )}
          </Card>
        </li>
      ))}
    </ul>
  );
}

function FactList({ items }: { items: import("@/lib/types").Fact[] }) {
  if (!items.length) return <Card><Empty>No facts recorded.</Empty></Card>;
  return (
    <ul className="space-y-3">
      {items.map((f) => (
        <li key={f.id}>
          <Card className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="min-w-0 break-words text-sm font-medium">{f.summary}</h3>
              <span className="ml-auto text-xs text-muted">{f.status} · {time(f.created_at)}</span>
            </div>
            {f.detail && <Disclosure label="Detail"><div className="rounded-md border border-line bg-bg p-3"><Markdown className="prose-md-sm">{f.detail}</Markdown></div></Disclosure>}
          </Card>
        </li>
      ))}
    </ul>
  );
}

function AssetTable({ items }: { items: import("@/lib/types").Asset[] }) {
  if (!items.length) return <Card><Empty>No assets recorded.</Empty></Card>;
  return (
    <Card className="overflow-x-auto !p-0">
      <table className="w-full min-w-[32rem] text-left text-sm">
        <caption className="sr-only">Assets</caption>
        <thead className="border-b border-line text-xs text-muted">
          <tr><th scope="col" className="px-3 py-2 font-medium">Type</th><th scope="col" className="px-3 py-2 font-medium">Value</th><th scope="col" className="px-3 py-2 font-medium">Tested</th><th scope="col" className="px-3 py-2 font-medium">Attributes</th></tr>
        </thead>
        <tbody>
          {items.map((a) => (
            <tr key={a.id} className="border-b border-line last:border-0 align-top">
              <td className="px-3 py-2"><span className="rounded bg-line/60 px-1.5 py-0.5 text-xs">{a.type}</span></td>
              <td className="break-all px-3 py-2 font-mono text-xs">{a.value}</td>
              <td className="px-3 py-2">{a.tested ? <span className="text-emerald-600 dark:text-emerald-400"><span aria-hidden>✓</span> tested</span> : <span className="text-muted"><span aria-hidden>○</span> untested</span>}</td>
              <td className="break-all px-3 py-2 font-mono text-xs text-muted">{a.attrs && Object.keys(a.attrs).length ? JSON.stringify(a.attrs) : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
