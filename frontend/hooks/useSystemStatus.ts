"use client";

import { useCallback, useState } from "react";
import { api } from "@/lib/api";
import type { HealthResponse } from "@/lib/types";
import { usePolling } from "./usePolling";

/**
 * Shared system status: polls /api/health and exposes the fields the UI uses
 * to communicate backend state (maintenance, semantic mode, model mismatch).
 * The backend remains authoritative — the frontend only reflects its state.
 */
export function useSystemStatus() {
  const [health, setHealth] = useState<HealthResponse | null>(null);

  const refresh = useCallback(async () => {
    try {
      setHealth(await api.health());
    } catch {
      setHealth(null);
    }
  }, []);

  usePolling(refresh, 4000);

  return {
    health,
    maintenance: health?.details?.maintenance ?? false,
    semanticOk: health?.details?.semantic_search ?? true,
    modelMismatch: health?.details?.model_mismatch ?? false,
    refresh,
  };
}
