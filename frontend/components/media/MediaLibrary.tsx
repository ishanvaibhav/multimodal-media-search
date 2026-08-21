"use client";

import { useEffect, useState } from "react";
import { api, absoluteUrl } from "@/lib/api";
import { useMedia } from "@/hooks/useMedia";
import { formatBytes, formatDate, formatDateTime, formatHms, titleCase } from "@/lib/format";
import type { VideoDetail, VideoItem } from "@/lib/types";
import { Badge, Button, EmptyState, Spinner } from "../ui/primitives";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Modal } from "../ui/Modal";

function statusTone(status: VideoItem["status"]): "green" | "blue" | "amber" | "red" | "slate" {
  if (status === "ready") return "green";
  if (status === "failed") return "red";
  if (status === "cancelled") return "slate";
  if (status === "queued" || status === "pending") return "blue";
  return "amber";
}

export function MediaLibrary({
  onOpen,
  onPlay,
  maintenance = false,
}: {
  onOpen: (video: VideoItem) => void;
  onPlay: (video: VideoItem) => void;
  maintenance?: boolean;
}) {
  const media = useMedia();
  const [detail, setDetail] = useState<VideoItem | null>(null);
  const [toDelete, setToDelete] = useState<VideoItem | null>(null);

  return (
    <section className="panel">
      <div className="panel-header">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-slate-100">Media Library</h2>
          <span className="text-xs text-slate-500">{media.total} item{media.total === 1 ? "" : "s"}</span>
        </div>
        <Button onClick={() => void media.refresh()}>{media.loading ? <Spinner size={14} /> : "Refresh"}</Button>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-b border-surface-700 px-4 py-2">
        <input
          className="input max-w-[220px]"
          placeholder="Search media…"
          value={media.search}
          onChange={(e) => media.setSearch(e.target.value)}
        />
        <select
          className="input max-w-[160px]"
          value={media.sortBy}
          onChange={(e) => media.setSortBy(e.target.value)}
        >
          <option value="uploaded_at">Newest upload</option>
          <option value="original_filename">Name</option>
          <option value="size_bytes">Size</option>
        </select>
        <select
          className="input max-w-[120px]"
          value={media.sortOrder}
          onChange={(e) => media.setSortOrder(e.target.value as "asc" | "desc")}
        >
          <option value="desc">Desc</option>
          <option value="asc">Asc</option>
        </select>
      </div>

      <div className="p-4">
        {media.items.length === 0 ? (
          <EmptyState
            title="No media indexed yet."
            subtitle="Upload images or videos above to start building your searchable media library."
          />
        ) : (
          <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {media.items.map((v) => (
              <li key={v.video_id} className="rounded-lg border border-surface-700 bg-surface-850 p-3">
                <div className="flex gap-3">
                  <div className="h-16 w-24 shrink-0 overflow-hidden rounded-md bg-surface-800">
                    {v.status === "ready" ? (
                      <img
                        src={absoluteUrl(v.thumbnail_url)}
                        alt={v.filename}
                        className="h-full w-full object-cover"
                        loading="lazy"
                      />
                    ) : (
                      <div className="flex h-full w-full items-center justify-center text-2xl">🎬</div>
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-2">
                      <p className="truncate text-sm font-medium text-slate-100" title={v.filename}>
                        {v.filename}
                      </p>
                      <span className="flex shrink-0 items-center gap-1">
                        <Badge tone={v.media_type === "image" ? "green" : "blue"}>
                          {v.media_type}
                        </Badge>
                        <Badge tone={statusTone(v.status)}>{titleCase(v.status)}</Badge>
                      </span>
                    </div>
                    <p className="mt-0.5 text-xs text-slate-500">
                      {formatBytes(v.size_bytes)}
                      {v.media_type === "video" && <> · {formatHms(v.duration_seconds)}</>}
                      {v.width && v.height ? ` · ${v.width}×${v.height}` : ""}
                    </p>
                    <p className="text-xs text-slate-500">
                      {v.status === "ready"
                        ? v.media_type === "image"
                          ? "Indexed"
                          : `Indexed ${v.frame_count.toLocaleString()} frames`
                        : v.error ?? "Processing…"}
                      {" · "}
                      {formatDate(v.uploaded_at)}
                    </p>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {v.status === "ready" && (
                        <Button variant="primary" onClick={() => onPlay(v)}>
                          {v.media_type === "image" ? "🖼 View" : "▶ Open"}
                        </Button>
                      )}
                      <Button onClick={() => setDetail(v)}>Details</Button>
                      <Button
                        onClick={() => void media.reindex(v.video_id)}
                        disabled={media.busy || maintenance}
                        title={maintenance ? "maintenance in progress" : undefined}
                      >
                        Re-index
                      </Button>
                      <Button
                        variant="danger"
                        onClick={() => setToDelete(v)}
                        disabled={maintenance}
                        title={maintenance ? "maintenance in progress" : undefined}
                      >
                        Delete
                      </Button>
                    </div>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {detail && (
        <MediaDetailModal
          videoId={detail.video_id}
          onClose={() => setDetail(null)}
        />
      )}

      <ConfirmDialog
        open={toDelete !== null}
        onClose={() => setToDelete(null)}
        title="Delete media"
        description={`Delete "${toDelete?.filename ?? ""}" and all of its indexed frames and embeddings? This cannot be undone.`}
        confirmLabel="Delete media"
        onConfirm={() => {
          if (toDelete) void media.remove(toDelete.video_id);
        }}
      />
    </section>
  );
}

function MediaDetailModal({ videoId, onClose }: { videoId: string; onClose: () => void }) {
  const [detail, setDetail] = useState<VideoDetail | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoaded(false);
    api.getMedia(videoId)
      .then((d) => {
        if (!cancelled) {
          setDetail(d);
          setLoaded(true);
        }
      })
      .catch(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [videoId]);

  return (
    <Modal open onClose={onClose} title={detail?.filename ?? "Media details"} wide>
      {!loaded ? (
        <div className="flex justify-center py-8"><Spinner /></div>
      ) : !detail ? (
        <p className="text-sm text-slate-400">Could not load media details.</p>
      ) : (
        <div className="space-y-4">
          <div className="flex gap-4">
            <div className="h-24 w-40 shrink-0 overflow-hidden rounded-md bg-surface-800">
              {detail.status === "ready" ? (
                <img src={absoluteUrl(detail.thumbnail_url)} alt="" className="h-full w-full object-cover" />
              ) : (
                <div className="flex h-full w-full items-center justify-center text-2xl">🎬</div>
              )}
            </div>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
              <dt className="text-slate-500">Duration</dt>
              <dd className="text-slate-200">{formatHms(detail.duration_seconds)}</dd>
              <dt className="text-slate-500">Resolution</dt>
              <dd className="text-slate-200">{detail.width}×{detail.height}</dd>
              <dt className="text-slate-500">Codec</dt>
              <dd className="text-slate-200">{detail.codec ?? "—"}</dd>
              <dt className="text-slate-500">Container</dt>
              <dd className="text-slate-200">{detail.container ?? "—"}</dd>
              <dt className="text-slate-500">Size</dt>
              <dd className="text-slate-200">{formatBytes(detail.size_bytes)}</dd>
              <dt className="text-slate-500">Frames indexed</dt>
              <dd className="text-slate-200">{detail.frame_count.toLocaleString()}</dd>
              <dt className="text-slate-500">Uploaded</dt>
              <dd className="text-slate-200">{formatDateTime(detail.uploaded_at)}</dd>
              <dt className="text-slate-500">Status</dt>
              <dd className="text-slate-200">{titleCase(detail.status)}</dd>
            </dl>
          </div>

          <div>
            <p className="label">Indexed frames ({Math.min(detail.frames.length, 60)} shown)</p>
            <div className="flex flex-wrap gap-1.5">
              {detail.frames.slice(0, 60).map((f) => (
                <figure key={f.frame_id} className="w-20">
                  <img
                    src={absoluteUrl(f.frame_url)}
                    alt=""
                    className="h-14 w-full rounded object-cover"
                    loading="lazy"
                  />
                  <figcaption className="text-center font-mono text-[10px] text-slate-500">
                    {f.timestamp_hms}
                  </figcaption>
                </figure>
              ))}
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}
