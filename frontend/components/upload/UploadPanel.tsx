"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useUploads } from "@/hooks/useUploads";
import { formatBytes, formatEta, formatSpeed } from "@/lib/format";
import { Badge, Button, ProgressBar } from "../ui/primitives";

const ACCEPTED_EXT = [
  ".jpg", ".jpeg", ".png", ".webp", ".gif",
  ".mp4", ".mov", ".avi", ".mkv", ".webm",
  ".m4v", ".mpg", ".mpeg", ".ts", ".flv", ".wmv",
];

function mediaKindOf(file: File): "image" | "video" {
  if (file.type.startsWith("image/")) return "image";
  if (file.type.startsWith("video/")) return "video";
  const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  if ([".jpg", ".jpeg", ".png", ".webp", ".gif"].includes(ext)) return "image";
  return "video";
}

export function UploadPanel({
  hidden,
  onToggleHidden,
  onUploaded,
  maintenance = false,
}: {
  hidden: boolean;
  onToggleHidden: () => void;
  onUploaded: () => void;
  maintenance?: boolean;
}) {
  // agree on the SAME chunk size as the backend (server-configured, so a
  // chunk never exceeds any proxy/server request-body limit)
  const [chunkSize, setChunkSize] = useState<number | undefined>(undefined);
  const [maxGb, setMaxGb] = useState(10);
  const { entries, addFiles, pause, resume, cancel, clearFinished, activeCount } =
    useUploads(onUploaded, chunkSize);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .uploadsConfig()
      .then((cfg) => {
        if (cancelled) return;
        setChunkSize(cfg.chunk_size);
        setMaxGb(cfg.max_upload_size_gb);
      })
      .catch(() => {
        /* fall back to the client default (10 MB) */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleFiles = useCallback(
    (files: FileList | null) => {
      if (!files) return;
      const media = Array.from(files).filter((f) => {
        const ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase();
        return ACCEPTED_EXT.includes(ext) || f.type.startsWith("video/") || f.type.startsWith("image/");
      });
      if (media.length) addFiles(media);
    },
    [addFiles],
  );

  return (
    <section className="panel">
      <div className="panel-header">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-slate-100">Upload</h2>
          {activeCount > 0 && (
            <span className="rounded-full bg-accent/20 px-2 py-0.5 text-[11px] text-accent-soft">
              {activeCount} active
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {entries.length > 0 && <Button onClick={clearFinished}>Clear finished</Button>}
          <Button onClick={onToggleHidden}>{hidden ? "Show Upload Panel" : "Hide Upload Panel"}</Button>
        </div>
      </div>

      {!hidden && (
        <div className="p-4">
          {maintenance && (
            <div className="mb-3 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-200">
              🛠 System maintenance in progress — uploads and indexing are
              temporarily unavailable.
            </div>
          )}
          <div
            onDragOver={(e) => {
              if (maintenance) return;
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              if (maintenance) return;
              handleFiles(e.dataTransfer.files);
            }}
            onClick={() => !maintenance && inputRef.current?.click()}
            className={`flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors ${
              maintenance
                ? "cursor-not-allowed border-surface-700 bg-surface-800 opacity-60"
                : dragOver
                  ? "cursor-pointer border-accent bg-accent/10"
                  : "cursor-pointer border-surface-600 bg-surface-800 hover:border-surface-500"
            }`}
          >
            <div className="text-2xl">⬆️</div>
            <p className="text-sm text-slate-200">
              Drop images or videos here or <span className="text-accent-soft">browse</span>
            </p>
            <p className="text-xs text-slate-500">
              JPG, PNG, WEBP, GIF, MP4, MOV, AVI, MKV, WEBM — resumable chunked
              uploads up to {maxGb} GB
            </p>
            <input
              ref={inputRef}
              type="file"
              multiple
              disabled={maintenance}
              accept="image/*,video/*,.jpg,.jpeg,.png,.webp,.gif,.mp4,.mov,.avi,.mkv,.webm"
              className="hidden"
              onChange={(e) => {
                handleFiles(e.target.files);
                e.target.value = "";
              }}
            />
          </div>

          {entries.length > 0 && (
            <ul className="mt-4 space-y-3">
              {entries.map((e) => (
                <UploadRow
                  key={e.id}
                  entry={e}
                  onPause={() => pause(e.id)}
                  onResume={() => resume(e.id)}
                  onCancel={() => cancel(e.id)}
                />
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

function UploadRow({
  entry,
  onPause,
  onResume,
  onCancel,
}: {
  entry: ReturnType<typeof useUploads>["entries"][number];
  onPause: () => void;
  onResume: () => void;
  onCancel: () => void;
}) {
  const { filename, fileSize, phase, progressPct, uploadedBytes, speedBps, etaSeconds, error, totalChunks, currentChunk } = entry;

  return (
    <li className="rounded-lg border border-surface-700 bg-surface-850 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-medium text-slate-100">{filename}</p>
            <Badge tone={mediaKindOf(new File([], filename)) === "image" ? "green" : "blue"}>
              {mediaKindOf(new File([], filename)) === "image" ? "image" : "video"}
            </Badge>
          </div>
          <p className="text-xs text-slate-500">
            {formatBytes(fileSize)}
            {totalChunks > 0 && (
              <>
                {" · "}chunk {Math.min(currentChunk + 1, totalChunks)}/{totalChunks}
              </>
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {phase === "uploading" && <Button onClick={onPause}>Pause</Button>}
          {entry.paused && <Button onClick={onResume}>Resume</Button>}
          {(phase === "uploading" || entry.paused || phase === "preparing" || phase === "completing") && (
            <Button variant="danger" onClick={onCancel}>Cancel</Button>
          )}
        </div>
      </div>

      <div className="mt-2 flex items-center gap-3">
        <div className="flex-1">
          <ProgressBar
            value={progressPct}
            tone={phase === "error" ? "red" : phase === "done" ? "green" : "accent"}
          />
        </div>
        <span className="w-12 text-right text-xs font-semibold text-slate-300">
          {Math.round(progressPct)}%
        </span>
      </div>

      <div className="mt-1 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
        {phase === "uploading" || phase === "preparing" ? (
          <span>
            {formatBytes(uploadedBytes)} / {formatBytes(fileSize)}
            {speedBps > 0 && <> · {formatSpeed(speedBps)} · ETA {formatEta(etaSeconds)}</>}
          </span>
        ) : phase === "done" ? (
          <span className="text-emerald-400">✓ Upload complete — indexing started</span>
        ) : phase === "error" ? (
          <span className="text-red-400">✕ {error ?? "Upload failed"}</span>
        ) : phase === "cancelled" ? (
          <span>Upload cancelled</span>
        ) : phase === "paused" ? (
          <span>Paused</span>
        ) : (
          <span>Completing…</span>
        )}
      </div>
    </li>
  );
}
