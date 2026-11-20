"use client";
import { useState } from "react";
import { api, errMsg } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import type { Tool, ToolCallResult } from "@/lib/types";
import { Button, Card, Empty, ErrorNote, Field, Loading, inputCls } from "./ui";

const parseJson = (s: string): Record<string, unknown> => {
  const v = JSON.parse(s || "{}");
  if (typeof v !== "object" || v === null || Array.isArray(v)) throw new Error("Must be a JSON object");
  return v;
};

export function ToolsPanel() {
  const { data, error, loading, reload } = usePoll(api.tools.list);
  const [actionError, setActionError] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [testing, setTesting] = useState<Tool | null>(null);

  async function act(fn: () => Promise<unknown>) {
    setActionError("");
    try { await fn(); await reload(); } catch (e) { setActionError(errMsg(e)); }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="mr-auto text-xl font-semibold tracking-tight">Tools</h1>
        <Button variant="primary" onClick={() => setShowForm((v) => !v)}>{showForm ? "Close" : "New custom tool"}</Button>
      </div>
      {showForm && <Card><NewToolForm onCreated={() => { setShowForm(false); reload(); }} /></Card>}
      <ErrorNote message={actionError || error} />

      {loading && !data ? <Loading /> : !data?.length ? <Empty>No tools registered.</Empty> : (
        <div className="overflow-x-auto rounded-lg border border-line bg-surface">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-line text-xs text-muted">
              <tr><th className="p-3 font-medium">Name</th><th className="p-3 font-medium">Kind</th><th className="p-3 font-medium">Description</th><th className="p-3 font-medium">Enabled</th><th className="p-3" /></tr>
            </thead>
            <tbody className="divide-y divide-line">
              {data.map((t) => <ToolRow key={t.id} t={t} act={act} onTest={() => setTesting(t)} />)}
            </tbody>
          </table>
        </div>
      )}
      {testing && <TestPanel tool={testing} onClose={() => setTesting(null)} />}
    </div>
  );
}

function ToolRow({ t, act, onTest }: { t: Tool; act: (fn: () => Promise<unknown>) => void; onTest: () => void }) {
  const [editing, setEditing] = useState(false);
  const [desc, setDesc] = useState(t.description);
  return (
    <tr className="align-top">
      <td className="p-3 font-mono text-xs">{t.name}{t.read_only && <span className="ml-1 text-muted" title="read-only">(ro)</span>}</td>
      <td className="p-3"><span className="rounded bg-line/60 px-1.5 py-0.5 text-xs">{t.kind}</span></td>
      <td className="p-3">
        {editing ? (
          <div className="flex flex-col gap-2">
            <textarea aria-label={`Description of ${t.name}`} rows={3} value={desc} onChange={(e) => setDesc(e.target.value)} className={inputCls} />
            <div className="flex gap-2">
              <Button variant="primary" onClick={() => act(async () => { await api.tools.update(t.id, { description: desc }); setEditing(false); })}>Save</Button>
              {t.builtin_default_description && t.builtin_default_description !== desc && (
                <Button onClick={() => setDesc(t.builtin_default_description!)}>Reset to default</Button>
              )}
              <Button onClick={() => { setDesc(t.description); setEditing(false); }}>Cancel</Button>
            </div>
          </div>
        ) : <span className="text-muted">{t.description}</span>}
      </td>
      <td className="p-3">
        <input type="checkbox" role="switch" aria-label={`Enable ${t.name}`} checked={t.enabled} className="size-4 accent-[var(--accent)]"
          onChange={(e) => act(() => api.tools.update(t.id, { enabled: e.target.checked }))} />
      </td>
      <td className="whitespace-nowrap p-3 text-right">
        <div className="flex justify-end gap-2">
          <Button onClick={onTest}>Test</Button>
          {!editing && <Button onClick={() => setEditing(true)}>Edit</Button>}
          {t.kind !== "builtin" && <Button variant="danger" onClick={() => confirm(`Delete tool ${t.name}?`) && act(() => api.tools.remove(t.id))}>Delete</Button>}
        </div>
      </td>
    </tr>
  );
}

function TestPanel({ tool, onClose }: { tool: Tool; onClose: () => void }) {
  const [args, setArgs] = useState("{}");
  const [taskId, setTaskId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ToolCallResult | null>(null);

  async function run() {
    setBusy(true); setError(""); setResult(null);
    try {
      setResult(await api.tools.call(tool.name, parseJson(args), taskId.trim() || undefined));
    } catch (e) { setError(errMsg(e)); }
    finally { setBusy(false); }
  }

  return (
    <Card className="space-y-3">
      <div className="flex items-center gap-3">
        <h2 className="mr-auto text-sm font-semibold">Test call: <code>{tool.name}</code></h2>
        <Button onClick={onClose}>Close</Button>
      </div>
      <details className="text-xs text-muted">
        <summary className="cursor-pointer">Input schema</summary>
        <pre className="mt-1 overflow-auto rounded bg-line/40 p-2 font-mono">{JSON.stringify(tool.input_schema, null, 2)}</pre>
      </details>
      <Field label="Args (JSON)"><textarea rows={4} value={args} onChange={(e) => setArgs(e.target.value)} className={`${inputCls} font-mono`} spellCheck={false} /></Field>
      <Field label="Task id (optional)" hint="Logs the call to that task's event log"><input value={taskId} onChange={(e) => setTaskId(e.target.value)} className={inputCls} /></Field>
      <Button variant="primary" onClick={run} disabled={busy}>{busy ? "Calling…" : "Call tool"}</Button>
      <ErrorNote message={error} />
      {result && (
        <div className="space-y-1" aria-live="polite">
          <p className={`text-xs font-medium ${result.ok ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>{result.ok ? "ok" : `error: ${result.error ?? "unknown"}`}</p>
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded bg-line/40 p-2 font-mono text-xs">{typeof result.output === "string" ? result.output : JSON.stringify(result.output, null, 2)}</pre>
        </div>
      )}
    </Card>
  );
}

const DEFAULT_CFG = {
  http: '{\n  "method": "GET",\n  "url": "https://example.com/{{path}}",\n  "headers": {},\n  "body_template": null\n}',
  script: '{\n  "code": "result = {\\"sum\\": args[\\"a\\"] + args[\\"b\\"]}"\n}',
};
const DEFAULT_SCHEMA = '{\n  "type": "object",\n  "properties": {},\n  "required": []\n}';

function NewToolForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"http" | "script">("http");
  const [description, setDescription] = useState("");
  const [schema, setSchema] = useState(DEFAULT_SCHEMA);
  const [config, setConfig] = useState(DEFAULT_CFG.http);
  const [readOnly, setReadOnly] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await api.tools.create({
        name: name.trim(), kind, description: description.trim(),
        input_schema: parseJson(schema), config: parseJson(config), read_only: readOnly, enabled: true,
      });
      onCreated();
    } catch (e) { setError(errMsg(e)); }
    finally { setBusy(false); }
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Name *"><input required pattern="[A-Za-z0-9_\-]+" value={name} onChange={(e) => setName(e.target.value)} className={inputCls} /></Field>
        <Field label="Kind">
          <select value={kind} onChange={(e) => { const k = e.target.value as "http" | "script"; setKind(k); setConfig(DEFAULT_CFG[k]); }} className={inputCls}>
            <option value="http">http</option><option value="script">script (Python)</option>
          </select>
        </Field>
        <label className="flex items-end gap-2 pb-1.5 text-sm"><input type="checkbox" checked={readOnly} onChange={(e) => setReadOnly(e.target.checked)} className="size-4 accent-[var(--accent)]" />Read-only</label>
      </div>
      <Field label="Description *"><input required value={description} onChange={(e) => setDescription(e.target.value)} className={inputCls} /></Field>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Input schema (JSON Schema)"><textarea rows={7} value={schema} onChange={(e) => setSchema(e.target.value)} className={`${inputCls} font-mono`} spellCheck={false} /></Field>
        <Field label="Config (JSON)" hint={kind === "http" ? "{{arg}} placeholders are filled from call args" : "Set `result`; args arrive as JSON on stdin"}>
          <textarea rows={7} value={config} onChange={(e) => setConfig(e.target.value)} className={`${inputCls} font-mono`} spellCheck={false} />
        </Field>
      </div>
      <ErrorNote message={error} />
      <Button type="submit" variant="primary" disabled={busy}>{busy ? "Creating…" : "Create tool"}</Button>
    </form>
  );
}
