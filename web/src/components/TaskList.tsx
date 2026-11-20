"use client";
import Link from "next/link";
import { useState } from "react";
import { api, errMsg } from "@/lib/api";
import { duration } from "@/lib/format";
import { usePoll } from "@/lib/usePoll";
import type { Task } from "@/lib/types";
import { NewTaskForm } from "./NewTaskForm";
import { Button, Card, Empty, ErrorNote, Loading, StatusBadge, inputCls } from "./ui";

const FILTERS = ["", "running", "paused", "created", "done", "failed", "timeout"];

export function TaskList() {
  const [status, setStatus] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [actionError, setActionError] = useState("");
  const { data, error, loading, reload } = usePoll(() => api.tasks.list(status || undefined), 3000, [status]);

  async function act(fn: () => Promise<unknown>) {
    setActionError("");
    try { await fn(); await reload(); } catch (e) { setActionError(errMsg(e)); }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="mr-auto text-xl font-semibold tracking-tight">Tasks</h1>
        <select aria-label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value)} className={`${inputCls} !w-auto`}>
          {FILTERS.map((f) => <option key={f} value={f}>{f || "all statuses"}</option>)}
        </select>
        <Button variant="primary" onClick={() => setShowForm((v) => !v)}>{showForm ? "Close" : "New task"}</Button>
      </div>

      {showForm && (
        <Card>
          <NewTaskForm onCreated={() => { setShowForm(false); reload(); }} />
        </Card>
      )}

      <ErrorNote message={actionError || error} />

      {loading && !data ? <Loading /> : !data?.length ? (
        <Empty>{status ? `No ${status} tasks.` : "No tasks yet. Create one to get started."}</Empty>
      ) : (
        <ul className="divide-y divide-line rounded-lg border border-line bg-surface">
          {data.map((t) => <Row key={t.id} t={t} act={act} />)}
        </ul>
      )}
    </div>
  );
}

function Row({ t, act }: { t: Task; act: (fn: () => Promise<unknown>) => void }) {
  const terminal = ["done", "failed", "timeout"].includes(t.status);
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-2 p-3">
      <div className="min-w-0 flex-1 basis-64">
        <Link href={`/tasks/${t.id}`} className="block truncate font-medium hover:underline">{t.name || t.goal}</Link>
        <p className="truncate text-xs text-muted">{t.name ? t.goal : t.description}</p>
      </div>
      <StatusBadge status={t.status} />
      <span className="w-16 text-xs tabular-nums text-muted" title="Goals met">{t.goals_met}/{t.goals_total} goals</span>
      <span className="w-14 text-xs tabular-nums text-muted" title="Active run time">{duration(t.run_seconds)}</span>
      <div className="flex gap-2">
        {!terminal && (t.status === "paused"
          ? <Button onClick={() => act(() => api.tasks.control(t.id, "resume"))}>Resume</Button>
          : t.status === "running" && <Button onClick={() => act(() => api.tasks.control(t.id, "pause"))}>Pause</Button>)}
        <Button variant="danger" disabled={t.status === "running"} title={t.status === "running" ? "Pause before deleting" : "Delete"}
          onClick={() => confirm("Delete this task?") && act(() => api.tasks.remove(t.id))}>Delete</Button>
      </div>
    </li>
  );
}
