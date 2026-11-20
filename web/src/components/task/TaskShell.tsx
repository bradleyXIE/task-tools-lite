"use client";
import Link from "next/link";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { Suspense, useCallback, useRef, type KeyboardEvent } from "react";
import { useState } from "react";
import { api, errMsg } from "@/lib/api";
import { duration, time } from "@/lib/format";
import { usePoll } from "@/lib/usePoll";
import { Button, Card, ErrorNote, Loading, StatusBadge } from "../ui";
import { FindingsTab } from "./FindingsTab";
import { GraphTab } from "./GraphTab";
import { OverviewTab } from "./OverviewTab";
import { ReportTab } from "./ReportTab";
import { SessionsTab } from "./SessionsTab";
import { Stat, Tokens } from "./shared";

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "sessions", label: "Sessions" },
  { key: "graph", label: "Graph" },
  { key: "findings", label: "Findings" },
  { key: "report", label: "Report" },
] as const;
type TabKey = (typeof TABS)[number]["key"];

export function TaskShell({ id }: { id: string }) {
  return (
    <Suspense fallback={<Loading />}>
      <Shell id={id} />
    </Suspense>
  );
}

function Shell({ id }: { id: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const raw = params.get("tab");
  const tab: TabKey = TABS.some((t) => t.key === raw) ? (raw as TabKey) : "overview";

  const task = usePoll(() => api.tasks.get(id), 2000, [id]);
  const goals = usePoll(() => api.tasks.goals(id), 2000, [id]);
  const hints = usePoll(() => api.tasks.hints(id), 4000, [id]);
  const sessions = usePoll(() => api.tasks.sessions(id), task.data?.status === "running" ? 3000 : 0, [id, task.data?.status]);
  const [actionError, setActionError] = useState("");
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  const setTab = useCallback((k: TabKey) => {
    const sp = new URLSearchParams(params.toString());
    if (k === "overview") sp.delete("tab"); else sp.set("tab", k);
    const q = sp.toString();
    router.replace(q ? `${pathname}?${q}` : pathname, { scroll: false });
  }, [params, pathname, router]);

  async function act(fn: () => Promise<unknown>, after?: () => void) {
    setActionError("");
    try { await fn(); after?.(); await Promise.all([task.reload(), goals.reload(), hints.reload()]); }
    catch (e) { setActionError(errMsg(e)); }
  }

  function onKey(e: KeyboardEvent) {
    const i = TABS.findIndex((t) => t.key === tab);
    let n = -1;
    if (e.key === "ArrowRight") n = (i + 1) % TABS.length;
    else if (e.key === "ArrowLeft") n = (i + TABS.length - 1) % TABS.length;
    else if (e.key === "Home") n = 0;
    else if (e.key === "End") n = TABS.length - 1;
    if (n < 0) return;
    e.preventDefault();
    setTab(TABS[n].key);
    tabRefs.current[TABS[n].key]?.focus();
  }

  if (task.loading && !task.data) return <Loading />;
  if (!task.data) return <ErrorNote message={task.error || "Task not found"} />;
  const t = task.data;
  const terminal = ["done", "failed", "timeout"].includes(t.status);
  const totals = sessions.data?.totals;

  return (
    <div className="space-y-4">
      <Link href="/tasks" className="text-sm text-muted hover:text-fg">← All tasks</Link>
      <Card className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="mr-auto min-w-0 break-words text-xl font-semibold tracking-tight">{t.name || `Task ${t.id}`}</h1>
          <StatusBadge status={t.status} />
          {!terminal && t.status === "paused" && <Button onClick={() => act(() => api.tasks.control(id, "resume"))}>Resume</Button>}
          {!terminal && t.status === "running" && <Button onClick={() => act(() => api.tasks.control(id, "pause"))}>Pause</Button>}
        </div>
        <p className="whitespace-pre-wrap text-sm">{t.goal}</p>
        {t.description && <p className="text-sm text-muted">{t.description}</p>}
        <dl className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
          <Stat k="Run time" v={duration(t.run_seconds)} />
          <Stat k="Timeout" v={t.timeout_seconds ? duration(t.timeout_seconds) : "none"} />
          <Stat k="Goals" v={`${t.goals_met}/${t.goals_total}`} />
          <Stat k="Created" v={time(t.created_at)} />
          {totals && <Stat k="Steps / tool calls" v={`${totals.steps} / ${totals.tool_calls}`} />}
          {totals && <Stat k="Tokens" v={<Tokens input={totals.input_tokens} output={totals.output_tokens} />} />}
          {t.pause_reason && t.status === "paused" && <Stat k="Pause reason" v={t.pause_reason} />}
          {t.stop_reason && <Stat k="Stop reason" v={t.stop_reason} />}
          {t.completed_at && <Stat k="Completed" v={time(t.completed_at)} />}
        </dl>
      </Card>
      <ErrorNote message={actionError || task.error} />

      <div role="tablist" aria-label="Task sections" onKeyDown={onKey} className="no-print flex gap-1 overflow-x-auto border-b border-line">
        {TABS.map((x) => {
          const sel = x.key === tab;
          return (
            <button
              key={x.key}
              ref={(el) => { tabRefs.current[x.key] = el; }}
              role="tab"
              id={`tab-${x.key}`}
              aria-selected={sel}
              aria-controls={`panel-${x.key}`}
              tabIndex={sel ? 0 : -1}
              onClick={() => setTab(x.key)}
              className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium transition ${sel ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg"}`}
            >
              {x.label}
            </button>
          );
        })}
      </div>

      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} tabIndex={0} className="outline-offset-4">
        {tab === "overview" && (
          <OverviewTab task={t} goals={goals.data} hints={hints.data} loadingGoals={goals.loading} loadingHints={hints.loading} terminal={terminal} act={act} />
        )}
        {tab === "sessions" && <SessionsTab task={t} sessions={sessions.data} sessionsError={sessions.error} />}
        {tab === "graph" && <GraphTab task={t} />}
        {tab === "findings" && <FindingsTab task={t} />}
        {tab === "report" && <ReportTab task={t} />}
      </div>
    </div>
  );
}
