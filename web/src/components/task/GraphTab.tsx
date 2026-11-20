"use client";
import {
  Background, Controls, Handle, MiniMap, Position, ReactFlow, ReactFlowProvider, useReactFlow,
  type Node, type NodeChange, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { memo, useCallback, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import type { GraphNode, Task } from "@/lib/types";
import { Markdown } from "../Markdown";
import { Button, Card, Empty, ErrorNote, Loading } from "../ui";
import { layoutGraph, SIZE } from "./graphLayout";
import { SeverityBadge, ToolRow, Tokens, type ToolRowData } from "./shared";

const SEV_HEX: Record<string, string> = { critical: "#dc2626", high: "#ea580c", medium: "#d97706", low: "#0284c7", info: "#78716c" };

function style(n: GraphNode): { cls: string; glyph: string; hex: string } {
  const s = n.status;
  switch (n.type) {
    case "task": return { cls: "border-accent bg-accent/10", glyph: "◆", hex: "#0f766e" };
    case "goal":
      return s === "met"
        ? { cls: "border-emerald-500 bg-emerald-500/10", glyph: "✓", hex: "#059669" }
        : { cls: "border-line bg-surface border-dashed", glyph: "○", hex: "#a8a29e" };
    case "step":
      if (s === "error") return { cls: "border-red-500 bg-red-500/10", glyph: "✗", hex: "#dc2626" };
      if (s === "running") return { cls: "border-sky-500 bg-sky-500/10", glyph: "●", hex: "#0284c7" };
      return { cls: "border-line bg-surface", glyph: "✓", hex: "#78716c" };
    case "finding": {
      const sev = String(n.data?.severity ?? "info").toLowerCase();
      const hex = SEV_HEX[sev] ?? SEV_HEX.info;
      return { cls: "bg-surface", glyph: "⚑", hex };
    }
    case "fact": return { cls: "border-violet-400 bg-violet-500/10", glyph: "§", hex: "#8b5cf6" };
    default: return { cls: "border-line bg-surface", glyph: "◇", hex: "#a8a29e" };
  }
}

const CardNode = memo(function CardNode({ data, selected }: NodeProps) {
  const n = (data as { node: GraphNode }).node;
  const st = style(n);
  const sz = SIZE[n.type];
  const label = n.type === "step" ? `${n.run_no != null ? `R${n.run_no}·` : ""}${n.step}  ${n.label}` : n.label;
  return (
    <div
      style={{ width: sz.w, height: sz.h, borderColor: n.type === "finding" ? st.hex : undefined }}
      className={`flex items-center gap-2 rounded-lg border px-2.5 text-xs shadow-sm ${st.cls} ${selected ? "ring-2 ring-accent" : ""}`}
    >
      <Handle id="l" type="target" position={Position.Left} className="!size-1.5 !border-0 !bg-muted" />
      <Handle id="t" type="target" position={Position.Top} className="!size-1.5 !border-0 !bg-muted" />
      <span aria-hidden className="shrink-0 text-sm font-bold" style={{ color: st.hex }}>{st.glyph}</span>
      <div className="min-w-0">
        <div className="text-[10px] uppercase tracking-wide text-muted">{n.type}</div>
        <div className="line-clamp-2 break-words font-medium leading-tight">{label}</div>
      </div>
      <Handle id="r" type="source" position={Position.Right} className="!size-1.5 !border-0 !bg-muted" />
      <Handle id="b" type="source" position={Position.Bottom} className="!size-1.5 !border-0 !bg-muted" />
    </div>
  );
});
const nodeTypes = { card: CardNode };

export function GraphTab({ task }: { task: Task }) {
  const running = task.status === "running";
  const g = usePoll(() => api.tasks.graph(task.id), running ? 4000 : 0, [task.id, task.status]);
  const [selId, setSelId] = useState<string | null>(null);

  const laid = useMemo(() => (g.data ? layoutGraph(g.data, selId) : null), [g.data, selId]);
  const selected = g.data?.nodes.find((n) => n.id === selId) ?? null;

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    for (const c of changes) if (c.type === "select" && c.selected) setSelId(c.id);
  }, []);

  if (g.loading && !g.data) return <Loading />;
  if (!g.data || !laid) return <ErrorNote message={g.error || "No graph data"} />;
  if (g.data.nodes.length <= 1 && !g.data.edges.length) return <Card><Empty>Nothing to graph yet.</Empty></Card>;

  return (
    <div className="space-y-3">
      <ErrorNote message={g.error} />
      {g.data.truncated && (
        <p className="rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
          Graph truncated to 400 nodes (asset nodes omitted first).
        </p>
      )}
      <Legend />
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="relative h-[34rem] overflow-hidden rounded-lg border border-line bg-surface" role="region" aria-label="Process graph">
          <ReactFlowProvider>
            <ReactFlow
              nodes={laid.nodes}
              edges={laid.edges}
              nodeTypes={nodeTypes}
              onNodesChange={onNodesChange}
              onPaneClick={() => setSelId(null)}
              nodesDraggable={false}
              nodesConnectable={false}
              fitView
              fitViewOptions={{ padding: 0.15, maxZoom: 1, minZoom: 0.5 }}
              minZoom={0.1}
              maxZoom={1.8}
              onlyRenderVisibleElements
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={24} />
              <Controls showInteractive={false} />
              <MiniMap pannable zoomable ariaLabel="Graph minimap" nodeColor={(n: Node) => style((n.data as { node: GraphNode }).node).hex} maskColor="rgba(120,113,108,0.15)" />
              <FitButton />
            </ReactFlow>
          </ReactFlowProvider>
        </div>
        <Detail node={selected} onClose={() => setSelId(null)} />
      </div>
    </div>
  );
}

function FitButton() {
  const { fitView } = useReactFlow();
  return (
    <div className="absolute right-2 top-2 z-10">
      <Button className="!px-2 !py-1 text-xs" onClick={() => fitView({ padding: 0.15, maxZoom: 1, duration: 200 })}>Fit view</Button>
    </div>
  );
}

function Legend() {
  const items: [string, string, string][] = [
    ["✓", "#78716c", "step ok"], ["✗", "#dc2626", "step error"], ["●", "#0284c7", "running"],
    ["✓", "#059669", "goal met"], ["○", "#a8a29e", "goal open"], ["⚑", "#ea580c", "finding"],
    ["§", "#8b5cf6", "fact"], ["◇", "#a8a29e", "asset"],
  ];
  return (
    <ul aria-label="Legend" className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
      {items.map(([g, c, l]) => (
        <li key={l} className="flex items-center gap-1"><span aria-hidden style={{ color: c }} className="font-bold">{g}</span>{l}</li>
      ))}
      <li className="flex items-center gap-1"><span aria-hidden className="inline-block w-5 border-t-2 border-dashed border-emerald-600" />proves</li>
      <li className="flex items-center gap-1"><span aria-hidden className="inline-block w-5 border-t border-muted" />flow / creates</li>
    </ul>
  );
}

const MD_KEYS = ["text", "report", "evidence", "summary", "detail"];

function Detail({ node, onClose }: { node: GraphNode | null; onClose: () => void }) {
  if (!node) {
    return <Card className="text-sm text-muted">Select a node (click or focus + Enter) to see its details.</Card>;
  }
  const d = node.data ?? {};
  const tools = Array.isArray(d.tools) ? (d.tools as ToolRowData[]) : [];
  const rest = Object.entries(d).filter(([k, v]) => k !== "tools" && !MD_KEYS.includes(k) && !k.endsWith("_tokens") && v !== "" && v != null);
  return (
    <Card className="max-h-[34rem] space-y-3 overflow-y-auto">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="text-[11px] uppercase tracking-wide text-muted">
            {node.type}{node.run_no != null ? ` · run ${node.run_no}` : ""}{node.step != null ? ` · step ${node.step}` : ""} · {node.status}
          </div>
          <h3 className="break-words text-sm font-semibold">{node.label}</h3>
        </div>
        <Button className="!px-2 !py-0.5 text-xs" onClick={onClose} aria-label="Close details">×</Button>
      </div>
      {node.type === "finding" && typeof d.severity === "string" && <SeverityBadge severity={d.severity} />}
      {node.type === "step" && typeof d.input_tokens === "number" && (
        <Tokens input={d.input_tokens as number} output={(d.output_tokens as number) ?? 0} className="text-xs text-muted" />
      )}
      {MD_KEYS.map((k) => typeof d[k] === "string" && d[k] ? (
        <div key={k}>
          <div className="text-[11px] font-medium uppercase tracking-wide text-muted">{k}</div>
          <Markdown className="prose-md-sm">{d[k] as string}</Markdown>
        </div>
      ) : null)}
      {rest.length > 0 && (
        <dl className="space-y-1 text-xs">
          {rest.map(([k, v]) => (
            <div key={k}><dt className="text-muted">{k}</dt><dd className="break-words font-mono">{typeof v === "string" ? v : JSON.stringify(v)}</dd></div>
          ))}
        </dl>
      )}
      {tools.length > 0 && (
        <div>
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">Tool calls</div>
          <ul className="space-y-1.5">{tools.map((t, i) => <ToolRow key={t.id ?? i} t={t} />)}</ul>
        </div>
      )}
    </Card>
  );
}
