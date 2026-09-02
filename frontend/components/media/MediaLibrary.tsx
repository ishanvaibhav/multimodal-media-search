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

export function MediaLibrary({ onOpen, onPlay, maintenance = false }: { onOpen?: (video: VideoItem) => void; onPlay?: (video: VideoItem) => void; maintenance?: boolean }) {
  const media = useMedia();
  const [detail, setDetail] = useState<VideoItem | null>(null);
  const [toDelete, setToDelete] = useState<VideoItem | null>(null);

  return (
    <section className="min-h-full overflow-hidden rounded-2xl border border-surface-700 bg-surface-950 shadow-2xl shadow-black/20">
      <header className="border-b border-surface-700 bg-surface-900/90 px-5 py-5">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-semibold tracking-tight text-slate-100">Media Library</h1>
              <span className="rounded-full border border-surface-700 bg-surface-850 px-2.5 py-1 text-xs text-slate-400">{media.total} item{media.total === 1 ? "" : "s"}</span>
            </div>
            <p className="mt-1 text-sm text-slate-500">Browse and manage your indexed images and videos.</p>
          </div>
          <Button onClick={() => void media.refresh()}>{media.loading ? <Spinner size={14} /> : "Refresh library"}</Button>
        </div>
        <div className="mt-4 grid gap-2 md:grid-cols-[1fr_180px_130px]">
          <input className="input" placeholder="Search media…" value={media.search} onChange={(e) => media.setSearch(e.target.value)} />
          <select className="input" value={media.sortBy} onChange={(e) => media.setSortBy(e.target.value)}>
            <option value="uploaded_at">Newest upload</option><option value="original_filename">Name</option><option value="size_bytes">Size</option>
          </select>
          <select className="input" value={media.sortOrder} onChange={(e) => media.setSortOrder(e.target.value as "asc" | "desc")}>
            <option value="desc">Descending</option><option value="asc">Ascending</option>
          </select>
        </div>
      </header>

      <div className="p-5">
        {media.items.length === 0 ? <EmptyState title="No media indexed yet" subtitle="Upload an image or video to start building your searchable media library." /> :
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {media.items.map((v) => (
              <article key={v.video_id} className="group overflow-hidden rounded-xl border border-surface-700 bg-surface-900 transition hover:-translate-y-0.5 hover:border-surface-600 hover:bg-surface-850">
                <button type="button" className="relative block aspect-video w-full overflow-hidden bg-surface-800 text-left" onClick={() => v.status === "ready" && (onPlay?.(v) ?? onOpen?.(v))}>
                  {v.status === "ready" ? <img src={absoluteUrl(v.thumbnail_url)} alt={v.filename} className="h-full w-full object-cover transition duration-300 group-hover:scale-[1.03]" loading="lazy" /> : <div className="flex h-full items-center justify-center text-4xl opacity-60">🎬</div>}
                  <div className="absolute inset-x-0 top-0 flex justify-between bg-gradient-to-b from-black/70 to-transparent p-3"><Badge tone={v.media_type === "image" ? "green" : "blue"}>{v.media_type}</Badge><Badge tone={statusTone(v.status)}>{titleCase(v.status)}</Badge></div>
                  {v.media_type === "video" && v.duration_seconds != null && <span className="absolute bottom-2 right-2 rounded bg-black/75 px-1.5 py-0.5 font-mono text-[10px] text-white">{formatHms(v.duration_seconds)}</span>}
                </button>
                <div className="p-4">
                  <p className="truncate text-sm font-medium text-slate-100" title={v.filename}>{v.filename}</p>
                  <p className="mt-1 text-xs text-slate-500">{formatBytes(v.size_bytes)}{v.width && v.height ? ` · ${v.width}×${v.height}` : ""}</p>
                  <p className="mt-1 text-xs text-slate-500">{v.status === "ready" ? (v.media_type === "image" ? "Indexed" : `Indexed ${v.frame_count.toLocaleString()} frames`) : v.error ?? "Processing…"} · {formatDate(v.uploaded_at)}</p>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {v.status === "ready" && <Button variant="primary" onClick={() => onPlay?.(v) ?? onOpen?.(v)}>{v.media_type === "image" ? "View" : "Open"}</Button>}
                    <Button onClick={() => setDetail(v)}>Details</Button>
                    <Button onClick={() => void media.reindex(v.video_id)} disabled={media.busy || maintenance}>Re-index</Button>
                    <Button variant="danger" onClick={() => setToDelete(v)} disabled={maintenance}>Delete</Button>
                  </div>
                </div>
              </article>
            ))}
          </div>}
      </div>

      {detail && <MediaDetailModal videoId={detail.video_id} onClose={() => setDetail(null)} />}
      <ConfirmDialog open={toDelete !== null} onClose={() => setToDelete(null)} title="Delete media" description={`Delete "${toDelete?.filename ?? ""}" and all of its indexed frames and embeddings? This cannot be undone.`} confirmLabel="Delete media" onConfirm={() => { if (toDelete) void media.remove(toDelete.video_id); }} />
    </section>
  );
}

function MediaDetailModal({ videoId, onClose }: { videoId: string; onClose: () => void }) {
  const [detail, setDetail] = useState<VideoDetail | null>(null);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => { let cancelled = false; setLoaded(false); api.getMedia(videoId).then((d) => { if (!cancelled) { setDetail(d); setLoaded(true); } }).catch(() => { if (!cancelled) setLoaded(true); }); return () => { cancelled = true; }; }, [videoId]);
  return <Modal open onClose={onClose} title={detail?.filename ?? "Media details"} wide>{!loaded ? <div className="flex justify-center py-8"><Spinner /></div> : !detail ? <p className="text-sm text-slate-400">Could not load media details.</p> : <div className="space-y-5"><div className="flex flex-col gap-4 md:flex-row"><div className="h-28 w-full shrink-0 overflow-hidden rounded-lg bg-surface-800 md:w-48">{detail.status === "ready" ? <img src={absoluteUrl(detail.thumbnail_url)} alt="" className="h-full w-full object-cover" /> : <div className="flex h-full items-center justify-center text-2xl">🎬</div>}</div><dl className="grid flex-1 grid-cols-2 gap-x-6 gap-y-2 text-sm"><dt className="text-slate-500">Duration</dt><dd className="text-slate-200">{formatHms(detail.duration_seconds)}</dd><dt className="text-slate-500">Resolution</dt><dd className="text-slate-200">{detail.width}×{detail.height}</dd><dt className="text-slate-500">Codec</dt><dd className="text-slate-200">{detail.codec ?? "—"}</dd><dt className="text-slate-500">Container</dt><dd className="text-slate-200">{detail.container ?? "—"}</dd><dt className="text-slate-500">Size</dt><dd className="text-slate-200">{formatBytes(detail.size_bytes)}</dd><dt className="text-slate-500">Frames indexed</dt><dd className="text-slate-200">{detail.frame_count.toLocaleString()}</dd><dt className="text-slate-500">Uploaded</dt><dd className="text-slate-200">{formatDateTime(detail.uploaded_at)}</dd></dl></div><div><p className="label">Indexed frames ({Math.min(detail.frames.length, 60)} shown)</p><div className="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8">{detail.frames.slice(0, 60).map((f) => <figure key={f.frame_id} className="overflow-hidden rounded-md border border-surface-700 bg-surface-850"><img src={absoluteUrl(f.frame_url)} alt="" className="aspect-video w-full object-cover" loading="lazy" /><figcaption className="px-1 py-1 text-center font-mono text-[10px] text-slate-500">{f.timestamp_hms}</figcaption></figure>)}</div></div></div>}</Modal>;
}
