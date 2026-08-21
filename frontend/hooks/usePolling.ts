"use client";

import { useEffect, useRef } from "react";

export function usePolling(
  callback: () => void | Promise<void>,
  intervalMs: number,
  enabled = true,
) {
  const saved = useRef(callback);
  useEffect(() => {
    saved.current = callback;
  }, [callback]);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      try {
        await saved.current();
      } catch {
        /* polling errors are non-fatal */
      }
    };
    const id = setInterval(tick, intervalMs);
    void tick();
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [intervalMs, enabled]);
}
