"use client";

import { useCallback, useRef, useState } from "react";
import { ChunkedUploader, type UploadProgress } from "@/lib/uploads";

export interface UploadEntry extends UploadProgress {
  id: string;
  paused: boolean;
}

export function useUploads(onUploaded: () => void, chunkSize?: number) {
  const [entries, setEntries] = useState<UploadEntry[]>([]);
  const uploaders = useRef<Map<string, ChunkedUploader>>(new Map());
  const idCounter = useRef(0);

  const patch = useCallback((id: string, patchFn: (e: UploadEntry) => UploadEntry) => {
    setEntries((prev) => prev.map((e) => (e.id === id ? patchFn(e) : e)));
  }, []);

  const addFiles = useCallback(
    (files: File[]) => {
      for (const file of files) {
        const id = `upload-${++idCounter.current}`;
        const uploader = new ChunkedUploader(file, {
          chunkSize,
          onProgress: (p) => patch(id, (e) => ({ ...e, ...p, paused: uploader.isPaused })),
          onComplete: () => {
            patch(id, (e) => ({ ...e, phase: "done" as const, paused: false }));
            if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("ai-media:uploaded"));
            onUploaded();
          },
          onError: (p) => patch(id, (e) => ({ ...e, ...p, paused: false })),
        });
        uploaders.current.set(id, uploader);
        setEntries((prev) => [
          ...prev,
          { id, uploadId: "", filename: file.name, fileSize: file.size, chunkSize: 0, totalChunks: 0, uploadedChunks: 0, uploadedBytes: 0, currentChunk: 0, progressPct: 0, speedBps: 0, etaSeconds: null, phase: "preparing", paused: false },
        ]);
        void uploader.start();
      }
    },
    [onUploaded, patch, chunkSize],
  );

  const pause = useCallback((id: string) => { uploaders.current.get(id)?.pause(); patch(id, (e) => ({ ...e, paused: true })); }, [patch]);
  const resume = useCallback((id: string) => { const u = uploaders.current.get(id); if (u) { void u.resume(); patch(id, (e) => ({ ...e, paused: false })); } }, [patch]);
  const cancel = useCallback((id: string) => { uploaders.current.get(id)?.cancel(); patch(id, (e) => ({ ...e, phase: "cancelled" as const })); }, [patch]);
  const clearFinished = useCallback(() => { setEntries((prev) => prev.filter((e) => e.phase !== "done" && e.phase !== "cancelled" && e.phase !== "error")); }, []);

  const activeCount = entries.filter((e) => e.phase === "uploading" || e.phase === "preparing" || e.phase === "completing" || e.paused).length;
  return { entries, addFiles, pause, resume, cancel, clearFinished, activeCount };
}
