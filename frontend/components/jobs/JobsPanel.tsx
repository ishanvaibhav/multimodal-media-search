"use client";

import { useJobs } from "@/hooks/useJobs";
import { formatDateTime, titleCase } from "@/lib/format";
import { Badge, Button, ProgressBar, EmptyState } from "../ui/primitives";

const STAGE_LABELS: Record<string, string> = {
  queued: "Queued",
  validating: "Validating",
  probing: "FFprobe metadata",
  extracting_frames: "Frame extraction",
  deduplicating: "Deduplication",
  embedding: "Embedding",
  indexing: "Chroma indexing",
  finalizing: "Finalizing",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

function etaFor(job: { progress: number; updated_at: string | null }): string | null {
  if (!job.progress || job.progress <= 0) return null;
  const started = job.updated_at ? new Date(job.updated_at).getTime() : Date.now();
  const elapsed = (Date.now() - started) / 1000;
  if (elapsed <= 0 || !Number.isFinite(elapsed)) return null;
  const remaining = (elapsed / (job.progress / 100)) - elapsed;
  if (!Number.isFinite(remaining) || remaining < 0) return null;
  const m = Math.floor(remaining / 60);
  const s = Math.ceil(remaining % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

export function JobsPanel() {
  const { jobs, active, cancel } = useJobs();
  const recent = jobs.slice(0, 8);

  return (
    <section className="panel">
      <div className="panel-header">
        <h2 className="text-sm font-semibold text-slate-100">Processing</h2>
        {active && (
          <Badge tone="amber">
            <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400" />
            running
          </Badge>
        )}
      </div>

      <div className="p-4">
        {recent.length === 0 ? (
          <EmptyState title="No jobs yet" subtitle="Upload a video to see indexing progress here." />
        ) : (
          <ul className="space-y-3">
            {recent.map((j) => {
              const running = j.status === "running" || j.status === "queued";
              const stageLabel = STAGE_LABELS[j.current_stage] ?? titleCase(j.current_stage);
              const eta = etaFor(j);
              return (
                <li key={j.job_id} className="rounded-lg border border-surface-700 bg-surface-850 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-slate-200">
                      {titleCase(j.type)} · {stageLabel}
                    </span>
                    <Badge
                      tone={
                        j.status === "completed"
                          ? "green"
                          : j.status === "failed"
                            ? "red"
                            : j.status === "cancelled"
                              ? "slate"
                              : "amber"
                      }
                    >
                      {j.status}
                    </Badge>
                  </div>
                  <div className="mt-2 flex items-center gap-3">
                    <div className="flex-1">
                      <ProgressBar
                        value={j.progress}
                        tone={j.status === "failed" ? "red" : "accent"}
                      />
                    </div>
                    <span className="w-12 text-right font-mono text-xs text-slate-300">
                      {Math.round(j.progress)}%
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center justify-between gap-x-3 text-[11px] text-slate-500">
                    <span>
                      sampled {j.frames_sampled.toLocaleString()} · kept{" "}
                      {j.frames_kept.toLocaleString()} · embedded{" "}
                      {j.frames_embedded.toLocaleString()}
                      {j.frames_total > 0 && <> · total {j.frames_total.toLocaleString()}</>}
                    </span>
                    <span className="flex items-center gap-2">
                      {running && eta && <span>ETA {eta}</span>}
                      <span>{formatDateTime(j.updated_at)}</span>
                      {(running || j.status === "cancelling") && (
                        <Button onClick={() => void cancel(j.job_id)}>Cancel</Button>
                      )}
                    </span>
                  </div>
                  {j.error && <p className="mt-1 text-[11px] text-red-400">{j.error}</p>}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}
