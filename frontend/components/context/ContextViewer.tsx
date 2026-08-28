"use client";

import { useState } from "react";
import { absoluteUrl, api } from "@/lib/api";
import { copyText, downloadText } from "@/lib/clipboard";
import { contextText, resultCsv, resultJson, resultPlainText, safeFileName } from "@/lib/context";
import type { SearchResult } from "@/lib/types";
import { Badge, Button, Spinner } from "../ui/primitives";
import { Modal } from "../ui/Modal";

type Feedback = { msg: string; ok: boolean } | null;

export function ContextViewer({
  result,
  query,
  onClose,
  onSaved,
  onOpenAtMatch,
  onPlaySegment,
}: {
  result: SearchResult | null;
  query: string;
  onClose: () => void;
  onSaved: () => void;
  onOpenAtMatch?: (result: SearchResult) => void;
  onPlaySegment?: (result: SearchResult) => void;
}) {
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [saving, setSaving] = useState(false);

  const flash = (msg: string, ok = true) => {
    setFeedback({ msg, ok });
    window.setTimeout(() => setFeedback(null), 1800);
  };

  const copy = async (text: string, label: string) => {
    const ok = await copyText(text);
    flash(ok ? "Copied!" : "Copy failed — your browser blocked clipboard access.", ok);
    void label;
  };

  const save = async () => {
    if (!result) return;
    setSaving(true);
    try {
      await api.saveContext({
        query,
        video_id: result.video_id,
        filename: result.video_name,
        media_type: result.media_type,
        timestamp_seconds: result.timestamp,
        context_start: result.context_start,
        context_end: result.context_end,
        score: result.similarity,
        frame_id: result.frame_id || null,
        context_text: result.context_text ?? contextText(result, query),
        // persist the FULL evidence so reopening restores everything
        context_frames: result.context_frames,
        reason: result.context_reason ?? null,
      });
      flash("Saved ✓");
      onSaved();
    } catch (err) {
      flash(
        err instanceof Error ? `Save failed: ${err.message}` : "Save failed.",
        false,
      );
    } finally {
      setSaving(false);
    }
  };

  const copyResult = async () => {
    if (!result) return;
    const ok = await copyText(resultPlainText(result, query));
    flash(ok ? "Copied result!" : "Copy failed — clipboard blocked.", ok);
  };

  const copyAllContext = async () => {
    if (!result) return;
    const block = [
      resultPlainText(result, query),
      "",
      result.context_text ?? contextText(result, query),
    ].join("\n");
    const ok = await copyText(block);
    flash(ok ? "Copied all context!" : "Copy failed — clipboard blocked.", ok);
  };

  const download = (format: "txt" | "json" | "csv") => {
    if (!result) return;
    const base = safeFileName(result.video_name.replace(/\.[^.]+$/, ""));
    if (format === "json") {
      downloadText(`${base}.context.json`, resultJson(result, query), "application/json");
    } else if (format === "csv") {
      downloadText(`${base}.context.csv`, resultCsv(result, query), "text/csv");
    } else {
      downloadText(`${base}.context.txt`, result.context_text ?? contextText(result, query));
    }
    flash("Downloaded ✓");
  };

  const text = result ? (result.context_text ?? contextText(result, query)) : "";

  return (
    <Modal open={result !== null} onClose={onClose} title="Context" wide>
      {!result ? (
        <div className="flex justify-center py-8"><Spinner /></div>
      ) : (
        <div className="space-y-4">
          {/* structured header */}
          <div className="rounded-lg border border-surface-700 bg-surface-850 p-3">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
              <span className="font-semibold text-slate-100">{result.video_name}</span>
              <Badge tone={result.media_type === "image" ? "green" : "blue"}>
                {result.media_type}
              </Badge>
            </div>
            <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-4">
              <div>
                <div className="text-[10px] uppercase tracking-wide text-slate-500">Match</div>
                <div className="font-mono text-accent-soft">{result.timestamp_hms}</div>
              </div>
              {result.context_start != null && result.context_end != null && (
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-slate-500">Segment</div>
                  <div className="font-mono text-slate-200">
                    {result.context_start_hms ?? result.context_start.toFixed(2)} →{" "}
                    {result.context_end_hms ?? result.context_end.toFixed(2)}
                  </div>
                </div>
              )}
              <div>
                <div className="text-[10px] uppercase tracking-wide text-slate-500">Relevance</div>
                <div className="font-mono text-slate-200">{(result.similarity * 100).toFixed(1)}%</div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wide text-slate-500">Stage</div>
                <div className="font-mono text-slate-200">{result.retrieval_stage}</div>
              </div>
            </div>
            {result.context_reason && (
              <p className="mt-2 text-xs text-slate-400">{result.context_reason}</p>
            )}
          </div>

          {/* context frames */}
          {result.media_type === "video" && (
            <div>
              <p className="label">Context frames</p>
              <div className="flex flex-wrap gap-2">
                {result.context_frames.length === 0 && (
                  <p className="text-xs text-slate-500">No nearby frames indexed.</p>
                )}
                {result.context_frames.map((f) => (
                  <figure key={f.frame_id} className="w-28">
                    <img
                      src={absoluteUrl(f.frame_url)}
                      alt={`frame at ${f.timestamp_hms}`}
                      className="h-16 w-full rounded-md object-cover"
                      loading="lazy"
                    />
                    <figcaption className="mt-1 text-center font-mono text-[10px] text-slate-400">
                      {f.timestamp_hms}
                    </figcaption>
                  </figure>
                ))}
              </div>
            </div>
          )}

          {/* context text (includes optional AI summary when available) */}
          <div>
            <p className="label">Context text</p>
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-surface-700 bg-surface-900 p-3 font-mono text-xs text-slate-300">
              {text}
            </pre>
            {result.context_summary && (
              <div className="mt-2 rounded-lg border border-accent/30 bg-accent/10 px-3 py-2 text-sm text-slate-200">
                <span className="text-[10px] font-medium uppercase tracking-wide text-accent-soft">
                  AI summary (grounded)
                </span>
                <p className="mt-0.5">{result.context_summary}</p>
              </div>
            )}
          </div>

          {/* actions */}
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" onClick={() => void copy(text, "context")}>
              📋 Copy Context
            </Button>
            <Button onClick={() => void copy(result.timestamp_hms, "timestamp")}>
              ⏱ Copy Timestamp
            </Button>
            <Button onClick={() => void copyResult()}>📋 Copy Result</Button>
            <Button onClick={() => void copyAllContext()}>📋 Copy All Context</Button>
            <Button onClick={() => void save()} disabled={saving}>
              {saving ? <Spinner size={14} /> : "💾"} Save
            </Button>
            {result.media_type === "video" && onOpenAtMatch && (
              <Button onClick={() => onOpenAtMatch(result)}>▶ Open Video at Match</Button>
            )}
            {result.media_type === "video" && onPlaySegment && result.context_end != null && (
              <Button onClick={() => onPlaySegment(result)}>⏯ Play Context Segment</Button>
            )}
            <span className="text-xs text-slate-500">Download:</span>
            <Button onClick={() => download("txt")}>TXT</Button>
            <Button onClick={() => download("json")}>JSON</Button>
            <Button onClick={() => download("csv")}>CSV</Button>
            {feedback && (
              <span className={`text-xs ${feedback.ok ? "text-emerald-400" : "text-red-400"}`}>
                {feedback.msg}
              </span>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}
