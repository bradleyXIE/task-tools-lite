export function duration(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h}h ${m}m` : m ? `${m}m ${r}s` : `${r}s`;
}

export function time(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}

export function pretty(v: unknown): string {
  if (v === null || v === undefined || v === "") return "";
  return typeof v === "string" ? v : JSON.stringify(v, null, 2);
}
