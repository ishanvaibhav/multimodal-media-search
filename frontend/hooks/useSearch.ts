"use client";

import { useCallback, useRef, useState } from "react";
import { api } from "@/lib/api";
import type {
  HistoryItem,
  SearchFilters,
  SearchMode,
  SearchResponse,
} from "@/lib/types";
import { daysAgoIso, toIsoDate } from "@/lib/format";

export const DEFAULT_FILTERS: SearchFilters = {
  video_ids: [],
  date_from: "",
  date_to: "",
  min_similarity: 0,
  top_k: 50,
  final_results: 5,
  fine_search: true,
  temporal_grouping: true,
  sort_by: "relevance",
  sort_order: "desc",
  media_types: [],
  min_duration: null,
  max_duration: null,
  status: "",
  media_type: "",
};

export function useSearch() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("accurate");
  const [filters, setFilters] = useState<SearchFilters>(DEFAULT_FILTERS);
  const [results, setResults] = useState<SearchResponse | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const requestSeq = useRef(0);

  const setFilter = useCallback(
    <K extends keyof SearchFilters>(key: K, value: SearchFilters[K]) => {
      setFilters((f) => ({ ...f, [key]: value }));
    },
    [],
  );

  const quickRange = useCallback(
    (days: number | null) => {
      if (days == null) {
        setFilter("date_from", "");
        setFilter("date_to", "");
      } else if (days === 0) {
        const today = toIsoDate(new Date());
        setFilter("date_from", today);
        setFilter("date_to", today);
      } else {
        setFilter("date_from", daysAgoIso(days));
        setFilter("date_to", toIsoDate(new Date()));
      }
    },
    [setFilter],
  );

  const loadHistory = useCallback(async () => {
    try {
      const res = await api.searchHistory();
      setHistory(res.items);
    } catch {
      /* history is non-critical */
    }
  }, []);

  const runSearch = useCallback(async () => {
    if (!query.trim()) {
      setError("Enter a query first.");
      return;
    }
    abortRef.current?.abort(); // cancel any in-flight request
    const controller = new AbortController();
    abortRef.current = controller;
    const seq = ++requestSeq.current;

    setSearching(true);
    setError(null);
    try {
      const res = await api.search(
        {
          query: query.trim(),
          mode,
          ...filters,
          video_ids: filters.video_ids.length ? filters.video_ids : [],
        },
        controller.signal,
      );
      if (seq === requestSeq.current) {
        setResults(res);
      }
    } catch (err) {
      if (seq === requestSeq.current && !(err instanceof DOMException && err.name === "AbortError")) {
        setError(err instanceof Error ? err.message : "Search failed");
      }
    } finally {
      if (seq === requestSeq.current) setSearching(false);
    }
  }, [query, mode, filters]);

  const runSearchRef = useRef<(q: string) => Promise<void>>(async () => {});
  runSearchRef.current = async (q: string) => {
    setQuery(q);
    setSearching(true);
    setError(null);
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const seq = ++requestSeq.current;
    try {
      const res = await api.search(
        { query: q.trim(), mode, ...filters, video_ids: filters.video_ids.length ? filters.video_ids : [] },
        controller.signal,
      );
      if (seq === requestSeq.current) setResults(res);
    } catch (err) {
      if (seq === requestSeq.current && !(err instanceof DOMException && err.name === "AbortError")) {
        setError(err instanceof Error ? err.message : "Search failed");
      }
    } finally {
      if (seq === requestSeq.current) setSearching(false);
    }
  };

  const repeatSearch = useCallback(async (item: HistoryItem) => {
    setQuery(item.query);
    if (item.mode) setMode(item.mode);
    await runSearchRef.current(item.query);
  }, []);

  const clearResults = useCallback(() => {
    setResults(null);
    setError(null);
  }, []);

  const clearSearch = useCallback(() => {
    setQuery("");
    setFilters(DEFAULT_FILTERS);
    setMode("accurate");
    setResults(null);
    setError(null);
  }, []);

  return {
    query,
    setQuery,
    mode,
    setMode,
    filters,
    setFilter,
    quickRange,
    results,
    searching,
    error,
    history,
    loadHistory,
    repeatSearch,
    runSearch,
    clearResults,
    clearSearch,
  };
}
