"use client";

import { useState } from "react";
import type { SearchResponse, SearchResult } from "@/lib/types";
import { absoluteUrl, api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { copyText, downloadText } from "@/lib/clipboard";
import { contextText, resultCsv, resultJson, safeFileName } from "@/lib/context";
import { Badge, Button, EmptyState } from "../ui/primitives";
import { ContextViewer } from "../context/ContextViewer";

export function ResultsPanel({
  response,
  hidden,
  onToggleHidden,
  onClearResults,
  onPlay,
  onPlaySegment,
}: {
  response: SearchResponse | null;
  hidden: boolean;
  onToggleHidden: () => void;
  onClearResults: () => void;
  onPlay: (result: SearchResult) => void;
  onPlaySegment?: (result: SearchResult) => void;
}) {
  const [context, setContext] = useState<SearchResult | null>(null);
  const [feedback, setFeedback] = useState<Record<string, boolean>>({});
  const [flash, setFlash] = useState<string | null>(null);
  const [savedIds, setSavedIds] = useState<Set<string>>(new Set());

  const notify = (msg: string) => {
    setFlash(msg);
    window.setTimeout(() => setFlash(null), 1600);
  };

  const sendFeedback = (r: SearchResult, relevant: boolean) => {
    setFeedback((f) => ({ ...f, [r.frame_id || r.video_id]: relevant }));
    void api
      .sendFeedback({
        query: response?.query ?? "",
        relevant,
        video_id: r.video_id,
        frame_id: r.frame_id || undefined,
        timestamp: r.timestamp,
      })
      .catch(() => {});
  };

  const copyResult = async (r: SearchResult) => {
    const ok = await copyText(r.context_text ?? contextText(r, response?.query ?? ""));
    notify(ok ? "Copied!" : "Copy failed");
  };

  const saveResult = async (r: SearchResult) => {
    try {
      await api.saveContext({
        query: response?.query ?? "",
        video_id: r.video_id,
        filename: r.video_name,
        media_type: r.media_type,
        timestamp_seconds: r.timestamp,
        context_start: r.context_start,
        context_end: r.context_end,
        score: r.similarity,
        frame_id: r.frame_id || null,
        context_text: r.context_text ?? contextText(r, response?.query ?? ""),
        context_frames: r.context_frames,
        reason: r.context_reason ?? null,
      });
      setSavedIds((s) => new Set(s).add(r.frame_id || r.video_id));
      notify("Saved ✓");
    } catch {
      notify("Save failed");
    }
  };

  const downloadResult = (r: SearchResult, fmt: "txt" | "json" | "csv") => {
    const base = safeFileName(r.video_name.replace(/\.[^.]+$/, ""));
    if (fmt === "json") downloadText(`${base}.context.json`, resultJson(r, response?.query ?? ""), "application/json");
    else if (fmt === "csv") downloadText(`${base}.context.csv`, resultCsv(r, response?.query ?? ""), "text/csv");
    else downloadText(`${base}.context.txt`, r.context_text ?? contextText(r, response?.query ?? ""));
    notify("Downloaded ✓");
  };

  const copyAllResults = async () => {
    if (!response || response.results.length === 0) return;
    const block = response.results
      .map((r, i) => `# Result ${i + 1}\n${r.context_text ?? contextText(r, response.query)}`)
      .join("\n\n");
    const ok = await copyText(block);
    notify(ok ? "Copied all results!" : "Copy failed");
  };
  return (
    <section className="panel">
      <div className="panel-header">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-slate-100">Results</h2>
          {response && (
            <span className="text-xs text-slate-500">
              {response.results.length} moment{response.results.length === 1 ? "" : "s"} ·{" "}
              {response.took_ms} ms · {response.mode}
            </span>
          )}
          {flash && <span className="text-xs text-emerald-400">{flash}</span>}
        </div>
        <div className="flex items-center gap-2">
          {response && response.results.length > 0 && (
            <Button onClick={() => void copyAllResults()}>Copy All</Button>
          )}
          <Button onClick={onClearResults}>Clear Results</Button>
          <Button onClick={onToggleHidden}>
            {hidden ? "Show Results" : "Hide Results"}
          </Button>
        </div>
      </div>

      {!hidden && (
        <div className="p-4">
          {response && !response.semantic_search && (
            <div className="mb-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
              ⚠ Non-semantic embedding backend in use — results are approximate.
            </div>
          )}
          {response && response.rerank === "unavailable" && (
            <div className="mb-3 rounded-lg border border-surface-600 bg-surface-800 px-3 py-2 text-xs text-slate-400">
              Vector search complete · Gemini reranking unavailable.
            </div>
          )}
          {!response ? (
            <EmptyState
              title="No search yet"
              subtitle="Search your images and videos using natural language. Results appear here as temporal events, grouped so you don't see dozens of near-identical frames."
            />
          ) : response.results.length === 0 ? (
            <EmptyState
              title="No matching moments found."
              subtitle="Try a broader query, lower the similarity threshold, or enable fine search."
            />
          ) : (
            <ul className="space-y-3">
              {response.results.map((r, i) => {
                const fb = feedback[r.frame_id || r.video_id];
                const saved = savedIds.has(r.frame_id || r.video_id);
                return (
                  <li
                    key={`${r.frame_id}-${i}`}
                    className="rounded-lg border border-surface-700 bg-surface-850 p-3"
                  >
                    <div className="flex gap-3">
                      <div className="h-24 w-36 shrink-0 overflow-hidden rounded-md bg-surface-800">
                        {r.frame_url ? (
                          <img
                            src={absoluteUrl(r.frame_url)}
                            alt={`frame at ${r.timestamp_hms}`}
                            className="h-full w-full object-cover"
                            loading="lazy"
                          />
                        ) : (
                          <div className="flex h-full w-full items-center justify-center text-2xl">🎬</div>
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <div className="flex items-center gap-2">
                              <p className="truncate text-sm font-semibold text-slate-100">
                                {r.video_name}
                              </p>
                              <Badge tone={r.media_type === "image" ? "green" : "blue"}>
                                {r.media_type}
                              </Badge>
                            </div>
                            <p className="text-xs text-slate-500">
                              {r.media_type === "image"
                                ? "image"
                                : r.duration_hms
                                  ? `${r.duration_hms} long`
                                  : "unknown duration"}
                              {r.uploaded_at ? ` · uploaded ${formatDateTime(r.uploaded_at)}` : ""}
                            </p>
                          </div>
                          <Badge tone="blue">{(r.similarity * 100).toFixed(1)}% match</Badge>
                        </div>
                        <div className="mt-1.5 flex items-center gap-3">
                          {r.media_type === "image" ? (
                            <span className="text-xs text-slate-500">image result</span>
                          ) : (
                            <>
                              <button
                                onClick={() => void copyText(r.timestamp_hms).then(() => notify("Copied!"))}
                                title="Copy timestamp"
                                className="font-mono text-lg font-semibold text-accent-soft hover:underline"
                              >
                                {r.timestamp_hms}
                              </button>
                              {r.context_start != null && r.context_end != null && (
                                <span className="font-mono text-xs text-slate-500">
                                  {r.context_start_hms ?? r.context_start.toFixed(2)} →{" "}
                                  {r.context_end_hms ?? r.context_end.toFixed(2)}
                                </span>
                              )}
                            </>
                          )}
                        </div>
                        {r.context_reason && (
                          <p className="mt-1 truncate text-[11px] text-slate-500" title={r.context_reason}>
                            {r.context_reason}
                          </p>
                        )}
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          <Button variant="primary" onClick={() => onPlay(r)}>
                            {r.media_type === "image" ? "🖼 View Image" : "▶ Play from here"}
                          </Button>
                          {r.media_type === "video" && (
                            <Button onClick={() => setContext(r)}>View Context</Button>
                          )}
                          <Button onClick={() => void copyResult(r)}>📋 Copy</Button>
                          <Button onClick={() => void saveResult(r)}>
                            {saved ? "Saved ✓" : "💾 Save"}
                          </Button>
                          <span className="flex items-center gap-1">
                            <Button onClick={() => downloadResult(r, "txt")}>TXT</Button>
                            <Button onClick={() => downloadResult(r, "json")}>JSON</Button>
                          </span>
                          <span className="ml-1 flex items-center gap-1">
                            <button
                              onClick={() => sendFeedback(r, true)}
                              title="Relevant"
                              className={`rounded-md px-2 py-1 text-sm ${fb === true ? "bg-emerald-500/20 text-emerald-300" : "text-slate-500 hover:bg-surface-700"}`}
                            >
                              👍
                            </button>
                            <button
                              onClick={() => sendFeedback(r, false)}
                              title="Not relevant"
                              className={`rounded-md px-2 py-1 text-sm ${fb === false ? "bg-red-500/20 text-red-300" : "text-slate-500 hover:bg-surface-700"}`}
                            >
                              👎
                            </button>
                          </span>
                        </div>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}

      <ContextViewer
        result={context}
        query={response?.query ?? ""}
        onClose={() => setContext(null)}
        onSaved={() => notify("Saved ✓")}
        onOpenAtMatch={(r) => onPlay(r)}
        onPlaySegment={onPlaySegment}
      />
    </section>
  );
}
