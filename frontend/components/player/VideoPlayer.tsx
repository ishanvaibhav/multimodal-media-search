"use client";

import { useEffect, useRef, useState } from "react";
import { absoluteUrl } from "@/lib/api";
import { formatHms } from "@/lib/format";
import { Badge } from "../ui/primitives";
import { Modal } from "../ui/Modal";

export interface PlayerTarget {
  videoName: string;
  streamUrl: string;
  startAt: number; // seconds
}

export function VideoPlayer({
  target,
  onClose,
}: {
  target: PlayerTarget | null;
  onClose: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [current, setCurrent] = useState(0);
  const [seeked, setSeeked] = useState(false);

  useEffect(() => {
    if (target) setSeeked(false);
  }, [target]);

  useEffect(() => {
    const el = videoRef.current;
    if (!el || !target || seeked) return;
    const seek = () => {
      el.currentTime = target.startAt;
      setSeeked(true);
    };
    el.addEventListener("loadedmetadata", seek);
    return () => el.removeEventListener("loadedmetadata", seek);
  }, [target, seeked]);

  return (
    <Modal open={target !== null} onClose={onClose} title={target?.videoName ?? "Video"} wide>
      {target && (
        <div>
          <video
            ref={videoRef}
            src={absoluteUrl(target.streamUrl)}
            controls
            autoPlay
            playsInline
            className="w-full rounded-lg bg-black"
            onTimeUpdate={(e) => setCurrent(e.currentTarget.currentTime)}
          />
          <div className="mt-2 flex items-center justify-between text-xs text-slate-400">
            <span>
              Position: <span className="font-mono text-accent-soft">{formatHms(current)}</span>
            </span>
            <Badge tone="blue">Started at {formatHms(target.startAt)}</Badge>
          </div>
        </div>
      )}
    </Modal>
  );
}
