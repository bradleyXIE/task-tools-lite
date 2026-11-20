"use client";
import { useState } from "react";
import { api, errMsg } from "@/lib/api";
import type { Task } from "@/lib/types";
import { Button, ErrorNote, Field, inputCls } from "./ui";

export function NewTaskForm({ onCreated }: { onCreated: (t: Task) => void }) {
  const [goal, setGoal] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const t = await api.tasks.create({
        goal: goal.trim(),
        name: name.trim() || undefined,
        description: description.trim() || undefined,
        start: true,
      });
      setGoal(""); setName(""); setDescription("");
      onCreated(t);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} /></Field>
        <Field label="Description"><input value={description} onChange={(e) => setDescription(e.target.value)} className={inputCls} /></Field>
      </div>
      <Field label="Goal *">
        <textarea required rows={3} value={goal} onChange={(e) => setGoal(e.target.value)} className={inputCls} placeholder="What should the agent accomplish?" />
      </Field>
      <ErrorNote message={error} />
      <Button type="submit" variant="primary" disabled={busy || !goal.trim()}>{busy ? "Creating…" : "Create and start"}</Button>
    </form>
  );
}
