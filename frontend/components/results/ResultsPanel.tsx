"use client";

import { useState } from "react";
import type { SearchResponse, SearchResult } from "@/lib/types";
import { absoluteUrl, api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { Badge, Button, EmptyState } from "../ui/primitives";
import { Modal } from "../ui/Modal";

function copyTimestamp(text: string) {
  void navigator.clipboard?.writeText(text).catch(() => {});
}

export function ResultsPanel({
  response,
  hidden,
  onToggleHidden,
  onClearResults,
  onPlay,
}: {
  response: SearchResponse | null;
  hidden: boolean;
  onToggleHidden: () => void;
  onClearResults: () => void;
  onPlay: (result: SearchResult) => void;
}) {
  const [context, setContext] = useState<SearchResult | null>(null);
  const [feedback, setFeedback] = useState<Record<string, boolean>>({});

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
        </div>
        <div className="flex items-center gap-2">
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
                        <div className="mt-2 flex items-center gap-3">
                          {r.media_type === "image" ? (
                            <span className="text-xs text-slate-500">image result</span>
                          ) : (
                            <>
                              <button
                                onClick={() => copyTimestamp(r.timestamp_hms)}
                                title="Copy timestamp"
                                className="font-mono text-lg font-semibold text-accent-soft hover:underline"
                              >
                                {r.timestamp_hms}
                              </button>
                              <span className="text-xs text-slate-500">
                                {r.timestamp.toFixed(2)}s · raw {r.raw_similarity.toFixed(3)}
                              </span>
                            </>
                          )}
                        </div>
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          <Button variant="primary" onClick={() => onPlay(r)}>
                            {r.media_type === "image" ? "🖼 View Image" : "▶ Play from here"}
                          </Button>
                          {r.media_type === "video" && (
                            <Button onClick={() => setContext(r)}>View Context</Button>
                          )}
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

      <Modal open={context !== null} onClose={() => setContext(null)} title="Nearby frames">
        {context && (
          <div>
            <p className="mb-3 text-sm text-slate-300">
              {context.video_name} — around {context.timestamp_hms}
            </p>
            <div className="flex flex-wrap gap-2">
              {context.context_frames.length === 0 && (
                <p className="text-xs text-slate-500">No nearby frames indexed.</p>
              )}
              {context.context_frames.map((f) => (
                <figure key={f.frame_id} className="w-32">
                  <img
                    src={absoluteUrl(f.frame_url)}
                    alt={`frame at ${f.timestamp_hms}`}
                    className="h-20 w-full rounded-md object-cover"
                    loading="lazy"
                  />
                  <figcaption className="mt-1 text-center font-mono text-[11px] text-slate-400">
                    {f.timestamp_hms}
                  </figcaption>
                </figure>
              ))}
            </div>
          </div>
        )}
      </Modal>
    </section>
  );
}
