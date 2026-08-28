"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { copyText, downloadText } from "@/lib/clipboard";
import { safeFileName, savedContextText } from "@/lib/context";
import type { SavedContext } from "@/lib/types";
import { Badge, Button, EmptyState, Spinner } from "../ui/primitives";

export function SavedContexts({
  onOpen,
}: {
  onOpen?: (s: SavedContext) => void;
}) {
  const [items, setItems] = useState<SavedContext[]>([]);
  const [loading, setLoading] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.listContexts();
      setItems(res.items);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const flash = (msg: string) => {
    setFeedback(msg);
    window.setTimeout(() => setFeedback(null), 1800);
  };

  const copyOne = async (s: SavedContext) => {
    const ok = await copyText(s.context_text ?? savedContextText(s));
    flash(ok ? "Copied!" : "Copy failed");
  };

  const remove = async (id: number) => {
    await api.deleteContext(id).catch(() => undefined);
    await refresh();
  };

  const exportAll = async (format: "txt" | "json" | "csv") => {
    try {
      const content = await api.exportContexts(format);
      const mime =
        format === "json"
          ? "application/json"
          : format === "csv"
            ? "text/csv"
            : "text/plain";
      downloadText(`saved_contexts.${format}`, content, mime);
      flash("Downloaded ✓");
    } catch {
      flash("Export failed.");
    }
  };

  return (
    <section className="panel">
      <div className="panel-header">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-slate-100">Saved Contexts</h2>
          <span className="text-xs text-slate-500">{items.length}</span>
        </div>
        <div className="flex items-center gap-1.5">
          {feedback && <span className="text-xs text-emerald-400">{feedback}</span>}
          <Button onClick={() => void exportAll("txt")}>Export TXT</Button>
          <Button onClick={() => void exportAll("json")}>JSON</Button>
          <Button onClick={() => void refresh()}>{loading ? <Spinner size={14} /> : "Refresh"}</Button>
        </div>
      </div>

      <div className="p-4">
        {loading && items.length === 0 ? (
          <div className="flex justify-center py-6"><Spinner /></div>
        ) : items.length === 0 ? (
          <EmptyState
            title="No saved contexts yet."
            subtitle="Open a result's Context viewer and press Save to keep it here."
          />
        ) : (
          <ul className="space-y-2">
            {items.map((s) => (
              <li key={s.id} className="rounded-lg border border-surface-700 bg-surface-850 p-2.5">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-xs font-medium text-slate-200">{s.filename}</p>
                    <p className="truncate text-[11px] text-slate-500">“{s.query}”</p>
                    <p className="font-mono text-[11px] text-accent-soft">{s.timestamp_hms}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Badge tone={s.media_type === "image" ? "green" : "blue"}>{s.media_type}</Badge>
                  </div>
                </div>
                <div className="mt-1.5 flex gap-1.5">
                  {onOpen && s.media_type === "video" && (
                    <Button variant="primary" onClick={() => onOpen(s)}>
                      ▶ Open
                    </Button>
                  )}
                  <Button onClick={() => void copyOne(s)}>Copy</Button>
                  <Button
                    onClick={() => {
                      downloadText(
                        `${safeFileName(s.filename)}.context.txt`,
                        s.context_text ?? savedContextText(s),
                      );
                      flash("Downloaded ✓");
                    }}
                  >
                    Download
                  </Button>
                  <Button variant="danger" onClick={() => void remove(s.id)}>
                    Remove
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
