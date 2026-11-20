import type { Edge, Node } from "@xyflow/react";
import type { GraphData, GraphNode } from "@/lib/types";

export const SIZE: Record<GraphNode["type"], { w: number; h: number }> = {
  task: { w: 200, h: 64 }, goal: { w: 220, h: 64 }, step: { w: 190, h: 64 },
  fact: { w: 190, h: 44 }, finding: { w: 190, h: 44 }, asset: { w: 190, h: 44 },
};
const COL = 230, ENT_GAP = 54, LANE_PAD = 56, GOAL_GAP = 140;

/** Deterministic layout: one lane per run (steps left->right), entities stacked under
 *  their step, goals in a column on the right, task root at the far left. */
export function layoutGraph(g: GraphData, selectedId: string | null): { nodes: Node[]; edges: Edge[] } {
  const byId = new Map(g.nodes.map((n) => [n.id, n]));
  const pos = new Map<string, { x: number; y: number }>();

  const steps = g.nodes.filter((n) => n.type === "step");
  const runKey = (n: GraphNode) => (n.run_no == null ? Number.MAX_SAFE_INTEGER : n.run_no);
  const runs = [...new Set(steps.map(runKey))].sort((a, b) => a - b);

  // entity -> owning step (first creates edge)
  const owner = new Map<string, string>();
  for (const e of g.edges) {
    if (e.kind !== "creates") continue;
    const t = byId.get(e.target), s = byId.get(e.source);
    if (t && s?.type === "step" && !owner.has(t.id)) owner.set(t.id, s.id);
  }
  const under = new Map<string, GraphNode[]>();
  for (const n of g.nodes) {
    const o = owner.get(n.id);
    if (o) under.set(o, [...(under.get(o) ?? []), n]);
  }

  let y = 0, maxX = 0;
  const laneCenters: number[] = [];
  for (const r of runs) {
    const lane = steps.filter((s) => runKey(s) === r).sort((a, b) => (a.step ?? 0) - (b.step ?? 0));
    const maxStack = Math.max(0, ...lane.map((s) => under.get(s.id)?.length ?? 0));
    lane.forEach((s, i) => {
      const x = i * COL;
      pos.set(s.id, { x, y });
      (under.get(s.id) ?? []).forEach((e, j) => pos.set(e.id, { x, y: y + SIZE.step.h + 40 + j * ENT_GAP }));
      maxX = Math.max(maxX, x + SIZE.step.w);
    });
    laneCenters.push(y + SIZE.step.h / 2);
    y += SIZE.step.h + (maxStack ? 40 + maxStack * ENT_GAP : 0) + LANE_PAD;
  }
  // unowned entities: tidy row at bottom
  const loose = g.nodes.filter((n) => ["fact", "finding", "asset"].includes(n.type) && !pos.has(n.id));
  loose.forEach((n, i) => pos.set(n.id, { x: (i % 6) * COL, y: y + Math.floor(i / 6) * ENT_GAP }));
  if (loose.length) { maxX = Math.max(maxX, Math.min(loose.length, 6) * COL - 40); y += Math.ceil(loose.length / 6) * ENT_GAP + LANE_PAD; }

  const goals = g.nodes.filter((n) => n.type === "goal");
  const goalX = maxX + GOAL_GAP;
  goals.forEach((n, i) => pos.set(n.id, { x: goalX, y: i * (SIZE.goal.h + 24) }));

  const midY = laneCenters.length ? (laneCenters[0] + laneCenters[laneCenters.length - 1]) / 2 : 0;
  pos.set("task", { x: -COL - 20, y: midY - SIZE.task.h / 2 });

  const nodes: Node[] = g.nodes.map((n) => ({
    id: n.id,
    type: "card",
    position: pos.get(n.id) ?? { x: 0, y: 0 },
    data: { node: n },
    selected: n.id === selectedId,
    draggable: false,
    ariaLabel: `${n.type} ${n.label}, status ${n.status}`,
  }));
  const edges: Edge[] = g.edges
    .filter((e) => byId.has(e.source) && byId.has(e.target))
    .map((e) => {
      const base: Edge = { id: e.id, source: e.source, target: e.target, sourceHandle: "r", targetHandle: "l", type: "smoothstep", style: {} };
      if (e.kind === "creates") return { ...base, sourceHandle: "b", targetHandle: "t", type: "default", style: { stroke: "#a8a29e", strokeWidth: 1.2 } };
      if (e.kind === "proves") return { ...base, type: "default", animated: false, style: { stroke: "#059669", strokeWidth: 2, strokeDasharray: "6 4" } };
      if (e.kind === "plan") return { ...base, type: "default", style: { stroke: "#a8a29e", strokeWidth: 1, opacity: 0.35 } };
      return { ...base, markerEnd: { type: "arrowclosed" as never, color: "#78716c" }, style: { stroke: "#78716c", strokeWidth: 1.5 } };
    });
  return { nodes, edges };
}
