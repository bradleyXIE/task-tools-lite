"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { time } from "@/lib/format";
import type { Goal, Hint, Task } from "@/lib/types";
import { Markdown } from "../Markdown";
import { Button, Card, Empty, Loading, inputCls } from "../ui";
import { Disclosure } from "./shared";

export function OverviewTab({
  task, goals, hints, loadingGoals, loadingHints, terminal, act,
}: {
  task: Task;
  goals: Goal[] | null;
  hints: Hint[] | null;
  loadingGoals: boolean;
  loadingHints: boolean;
  terminal: boolean;
  act: (fn: () => Promise<unknown>, after?: () => void) => Promise<void>;
}) {
  const [goalText, setGoalText] = useState("");
  const [hintText, setHintText] = useState("");
  const id = task.id;
  return (
    <div className="space-y-4">
      {task.summary_report && (
        <Card className="space-y-2">
          <h2 className="text-sm font-semibold">Summary</h2>
          <Markdown>{task.summary_report}</Markdown>
        </Card>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        <Card className="space-y-3">
          <h2 className="text-sm font-semibold">Goals</h2>
          {loadingGoals && !goals ? <Loading /> : !goals?.length ? <Empty>No goals defined.</Empty> : (
            <ul className="space-y-3">
              {goals.map((g) => (
                <li key={g.id} className="flex items-start gap-2 text-sm">
                  <span className={`mt-0.5 ${g.met ? "text-emerald-600 dark:text-emerald-400" : "text-muted"}`}>
                    <span aria-hidden>{g.met ? "✓" : "○"}</span><span className="sr-only">{g.met ? "met" : "not met"}</span>
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className={g.met ? "text-muted" : ""}>{g.text}</p>
                    {g.evidence && <p className="mt-0.5 text-xs text-muted">Evidence: {g.evidence}</p>}
                    {g.report && (
                      <Disclosure label="Report" className="mt-1">
                        <div className="rounded-md border border-line bg-bg p-3"><Markdown className="prose-md-sm">{g.report}</Markdown></div>
                      </Disclosure>
                    )}
                  </div>
                  {!g.met && !terminal && (
                    <Button className="!px-2 !py-0.5 text-xs" onClick={() => {
                      const ev = prompt("Evidence (optional)") ?? undefined;
                      act(() => api.tasks.markGoalMet(id, g.id, ev));
                    }}>Mark met</Button>
                  )}
                </li>
              ))}
            </ul>
          )}
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (goalText.trim()) act(() => api.tasks.addGoals(id, [goalText.trim()]), () => setGoalText("")); }}>
            <input aria-label="New goal" value={goalText} onChange={(e) => setGoalText(e.target.value)} placeholder="Add a goal" className={inputCls} />
            <Button type="submit" disabled={!goalText.trim()}>Add</Button>
          </form>
        </Card>

        <Card className="space-y-3">
          <h2 className="text-sm font-semibold">Hints</h2>
          {loadingHints && !hints ? <Loading /> : !hints?.length ? <Empty>No hints yet.</Empty> : (
            <ul className="space-y-2">
              {hints.map((h) => (
                <li key={h.id} className="text-sm">{h.text} <span className="text-xs text-muted">· {time(h.created_at)}</span></li>
              ))}
            </ul>
          )}
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (hintText.trim()) act(() => api.tasks.addHint(id, hintText.trim()), () => setHintText("")); }}>
            <input aria-label="New hint" value={hintText} onChange={(e) => setHintText(e.target.value)} placeholder="Nudge the agent" className={inputCls} />
            <Button type="submit" disabled={!hintText.trim()}>Send</Button>
          </form>
        </Card>
      </div>
    </div>
  );
}
