"use client";

import { useState } from "react";
import { api, setAdminToken } from "@/lib/api";
import type { ConsistencyReport, HealthResponse, SystemInfo } from "@/lib/types";
import { Badge, Button, Spinner } from "./ui/primitives";
import { Modal } from "./ui/Modal";
import { ConfirmDialog } from "./ui/ConfirmDialog";
import { useSystemStatus } from "@/hooks/useSystemStatus";

function HealthDot({ health }: { health: HealthResponse | null }) {
  const ok =
    health !== null &&
    Object.values(health).slice(0, 8).every((v) => v === "ok");
  return (
    <span className="flex items-center gap-1.5 text-xs text-slate-400">
      <span
        className={`inline-block h-2 w-2 rounded-full ${ok ? "bg-emerald-400" : "bg-amber-400"}`}
      />
      {health ? (ok ? "healthy" : "degraded") : "connecting…"}
    </span>
  );
}

export function Header({ onDataCleared }: { onDataCleared: () => void }) {
  const { health, maintenance, semanticOk, modelMismatch, refresh } = useSystemStatus();
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [showInfo, setShowInfo] = useState(false);
  const [showConsistency, setShowConsistency] = useState(false);
  const [consistency, setConsistency] = useState<ConsistencyReport | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [adminToken, setAdminTokenState] = useState<string>("");

  const openInfo = async () => {
    try {
      setInfo(await api.systemInfo());
    } catch {
      setInfo(null);
    }
    setShowInfo(true);
  };

  const openConsistency = async () => {
    try {
      setConsistency(await api.consistency());
    } catch {
      setConsistency(null);
    }
    setShowConsistency(true);
  };

  return (
    <>
      {maintenance && (
        <div className="flex items-center justify-between gap-3 border-b border-red-500/40 bg-red-500/15 px-4 py-2 text-sm text-red-200">
          <span>
            🛠 System maintenance in progress — indexing and search-cache
            updates are temporarily unavailable.
          </span>
        </div>
      )}
      {modelMismatch && (
        <div className="flex items-center justify-between gap-3 border-b border-red-500/40 bg-red-500/15 px-4 py-2 text-sm text-red-200">
          <span>
            ⚠ Embedding model mismatch detected — the index was built with a
            different model/version. <strong>Reindex required</strong> for correct results.
          </span>
        </div>
      )}
      {!semanticOk && (
        <div className="flex items-center justify-between gap-3 border-b border-amber-500/40 bg-amber-500/15 px-4 py-2 text-sm text-amber-200">
          <span>
            ⚠ Semantic model unavailable — search is running in{" "}
            <strong>limited (non-semantic) mode</strong>.
          </span>
          <span className="text-xs text-amber-300/80">
            backend: {health?.details?.embedding_backend ?? "?"}
          </span>
        </div>
      )}

      <header className="flex items-center justify-between gap-3 border-b border-surface-700 bg-surface-900 px-4 py-3">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent/20 text-lg">
            🎞️
          </div>
          <div>
            <h1 className="text-sm font-semibold tracking-tight text-white">
              AI Media Search
            </h1>
            <p className="text-[11px] text-slate-500">
              Temporal multimodal video retrieval
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <HealthDot health={health} />
          <Button onClick={openConsistency}>Storage Health</Button>
          <Button onClick={openInfo}>System</Button>
          <Button
            variant="danger"
            disabled={clearing}
            onClick={() => setConfirmClear(true)}
          >
            {clearing ? <Spinner size={14} /> : null} Clear All Data
          </Button>
        </div>
      </header>

      <Modal open={showInfo} onClose={() => setShowInfo(false)} title="System information">
        {info ? (
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            <dt className="text-slate-500">App</dt>
            <dd className="text-slate-200">{info.app_name} v{info.version}</dd>
            <dt className="text-slate-500">Embedding</dt>
            <dd className="text-slate-200">
              {info.embedding_backend} · {info.embedding_dim}d · {info.embedding_device}
            </dd>
            <dt className="text-slate-500">Semantic search</dt>
            <dd className="text-slate-200">
              {info.semantic_search ? "✓ enabled" : "✕ disabled"}
            </dd>
            <dt className="text-slate-500">Model</dt>
            <dd className="truncate text-slate-200" title={info.model}>{info.model}</dd>
            <dt className="text-slate-500">FFmpeg</dt>
            <dd className="truncate text-slate-200" title={info.ffmpeg}>{info.ffmpeg}</dd>
            <dt className="text-slate-500">Collection</dt>
            <dd className="text-slate-200">{info.chroma_collection}</dd>
            <dt className="text-slate-500">Admin auth</dt>
            <dd className="text-slate-200">{info.admin_auth}</dd>
            <dt className="text-slate-500">Storage</dt>
            <dd className="text-slate-200">{String(info.storage.total_human ?? "—")}</dd>
          </dl>
        ) : (
          <div className="flex justify-center py-6"><Spinner /></div>
        )}
      </Modal>

      <Modal open={showConsistency} onClose={() => setShowConsistency(false)} title="Storage health">
        {consistency ? (
          <div className="space-y-3 text-sm">
            <div className="grid grid-cols-3 gap-2">
              <div className="rounded-lg bg-surface-800 p-3 text-center">
                <p className="text-lg font-semibold">{consistency.videos}</p>
                <p className="text-xs text-slate-500">Videos</p>
              </div>
              <div className="rounded-lg bg-surface-800 p-3 text-center">
                <p className="text-lg font-semibold">{consistency.frames}</p>
                <p className="text-xs text-slate-500">Frames</p>
              </div>
              <div className="rounded-lg bg-surface-800 p-3 text-center">
                <p className="text-lg font-semibold">{consistency.vectors}</p>
                <p className="text-xs text-slate-500">Vectors</p>
              </div>
            </div>
            <ul className="space-y-1 text-slate-300">
              <li>Missing files: <Badge tone={consistency.missing_files.length ? "red" : "green"}>{consistency.missing_files.length}</Badge></li>
              <li>Orphan files: <Badge tone={consistency.orphan_files ? "amber" : "green"}>{consistency.orphan_files}</Badge></li>
              <li>Missing vectors: <Badge tone={consistency.missing_vector_count ? "red" : "green"}>{consistency.missing_vector_count}</Badge></li>
              <li>Orphan vectors: <Badge tone={consistency.orphan_vector_count ? "amber" : "green"}>{consistency.orphan_vector_count}</Badge></li>
              <li>Orphan jobs: <Badge tone={consistency.orphan_jobs.length ? "red" : "green"}>{consistency.orphan_jobs.length}</Badge></li>
              <li>Reconciliation required: <Badge tone={consistency.reconciliation_required_count ? "red" : "green"}>{consistency.reconciliation_required_count ?? 0}</Badge></li>
              <li>Fine-cache intervals: {consistency.fine_cache_intervals ?? 0}</li>
              <li>Invalid cache manifests: <Badge tone={consistency.invalid_manifest_count ? "amber" : "green"}>{consistency.invalid_manifest_count ?? 0}</Badge></li>
              <li>Orphan cache manifests: <Badge tone={consistency.orphan_manifest_count ? "amber" : "green"}>{consistency.orphan_manifest_count ?? 0}</Badge></li>
              <li>Model consistency: <Badge tone={consistency.model_consistency?.consistent === false ? "red" : "green"}>{consistency.model_consistency?.consistent === false ? "mismatch" : "ok"}</Badge></li>
            </ul>
          </div>
        ) : (
          <div className="flex justify-center py-6"><Spinner /></div>
        )}
      </Modal>

      <ConfirmDialog
        open={confirmClear}
        onClose={() => setConfirmClear(false)}
        title="Clear all indexed data"
        description="This permanently deletes every video, frame, embedding and job from this instance. Configuration files are NOT affected. This action cannot be undone."
        confirmLabel="Delete everything"
        requireTypedConfirmation="DELETE ALL"
        onConfirm={async () => {
          if (clearing) return; // guard against duplicate DELETE ALL requests
          setAdminToken(adminToken);
          setClearing(true);
          try {
            await api.clearAllData("DELETE ALL");
            onDataCleared();
            await refresh();
          } catch (err) {
            // surfaced by the api client; keep the dialog-closed state clean
            void err;
          } finally {
            setClearing(false);
          }
        }}
      >
        <div className="mb-4">
          <label className="label">Admin token (if required by the backend)</label>
          <input
            className="input"
            type="password"
            value={adminToken}
            onChange={(e) => setAdminToken(e.target.value)}
            placeholder="optional"
          />
        </div>
      </ConfirmDialog>
    </>
  );
}
