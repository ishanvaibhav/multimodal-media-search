"use client";

import { useEffect, useRef, useState } from "react";
import { absoluteUrl } from "@/lib/api";
import { formatHms } from "@/lib/format";
<<<<<<< HEAD
import { Badge, Button } from "../ui/primitives";
=======
import { Badge } from "../ui/primitives";
>>>>>>> 7ed4cd97f55f17cc4833815b4ed7fa39656cb424
import { Modal } from "../ui/Modal";

export interface PlayerTarget {
  videoName: string;
  streamUrl: string;
  startAt: number; // seconds
<<<<<<< HEAD
  endAt?: number | null; // context segment end (seconds)
  autoPlayContext?: boolean; // seek + immediately play the segment
=======
>>>>>>> 7ed4cd97f55f17cc4833815b4ed7fa39656cb424
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
<<<<<<< HEAD
  const [playingContext, setPlayingContext] = useState(false);

  useEffect(() => {
    if (target) {
      setSeeked(false);
      setPlayingContext(false);
    }
=======

  useEffect(() => {
    if (target) setSeeked(false);
>>>>>>> 7ed4cd97f55f17cc4833815b4ed7fa39656cb424
  }, [target]);

  useEffect(() => {
    const el = videoRef.current;
    if (!el || !target || seeked) return;
    const seek = () => {
      el.currentTime = target.startAt;
      setSeeked(true);
<<<<<<< HEAD
      if (target.autoPlayContext && target.endAt != null) {
        setPlayingContext(true);
        void el.play().catch(() => undefined);
      }
=======
>>>>>>> 7ed4cd97f55f17cc4833815b4ed7fa39656cb424
    };
    el.addEventListener("loadedmetadata", seek);
    return () => el.removeEventListener("loadedmetadata", seek);
  }, [target, seeked]);

<<<<<<< HEAD
  // stop playback at the end of the context segment
  useEffect(() => {
    const el = videoRef.current;
    if (!el || !playingContext || target?.endAt == null) return;
    const onTime = () => {
      if (el.currentTime >= (target.endAt as number)) {
        el.pause();
        setPlayingContext(false);
      }
    };
    el.addEventListener("timeupdate", onTime);
    return () => el.removeEventListener("timeupdate", onTime);
  }, [playingContext, target]);

  const playContext = () => {
    const el = videoRef.current;
    if (!el || !target) return;
    el.currentTime = target.startAt;
    setPlayingContext(true);
    void el.play().catch(() => undefined);
  };

  const hasContext = target != null && target.endAt != null && target.endAt > target.startAt;

=======
>>>>>>> 7ed4cd97f55f17cc4833815b4ed7fa39656cb424
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
<<<<<<< HEAD
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
            <span>
              Position: <span className="font-mono text-accent-soft">{formatHms(current)}</span>
            </span>
            <div className="flex items-center gap-2">
              <Badge tone="blue">Matched at {formatHms(target.startAt)}</Badge>
              {hasContext && (
                <Button variant="primary" onClick={playContext}>
                  ▶ Play Context ({formatHms(target.startAt)} → {formatHms(target.endAt as number)})
                </Button>
              )}
            </div>
=======
          <div className="mt-2 flex items-center justify-between text-xs text-slate-400">
            <span>
              Position: <span className="font-mono text-accent-soft">{formatHms(current)}</span>
            </span>
            <Badge tone="blue">Started at {formatHms(target.startAt)}</Badge>
>>>>>>> 7ed4cd97f55f17cc4833815b4ed7fa39656cb424
          </div>
        </div>
      )}
    </Modal>
  );
}
