"use client";

import { useEffect } from "react";
import type { SearchFilters, SearchMode } from "@/lib/types";
import type { useSearch } from "@/hooks/useSearch";
import type { VideoItem } from "@/lib/types";
import { Button, Spinner, Toggle } from "../ui/primitives";

const QUICK_RANGES: Array<{ label: string; days: number | null }> = [
  { label: "Today", days: 0 },
  { label: "7 days", days: 7 },
  { label: "30 days", days: 30 },
  { label: "All time", days: null },
];

const MODES: Array<{ value: SearchMode; label: string; hint: string }> = [
  { value: "accurate", label: "Accurate", hint: "vector + fine search + rerank" },
  { value: "fast", label: "Fast", hint: "vector + temporal grouping" },
  { value: "metadata", label: "Metadata", hint: "filename / metadata match" },
];

export function SearchPanel({
  search,
  videos,
}: {
  search: ReturnType<typeof useSearch>;
  videos: VideoItem[];
}) {
  const {
    query,
    setQuery,
    mode,
    setMode,
    filters,
    setFilter,
    quickRange,
    runSearch,
    searching,
    clearSearch,
    history,
    loadHistory,
    repeatSearch,
  } = search;

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  return (
    <section className="panel">
      <div className="panel-header">
        <h2 className="text-sm font-semibold text-slate-100">Search</h2>
        <Button onClick={clearSearch}>Clear Search</Button>
      </div>

      <div className="space-y-4 p-4">
        <div className="flex gap-2">
          <input
            className="input flex-1"
            placeholder="Describe a moment… e.g. “person wearing a black shirt”"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void runSearch();
            }}
          />
          <Button variant="primary" onClick={() => void runSearch()} disabled={searching}>
            {searching ? <Spinner size={14} /> : "🔍"} Search
          </Button>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {MODES.map((m) => (
            <button
              key={m.value}
              onClick={() => setMode(m.value)}
              title={m.hint}
              className={`rounded-lg border px-3 py-1.5 text-xs transition-colors ${
                mode === m.value
                  ? "border-accent bg-accent/20 text-accent-soft"
                  : "border-surface-600 text-slate-300 hover:bg-surface-700"
              }`}
            >
              {m.label}
            </button>
          ))}
          <span className="ml-1 text-[11px] text-slate-500">
            {MODES.find((m) => m.value === mode)?.hint}
          </span>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          <div>
            <label className="label">From date</label>
            <input
              type="date"
              className="input"
              value={filters.date_from}
              onChange={(e) => setFilter("date_from", e.target.value)}
            />
          </div>
          <div>
            <label className="label">To date</label>
            <input
              type="date"
              className="input"
              value={filters.date_to}
              onChange={(e) => setFilter("date_to", e.target.value)}
            />
          </div>
          <div>
            <label className="label">Video</label>
            <select
              className="input"
              value={filters.video_ids[0] ?? ""}
              onChange={(e) =>
                setFilter("video_ids", e.target.value ? [e.target.value] : [])
              }
            >
              <option value="">All videos</option>
              {videos.map((v) => (
                <option key={v.video_id} value={v.video_id}>
                  {v.filename}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">Media type</label>
            <select
              className="input"
              value={filters.media_type}
              onChange={(e) =>
                setFilter("media_type", e.target.value as "" | "video" | "image")
              }
            >
              <option value="">Images + videos</option>
              <option value="video">Videos only</option>
              <option value="image">Images only</option>
            </select>
          </div>
          <div>
            <label className="label">Sort by</label>
            <select
              className="input"
              value={filters.sort_by}
              onChange={(e) =>
                setFilter("sort_by", e.target.value as SearchFilters["sort_by"])
              }
            >
              <option value="relevance">Relevance</option>
              <option value="timestamp">Timestamp</option>
              <option value="upload_date">Upload date</option>
            </select>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-4">
          <div className="flex gap-1.5">
            {QUICK_RANGES.map((r) => (
              <button
                key={r.label}
                onClick={() => quickRange(r.days)}
                className="rounded-full border border-surface-600 px-3 py-1 text-xs text-slate-300 hover:bg-surface-700"
              >
                {r.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-slate-400">
              Min similarity:{" "}
              <span className="font-mono text-slate-200">
                {filters.min_similarity.toFixed(2)}
              </span>
            </span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={filters.min_similarity}
              onChange={(e) => setFilter("min_similarity", Number(e.target.value))}
              className="w-32 accent-accent"
            />
          </div>
          <Toggle
            label="Fine search"
            checked={filters.fine_search}
            onChange={(v) => setFilter("fine_search", v)}
          />
          <Toggle
            label="Temporal grouping"
            checked={filters.temporal_grouping}
            onChange={(v) => setFilter("temporal_grouping", v)}
          />
        </div>

        {history.length > 0 && (
          <div>
            <p className="label">Recent searches</p>
            <div className="flex flex-wrap gap-1.5">
              {history.slice(0, 8).map((h) => (
                <button
                  key={h.id}
                  onClick={() => void repeatSearch(h)}
                  className="rounded-full border border-surface-600 px-2.5 py-1 text-[11px] text-slate-400 hover:bg-surface-700 hover:text-slate-200"
                  title={`${h.result_count} result(s) · ${h.latency_ms ?? "?"} ms`}
                >
                  {h.query}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
