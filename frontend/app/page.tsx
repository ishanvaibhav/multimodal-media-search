"use client";

import { useCallback, useState } from "react";
import { Header } from "@/components/Header";
import { UploadPanel } from "@/components/upload/UploadPanel";
import { SearchPanel } from "@/components/search/SearchPanel";
import { ResultsPanel } from "@/components/results/ResultsPanel";
import { MediaLibrary } from "@/components/media/MediaLibrary";
import { JobsPanel } from "@/components/jobs/JobsPanel";
import { VideoPlayer, type PlayerTarget } from "@/components/player/VideoPlayer";
import { Modal } from "@/components/ui/Modal";
import { SavedContexts } from "@/components/context/SavedContexts";
import { useSearch } from "@/hooks/useSearch";
import { useMedia } from "@/hooks/useMedia";
import { useSystemStatus } from "@/hooks/useSystemStatus";
import { absoluteUrl } from "@/lib/api";
import type { SavedContext, SearchResult, VideoItem } from "@/lib/types";

export default function Home() {
  const search = useSearch();
  const media = useMedia();
  const { maintenance } = useSystemStatus();
  const [showUpload, setShowUpload] = useState(true);
  const [showResults, setShowResults] = useState(true);
  const [player, setPlayer] = useState<PlayerTarget | null>(null);
  const [imageView, setImageView] = useState<{ name: string; url: string } | null>(null);
  const [mediaVersion, setMediaVersion] = useState(0);

  const refreshMedia = useCallback(() => {
    void media.refresh();
    setMediaVersion((v) => v + 1);
  }, [media]);

  const openMedia = useCallback((name: string, streamUrl: string, timestamp: number, isImage: boolean) => {
    if (isImage) {
      setImageView({ name, url: streamUrl });
    } else {
      setPlayer({ videoName: name, streamUrl, startAt: timestamp });
    }
  }, []);

  const playResult = useCallback(
    (r: SearchResult) => {
      if (r.media_type === "image") {
        openMedia(r.video_name, r.frame_url || r.stream_url, 0, true);
        return;
      }
      setPlayer({
        videoName: r.video_name,
        streamUrl: r.stream_url,
        startAt: r.timestamp,
        endAt: r.context_end ?? null,
      });
    },
    [openMedia],
  );

  const playVideo = useCallback(
    (v: VideoItem) => {
      openMedia(v.filename, v.stream_url, 0, v.media_type === "image");
    },
    [openMedia],
  );

  // "Play Context Segment": open the player and immediately play start→end.
  const playSegment = useCallback((r: SearchResult) => {
    if (r.media_type === "image") return;
    setPlayer({
      videoName: r.video_name,
      streamUrl: r.stream_url,
      startAt: r.timestamp,
      endAt: r.context_end ?? r.timestamp,
      autoPlayContext: true,
    });
  }, []);

  // Reopen a saved context: restore the query + seek the player to the
  // saved timestamp/segment.
  const openSavedContext = useCallback(
    (s: SavedContext) => {
      search.setQuery(s.query);
      setPlayer({
        videoName: s.filename,
        streamUrl: `/api/media/${s.video_id}/stream`,
        startAt: s.timestamp_seconds,
        endAt: s.context_end ?? null,
      });
    },
    [search],
  );

  return (
    <div className="flex min-h-screen flex-col">
      <Header onDataCleared={refreshMedia} />

      <main className="mx-auto w-full max-w-[1400px] flex-1 space-y-4 p-4">
        <UploadPanel
          hidden={!showUpload}
          onToggleHidden={() => setShowUpload((s) => !s)}
          onUploaded={refreshMedia}
          maintenance={maintenance}
        />

        <SearchPanel search={search} videos={media.items} />

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
          <div className="space-y-4 xl:col-span-2">
            <ResultsPanel
              response={search.results}
              hidden={!showResults}
              onToggleHidden={() => setShowResults((s) => !s)}
              onClearResults={search.clearResults}
              onPlay={playResult}
              onPlaySegment={playSegment}
            />
            {search.error && (
              <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
                {search.error}
              </div>
            )}
          </div>

          <div className="space-y-4">
            <JobsPanel />
            <MediaLibrary
              onOpen={() => undefined}
              onPlay={playVideo}
              maintenance={maintenance}
            />
            <SavedContexts onOpen={openSavedContext} />
          </div>
        </div>
      </main>

      <footer className="border-t border-surface-800 px-4 py-3 text-center text-xs text-slate-600">
        AI Media Search — SigLIP + ChromaDB image & video retrieval · mediaVersion {mediaVersion}
      </footer>

      <VideoPlayer target={player} onClose={() => setPlayer(null)} />

      <Modal open={imageView !== null} onClose={() => setImageView(null)} title={imageView?.name ?? "Image"}>
        {imageView && (
          <div className="flex justify-center">
            <img
              src={absoluteUrl(imageView.url)}
              alt={imageView.name}
              className="max-h-[70vh] max-w-full rounded-lg object-contain"
            />
          </div>
        )}
      </Modal>
    </div>
  );
}
