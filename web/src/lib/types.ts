export type TaskStatus = "created" | "running" | "paused" | "done" | "failed" | "timeout";

export interface Task {
  id: string;
  name: string;
  description: string;
  goal: string;
  status: TaskStatus;
  paused: boolean;
  pause_reason: string;
  stop_reason: string;
  timeout_seconds: number;
  run_seconds: number;
  deadline_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
  goals_total: number;
  goals_met: number;
  summary_report: string;
}

export interface Goal {
  id: string;
  task_id: string;
  text: string;
  met: boolean;
  evidence: string;
  report: string;
  met_at: string | null;
  created_at: string;
}

export interface Hint {
  id: string;
  task_id: string;
  text: string;
  created_at: string;
}

export interface TaskEvent {
  id: string;
  task_id: string | null;
  kind: string;
  tool_name: string;
  input: unknown;
  output: unknown;
  run_no: number | null;
  step: number | null;
  created_at: string;
}

export type ToolKind = "builtin" | "http" | "script";

export interface Tool {
  id: string;
  name: string;
  kind: ToolKind;
  description: string;
  input_schema: Record<string, unknown>;
  config: Record<string, unknown>;
  read_only: boolean;
  enabled: boolean;
  builtin_default_description?: string;
}

export interface ToolCallResult {
  ok: boolean;
  output: unknown;
  error?: string;
}

export interface NewTask {
  name?: string;
  description?: string;
  goal: string;
  timeout_seconds?: number;
  start?: boolean;
  goals?: string[];
}

export interface NewTool {
  name: string;
  kind: "http" | "script";
  description: string;
  input_schema: Record<string, unknown>;
  config: Record<string, unknown>;
  read_only?: boolean;
  enabled?: boolean;
}

export interface Session {
  run_no: number | null;
  label: string;
  started_at: string;
  ended_at: string | null;
  steps: number;
  tool_calls: number;
  errors: number;
  input_tokens: number;
  output_tokens: number;
}
export interface SessionTotals {
  steps: number; tool_calls: number; errors: number; input_tokens: number; output_tokens: number;
}
export interface SessionsData { sessions: Session[]; totals: SessionTotals }

export type GraphNodeType = "task" | "goal" | "step" | "fact" | "finding" | "asset";
export interface GraphNode {
  id: string;
  type: GraphNodeType;
  label: string;
  status: string;
  run_no: number | null;
  step: number | null;
  data: Record<string, unknown>;
}
export interface GraphEdge {
  id: string; source: string; target: string;
  kind: "flow" | "proves" | "creates" | "plan";
}
export interface GraphData { nodes: GraphNode[]; edges: GraphEdge[]; truncated?: boolean }

export interface Finding {
  id: string; task_id: string; vulnclass: string; name: string; severity: string;
  summary: string; evidence: string; report: string; status: string;
  created_at: string; updated_at: string;
}
export interface Fact {
  id: string; task_id: string; summary: string; detail: string; status: string; created_at: string;
}
export interface Asset {
  id: string; task_id: string; type: string; value: string;
  attrs: Record<string, unknown>; tested: boolean; created_at: string;
}
export interface ReportData { markdown: string; generated_at: string }
