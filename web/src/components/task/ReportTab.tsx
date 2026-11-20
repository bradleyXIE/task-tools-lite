"use client";
import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { time } from "@/lib/format";
import { usePoll } from "@/lib/usePoll";
import type { Task } from "@/lib/types";
import { Markdown, extractHeadings } from "../Markdown";
import { Button, Card, Empty, ErrorNote, Loading } from "../ui";

export function ReportTab({ task }: { task: Task }) {
  const running = task.status === "running";
  const rep = usePoll(() => api.tasks.report(task.id), running ? 5000 : 0, [task.id, task.status, task.goals_met]);
  const [copied, setCopied] = useState<"" | "ok" | "fail">("");
  const [force, setForce] = useState(false);
  const md = rep.data?.markdown ?? "";
  const toc = useMemo(() => extractHeadings(md).filter((h) => h.level >= 2 && h.level <= 3), [md]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(md);
      setCopied("ok");
    } catch {
      try {
        const ta = document.createElement("textarea");
        ta.value = md; document.body.appendChild(ta); ta.select();
        const ok = document.execCommand("copy");
        document.body.removeChild(ta);
        setCopied(ok ? "ok" : "fail");
      } catch { setCopied("fail"); }
    }
    setTimeout(() => setCopied(""), 2000);
  }

  if (rep.loading && !rep.data) return <Loading />;
  if (!rep.data) return <ErrorNote message={rep.error || "Report unavailable"} />;

  const nothingYet = task.goals_met === 0 && !task.summary_report && !force;
  if (nothingYet) {
    return (
      <Card className="space-y-3 text-center">
        <Empty>No goals have been proven yet, so there is no report to read.</Empty>
        <div><Button onClick={() => setForce(true)}>Show draft anyway</Button></div>
      </Card>
    );
  }

  return (
    <div className="space-y-3">
      <div className="no-print flex flex-wrap items-center gap-2">
        <Button onClick={copy}>{copied === "ok" ? "Copied" : copied === "fail" ? "Copy failed" : "Copy Markdown"}</Button>
        <a
          href={api.tasks.reportDownloadUrl(task.id)}
          download={`task-${task.id}-report.md`}
          className="rounded-md border border-line bg-surface px-3 py-1.5 text-sm font-medium transition hover:bg-line/50"
        >
          Download .md
        </a>
        <Button onClick={() => window.print()}>Print</Button>
        <Button onClick={() => rep.reload()}>Refresh</Button>
        <span className="ml-auto text-xs text-muted" aria-live="polite">
          {running ? "Live · refreshing · " : ""}Generated {time(rep.data.generated_at)}
        </span>
      </div>
      <ErrorNote message={rep.error} />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_14rem]">
        <Card className="print-area min-w-0 !p-5 sm:!p-8">
          <div className="mx-auto max-w-[46rem]">
            <Markdown headingIds>{md}</Markdown>
          </div>
        </Card>
        {toc.length > 0 && (
          <nav aria-label="Table of contents" className="no-print order-first lg:order-none">
            <details className="rounded-lg border border-line bg-surface p-3 lg:sticky lg:top-4 lg:border-0 lg:bg-transparent lg:p-0" open>
              <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-muted lg:cursor-default lg:list-none">Contents</summary>
              <ul className="mt-2 space-y-1 text-sm">
                {toc.map((h) => (
                  <li key={h.slug} className={h.level === 3 ? "pl-3" : ""}>
                    <a
                      href={`#${h.slug}`}
                      onClick={(e) => { e.preventDefault(); document.getElementById(h.slug)?.scrollIntoView({ behavior: "smooth", block: "start" }); }}
                      className="block truncate text-muted hover:text-fg"
                    >
                      {h.text}
                    </a>
                  </li>
                ))}
              </ul>
            </details>
          </nav>
        )}
      </div>
    </div>
  );
}
