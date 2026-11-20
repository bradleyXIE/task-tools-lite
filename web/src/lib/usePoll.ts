"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { errMsg } from "./api";

/** Load data now and every `ms` (0 = once). Keeps last good data on refresh errors. */
export function usePoll<T>(fn: () => Promise<T>, ms = 0, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const reload = useCallback(async () => {
    try {
      setData(await fnRef.current());
      setError("");
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    reload();
    if (!ms) return;
    const t = setInterval(reload, ms);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ms, reload, ...deps]);

  return { data, error, loading, reload, setData };
}
