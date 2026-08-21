"use client";

import { useCallback, useState } from "react";
import { api } from "@/lib/api";
import type { VideoItem } from "@/lib/types";
import { usePolling } from "./usePolling";

export function useMedia() {
  const [items, setItems] = useState<VideoItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState("uploaded_at");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.listMedia({ search, sort_by: sortBy, sort_order: sortOrder });
      setItems(res.items);
      setTotal(res.total);
    } catch {
      /* shown via empty/error state */
    } finally {
      setLoading(false);
    }
  }, [search, sortBy, sortOrder]);

  const hasActiveJobs = items.some(
    (v) =>
      v.status !== "ready" &&
      v.status !== "failed" &&
      v.status !== "cancelled",
  );
  usePolling(refresh, 2500, hasActiveJobs);

  const remove = useCallback(async (videoId: string) => {
    setBusy(true);
    try {
      await api.deleteMedia(videoId);
      await refresh();
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  const reindex = useCallback(async (videoId: string) => {
    await api.reindexMedia(videoId);
    await refresh();
  }, [refresh]);

  return {
    items,
    total,
    loading,
    busy,
    search,
    setSearch,
    sortBy,
    setSortBy,
    sortOrder,
    setSortOrder,
    refresh,
    remove,
    reindex,
  };
}
