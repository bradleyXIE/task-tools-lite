# Contract 3: goal reports, sessions, graph, report reader
Binding for backend and web agents. Read CONTRACT.md / CONTRACT2.md for the rest. Plan: /Users/xiezhijie/.claude/plans/compiled-sprouting-tide.md

IDs are strings in JSON. Timestamps are ISO strings. All paths under /api.

## Schema additions (backend appends to backend/app/db/schema.sql, idempotent)
```
ALTER TABLE task_goals  ADD COLUMN IF NOT EXISTS report TEXT NOT NULL DEFAULT '';
ALTER TABLE tasks       ADD COLUMN IF NOT EXISTS summary_report TEXT NOT NULL DEFAULT '';
ALTER TABLE task_events ADD COLUMN IF NOT EXISTS run_no INT;
ALTER TABLE task_events ADD COLUMN IF NOT EXISTS step INT;
```

## DTO changes
- Task DTO adds `summary_report: string`.
- Goal DTO adds `report: string`.
- Event DTO adds `run_no: number|null`, `step: number|null`.
- `llm` event output = `{text: string, usage: {input_tokens:number, output_tokens:number}}` (one per LLM call, even with empty text). `tool_call` events carry run_no/step of the LLM call that issued them. `status` events carry `run_no` when written by the runner (else null).

## Tools
- `prove_goal {goal_id, evidence, report?}` — `report` Markdown (description tells the model: what was done, requests/commands, observed result, conclusion). Stored in task_goals.report. `PATCH /api/tasks/{id}/goals/{gid}` accepts `{met:true, evidence?, report?}`.
- `goal_met {reason, report?}` — report stored in tasks.summary_report; `reason` remains stop_reason.
- Runner: `run_no` = (max run_no for the task)+1 at runner start; `step` starts at 1 and increments per LLM call. `ToolContext` gets optional `run_no`, `step`; `registry.call_tool(pool, name, args, task_id=None, run_no=None, step=None)`. `svc.add_event(pool, task_id, kind, tool_name='', input=None, output=None, run_no=None, step=None)`. `llm.call` result gains `usage: {input_tokens, output_tokens}` (anthropic `usage`; openai `prompt_tokens/completion_tokens` mapped; zeros if absent).

## New endpoints (backend/app/tasks/views.py + router)
- `GET /tasks/{id}/sessions` →
  `{sessions:[{run_no:number|null, label:string, started_at, ended_at|null, steps:number, tool_calls:number, errors:number, input_tokens:number, output_tokens:number}], totals:{steps, tool_calls, errors, input_tokens, output_tokens}}` — one session per distinct run_no ascending; events with null run_no form a final session `{run_no:null,label:"Manual calls"}` (omit when none). label `"Run N"`.
- `GET /tasks/{id}/findings` → `{findings:[{id, task_id, vulnclass, name, severity, summary, evidence, report, status, created_at, updated_at}]}` newest first. `GET /tasks/{id}/facts` → `{facts:[{id, task_id, summary, detail, status, created_at}]}`. `GET /tasks/{id}/assets` → `{assets:[{id, task_id, type, value, attrs, tested, created_at}]}`.
- `GET /tasks/{id}/graph` → `{nodes:[Node], edges:[Edge]}`
  `Node = {id:string, type:'task'|'goal'|'step'|'fact'|'finding'|'asset', label:string, status:'ok'|'error'|'met'|'unmet'|'running'|string, run_no:number|null, step:number|null, data:object}`; node ids: `task`, `goal:<id>`, `step:<run_no|m>:<step>`, `fact:<id>`, `finding:<id>`, `asset:<id>`.
  `Edge = {id:string, source:string, target:string, kind:'flow'|'proves'|'creates'|'plan'}`.
  Rules: root `task` → each goal (`plan`); step nodes ordered by (run_no, step), consecutive steps joined by `flow` (first step of each run also linked from `task` with `flow`); step label = first sentence (<=80 chars) of its llm text, else the first tool name; step.data = `{text, tools:[{id,name,ok,input,output}], input_tokens, output_tokens}` and status `error` if any tool_call failed (output.ok false or event error) else `ok`, last step of a still-running task `running`; `prove_goal` call → `proves` edge step→`goal:<goal_id>` (goal node status met/unmet from DB); `record_fact` / `report_finding` / `insert_assets` calls → `creates` edges to nodes built from ids found in the call output (backend must make sure those tools' outputs include the created id(s): `{id}` or `{ids:[...]}` or items with `id`); entity nodes only for ids that exist in the DB. Cap: at most 400 nodes; if exceeded drop `asset` nodes first and set `truncated:true` at the top level.
- `GET /tasks/{id}/report` → `{markdown:string, generated_at:string}`; `?download=1` returns `text/markdown; charset=utf-8` with `Content-Disposition: attachment; filename="task-<id>-report.md"`. Markdown structure (H1 title, then H2 sections in this order, skipping empty ones): `# <name or "Task N"> — Report`, a metadata list (status, run time, goals met/total, steps, tool calls, tokens); `## Summary` (summary_report, else stop_reason, else "_No summary yet._"); `## Goals` (`### ✓|○ <goal text>` + `**Evidence:** ...` + report body demoted so its own headings start at H4); `## Findings` (severity-sorted critical→info, `### [SEVERITY] name`, summary, evidence, report); `## Facts`; `## Assets` (GFM table type|value|tested); `## Hints`; `## Process` (per-run line: steps/tool calls/tokens).

## Web conventions
- Add exact-pinned deps: `react-markdown`, `remark-gfm`, `@xyflow/react`.
- New files under `web/src/components/task/` (TaskShell, OverviewTab, SessionsTab, GraphTab, FindingsTab, ReportTab) + `web/src/components/Markdown.tsx`; `app/tasks/[id]/page.tsx` keeps rendering `<TaskShell id=.../>`; delete or reduce `TaskDetail.tsx` after moving its content. Tabs in URL `?tab=overview|sessions|graph|findings|report` (default overview). Keep existing visual language (ui.tsx tokens, light/dark, mobile-friendly). Markdown renderer must not render raw HTML.
- Until the backend lands the web agent may develop against typed mock fixtures behind an `NEXT_PUBLIC_MOCK=1` flag or local fixtures in tests, but the final build must call the real endpoints above.
