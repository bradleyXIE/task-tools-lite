"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { time } from "@/lib/format";
import type { SessionsData, Task, TaskEvent } from "@/lib/types";
import { Markdown } from "../Markdown";
import { Card, Empty, ErrorNote, Loading } from "../ui";
import { JsonBlock, ToolRow, Tokens, outputOk } from "./shared";

type Item =
  | { type: "step"; step: number; llm?: TaskEvent; tools: TaskEvent[] }
  | { type: "note"; ev: TaskEvent }
  | { type: "manual"; ev: TaskEvent };

function buildTimeline(events: TaskEvent[], manual: boolean): Item[] {
  const items: Item[] = [];
  const steps = new Map<number, Extract<Item, { type: "step" }>>();
  for (const ev of events) {
    if (manual) { items.push({ type: "manual", ev }); continue; }
    if (ev.step != null && (ev.kind === "llm" || ev.kind === "tool_call")) {
      let g = steps.get(ev.step);
      if (!g) { g = { type: "step", step: ev.step, tools: [] }; steps.set(ev.step, g); items.push(g); }
      if (ev.kind === "llm") g.llm = ev; else g.tools.push(ev);
    } else items.push({ type: "note", ev });
  }
  return items;
}

const usage = (ev?: TaskEvent) => {
  const u = (ev?.output as { usage?: { input_tokens?: number; output_tokens?: number } } | null)?.usage;
  return { i: u?.input_tokens ?? 0, o: u?.output_tokens ?? 0 };
};
const llmText = (ev?: TaskEvent) => {
  const t = (ev?.output as { text?: unknown } | null)?.text;
  return typeof t === "string" ? t : "";
};

export function SessionsTab({
  task, sessions, sessionsError,
}: { task: Task; sessions: SessionsData | null; sessionsError: string }) {
  const id = task.id;
  const live = task.status === "running";
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const lastId = useRef("0");
  const [sel, setSel] = useState<string | null>(null); // "r<no>" | "manual"

  // Incremental event fetch: only events after the last seen id.
  useEffect(() => {
    let alive = true;
    setEvents([]); lastId.current = "0"; setLoading(true);
    const tick = async () => {
      try {
        const fresh = await api.tasks.events(id, lastId.current);
        if (!alive) return;
        setError("");
        if (fresh.length) {
          lastId.current = fresh[fresh.length - 1].id;
          setEvents((prev) => [...prev, ...fresh]);
        }
      } catch (e) { if (alive) setError(errMsg(e)); }
      finally { if (alive) setLoading(false); }
    };
    tick();
    const t = setInterval(tick, 2000);
    return () => { alive = false; clearInterval(t); };
  }, [id]);

  const list = sessions?.sessions ?? [];
  const keyOf = (run: number | null) => (run == null ? "manual" : `r${run}`);
  // default selection: latest run
  const selected = sel && list.some((s) => keyOf(s.run_no) === sel) ? sel : list.length ? keyOf((list.filter((x) => x.run_no != null).at(-1) ?? list[list.length - 1]).run_no) : null;
  const session = list.find((s) => keyOf(s.run_no) === selected);

  const runEvents = useMemo(
    () => events.filter((e) => (session?.run_no == null ? e.run_no == null : e.run_no === session.run_no)),
    [events, session?.run_no],
  );
  const timeline = useMemo(() => buildTimeline(runEvents, session?.run_no == null), [runEvents, session?.run_no]);

  // scroll handling: follow only while pinned to bottom
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const onScroll = () => {
    const el = scroller.current;
    if (el) pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  };
  useEffect(() => {
    const el = scroller.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [runEvents.length]);
  useEffect(() => { pinned.current = true; }, [selected]);

  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (k: string) => setOpen((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n; });

  if (loading && !sessions) return <Loading />;
  if (!list.length && !events.length) {
    return <Card><ErrorNote message={error || sessionsError} /><Empty>No sessions yet. The agent has not run.</Empty></Card>;
  }

  return (
    <div className="grid gap-4 md:grid-cols-[15rem_minmax(0,1fr)]">
      <nav aria-label="Runs" className="space-y-2">
        <ul className="flex gap-2 overflow-x-auto pb-1 md:flex-col md:overflow-visible md:pb-0">
          {list.map((s) => {
            const k = keyOf(s.run_no);
            const on = k === selected;
            return (
              <li key={k} className="shrink-0 md:shrink">
                <button
                  type="button"
                  aria-current={on ? "true" : undefined}
                  onClick={() => setSel(k)}
                  className={`w-44 rounded-lg border p-3 text-left text-xs transition md:w-full ${on ? "border-accent bg-accent/10" : "border-line bg-surface hover:bg-line/40"}`}
                >
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold">{s.label}</span>
                    {live && s.run_no === list.filter((x) => x.run_no != null).at(-1)?.run_no && s.run_no != null && (
                      <span className="inline-flex items-center gap-1 text-sky-600 dark:text-sky-300"><span className="size-1.5 animate-pulse rounded-full bg-current" aria-hidden />Live</span>
                    )}
                  </div>
                  <div className="mt-1 text-muted">{s.steps} steps · {s.tool_calls} calls{s.errors ? <span className="text-red-600 dark:text-red-400"> · {s.errors} err</span> : null}</div>
                  <Tokens input={s.input_tokens} output={s.output_tokens} className="mt-0.5 block text-muted" />
                  <div className="mt-0.5 text-muted">{time(s.started_at)}</div>
                </button>
              </li>
            );
          })}
        </ul>
        {sessions && (
          <p className="hidden rounded-lg border border-dashed border-line p-3 text-xs text-muted md:block">
            <span className="font-medium text-fg">Total</span><br />
            {sessions.totals.steps} steps · {sessions.totals.tool_calls} calls · {sessions.totals.errors} errors<br />
            <Tokens input={sessions.totals.input_tokens} output={sessions.totals.output_tokens} />
          </p>
        )}
      </nav>

      <Card className="min-w-0 space-y-3">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold">{session?.label ?? "Timeline"}</h2>
          {live && <span className="inline-flex items-center gap-1 text-xs text-sky-600 dark:text-sky-300"><span className="size-1.5 animate-pulse rounded-full bg-current" aria-hidden />Live</span>}
          {session && <Tokens input={session.input_tokens} output={session.output_tokens} className="ml-auto text-xs text-muted" />}
        </div>
        <ErrorNote message={error || sessionsError} />
        {!timeline.length ? <Empty>No events in this session.</Empty> : (
          <div ref={scroller} onScroll={onScroll} tabIndex={0} aria-label="Session timeline" className="max-h-[36rem] space-y-4 overflow-y-auto pr-1">
            {timeline.map((it, idx) => {
              if (it.type === "note") return <Note key={`n${it.ev.id}`} ev={it.ev} />;
              if (it.type === "manual") return (
                <ul key={`m${it.ev.id}`}><ToolRow t={{ id: it.ev.id, name: it.ev.tool_name || it.ev.kind, ok: outputOk(it.ev.output, it.ev.kind), input: it.ev.input, output: it.ev.output }} open={open.has(it.ev.id)} onToggle={() => toggle(it.ev.id)} /></ul>
              );
              const u = usage(it.llm);
              const text = llmText(it.llm);
              const lk = `l${it.step}-${idx}`;
              return (
                <section key={`s${it.step}`} aria-label={`Step ${it.step}`} className="space-y-2">
                  <header className="flex items-center gap-2 text-xs">
                    <h3 className="rounded bg-line/60 px-1.5 py-0.5 font-semibold">Step {it.step}</h3>
                    {it.llm && <time className="text-muted">{time(it.llm.created_at)}</time>}
                    {it.llm && <Tokens input={u.i} output={u.o} className="ml-auto text-muted" />}
                  </header>
                  {text && (
                    <div className="rounded-md bg-violet-500/5 p-2 text-sm">
                      <div className={open.has(lk) ? "" : "line-clamp-3"}><Markdown className="prose-md-sm">{text}</Markdown></div>
                      {(text.length > 220 || text.split("\n").length > 3) && (
                        <button type="button" aria-expanded={open.has(lk)} onClick={() => toggle(lk)} className="mt-1 text-xs font-medium text-accent hover:underline">
                          {open.has(lk) ? "Show less" : "Show more"}
                        </button>
                      )}
                    </div>
                  )}
                  {it.tools.length > 0 && (
                    <ul className="space-y-1.5">
                      {it.tools.map((e) => (
                        <ToolRow key={e.id} t={{ id: e.id, name: e.tool_name, ok: outputOk(e.output, e.kind), input: e.input, output: e.output }} open={open.has(e.id)} onToggle={() => toggle(e.id)} />
                      ))}
                    </ul>
                  )}
                </section>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}

function Note({ ev }: { ev: TaskEvent }) {
  const o = ev.output as { status?: string; message?: string; text?: string } | string | null;
  const msg = typeof o === "string" ? o : o?.message ?? o?.text ?? o?.status ?? "";
  const color = ev.kind === "error" ? "text-red-600 dark:text-red-400" : "text-sky-600 dark:text-sky-400";
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 border-l-2 border-line pl-2 text-xs text-muted">
      <span className={`font-medium ${color}`}>{ev.kind}</span>
      {msg ? <span className="min-w-0 break-words">{String(msg)}</span> : <JsonBlock label="data" v={ev.output ?? ev.input} />}
      <time className="ml-auto">{time(ev.created_at)}</time>
    </div>
  );
}
