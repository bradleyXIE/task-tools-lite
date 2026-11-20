import type {
  Asset, Fact, Finding, GraphData, ReportData, SessionsData,
  Goal, Hint, NewTask, NewTool, Task, TaskEvent, Tool, ToolCallResult,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!res.ok) {
    let msg = res.statusText || `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (j?.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {}
    throw new ApiError(res.status, msg);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

const body = (data: unknown) => JSON.stringify(data);
const qs = (p: Record<string, string | number | undefined>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(p)) if (v !== undefined && v !== "") s.set(k, String(v));
  const out = s.toString();
  return out ? `?${out}` : "";
};

export const api = {
  tasks: {
    list: (status?: string) =>
      req<{ tasks: Task[] }>(`/tasks${qs({ status })}`).then((r) => r.tasks),
    get: (id: string) => req<Task>(`/tasks/${id}`),
    create: (t: NewTask) => req<Task>("/tasks", { method: "POST", body: body(t) }),
    update: (id: string, p: { name?: string; description?: string }) =>
      req<Task>(`/tasks/${id}`, { method: "PATCH", body: body(p) }),
    remove: (id: string) => req<{ ok: boolean }>(`/tasks/${id}`, { method: "DELETE" }),
    control: (id: string, action: "pause" | "resume") =>
      req<Task>(`/tasks/${id}/control`, { method: "POST", body: body({ action }) }),
    goals: (id: string) => req<{ goals: Goal[] }>(`/tasks/${id}/goals`).then((r) => r.goals),
    addGoals: (id: string, texts: string[]) =>
      req<{ goals: Goal[] }>(`/tasks/${id}/goals`, { method: "POST", body: body({ texts }) }),
    markGoalMet: (id: string, gid: string, evidence?: string) =>
      req<Goal>(`/tasks/${id}/goals/${gid}`, {
        method: "PATCH",
        body: body({ met: true, evidence }),
      }),
    hints: (id: string) => req<{ hints: Hint[] }>(`/tasks/${id}/hints`).then((r) => r.hints),
    addHint: (id: string, text: string) =>
      req<Hint>(`/tasks/${id}/hints`, { method: "POST", body: body({ text }) }),
    events: (id: string, afterId?: string) =>
      req<{ events: TaskEvent[] }>(`/tasks/${id}/events${qs({ after_id: afterId })}`).then(
        (r) => r.events,
      ),
    sessions: (id: string) => req<SessionsData>(`/tasks/${id}/sessions`),
    graph: (id: string) => req<GraphData>(`/tasks/${id}/graph`),
    findings: (id: string) => req<{ findings: Finding[] }>(`/tasks/${id}/findings`).then((r) => r.findings),
    facts: (id: string) => req<{ facts: Fact[] }>(`/tasks/${id}/facts`).then((r) => r.facts),
    assets: (id: string) => req<{ assets: Asset[] }>(`/tasks/${id}/assets`).then((r) => r.assets),
    report: (id: string) => req<ReportData>(`/tasks/${id}/report`),
    reportDownloadUrl: (id: string) => `/api/tasks/${id}/report${qs({ download: 1 })}`,
  },
  tools: {
    list: () => req<{ tools: Tool[] }>("/tools").then((r) => r.tools),
    create: (t: NewTool) => req<Tool>("/tools", { method: "POST", body: body(t) }),
    update: (id: string, p: Partial<Pick<Tool, "description" | "input_schema" | "enabled" | "config" | "read_only">>) =>
      req<Tool>(`/tools/${id}`, { method: "PATCH", body: body(p) }),
    remove: (id: string) => req<{ ok: boolean }>(`/tools/${id}`, { method: "DELETE" }),
    call: (name: string, args: Record<string, unknown>, taskId?: string) =>
      req<ToolCallResult>(`/tools/${encodeURIComponent(name)}/call`, {
        method: "POST",
        body: body({ args, task_id: taskId }),
      }),
  },
};

export const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));
