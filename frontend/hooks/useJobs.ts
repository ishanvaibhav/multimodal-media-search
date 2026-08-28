"use client";

import { useCallback, useState } from "react";
import { api } from "@/lib/api";
import type { JobItem } from "@/lib/types";
import { usePolling } from "./usePolling";

export function useJobs(enabled = true) {
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setJobs(await api.listJobs());
    } catch {
      /* ignore transient errors */
    } finally {
      setLoading(false);
    }
  }, []);

  const active = jobs.some(
    (j) => j.status === "queued" || j.status === "running" || j.status === "cancelling",
  );
  usePolling(refresh, 1500, enabled && active);

  const cancel = useCallback(async (jobId: string) => {
    await api.cancelJob(jobId);
    await refresh();
  }, [refresh]);

  return { jobs, loading, active, refresh, cancel };
}
