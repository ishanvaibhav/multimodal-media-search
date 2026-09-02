"use client";

import { MediaLibrary } from "@/components/media/MediaLibrary";

export default function LibraryPage() {
  return (
    <main className="min-h-screen bg-surface-950 p-4 sm:p-6 lg:p-8">
      <div className="mx-auto min-h-[calc(100vh-3rem)] max-w-[1800px]">
        <MediaLibrary
          onOpen={(video) => window.dispatchEvent(new CustomEvent("ai-media:library-open", { detail: video }))}
          onPlay={(video) => window.dispatchEvent(new CustomEvent("ai-media:library-open", { detail: video }))}
        />
      </div>
    </main>
  );
}
