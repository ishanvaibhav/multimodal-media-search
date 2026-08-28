"""FFmpeg / FFprobe integration.

Robust executable discovery order:
  1. explicit FFMPEG_PATH / FFPROBE_PATH configuration
  2. ``ffmpeg`` / ``ffprobe`` on PATH
  3. the binary bundled with ``imageio-ffmpeg`` (works on Windows without a
     system install)

Frame timestamps are read from ffmpeg's ``showinfo`` filter output so that they
reflect *actual media presentation timestamps* (correct for VFR sources),
falling back to index*interval if parsing is unavailable.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time as _time
from pathlib import Path
from typing import Callable, Optional

from ..config import Settings
from ..domain.models import FrameSample, MediaInfo
from ..exceptions import FFmpegNotFoundError, MediaProcessingError
from ..logging_config import get_logger
from ..utils import parse_duration, parse_fraction
from . import metrics

log = get_logger(__name__)


class FFmpegService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._ffmpeg: Optional[Path] = None
        self._ffprobe: Optional[Path] = None
        self._ffprobe_missing = False

    # ------------------------------------------------------------------
    def resolve_ffmpeg(self) -> Path:
        if self._ffmpeg is not None:
            return self._ffmpeg
        if self.settings.ffmpeg_path:
            candidate = Path(self.settings.ffmpeg_path)
            if candidate.is_dir():
                candidate = candidate / ("ffmpeg.exe" if _is_windows() else "ffmpeg")
            if candidate.exists():
                self._ffmpeg = candidate
                return self._ffmpeg
        found = shutil.which("ffmpeg")
        if found:
            self._ffmpeg = Path(found)
            return self._ffmpeg
        try:
            import imageio_ffmpeg  # bundled binary, no system install needed

            self._ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe())
            return self._ffmpeg
        except Exception as exc:  # pragma: no cover
            raise FFmpegNotFoundError(
                "FFmpeg not found. Install FFmpeg, set FFMPEG_PATH, or "
                "`pip install imageio-ffmpeg`."
            ) from exc

    def resolve_ffprobe(self) -> Optional[Path]:
        if self._ffprobe is not None or self._ffprobe_missing:
            return self._ffprobe
        if self.settings.ffprobe_path:
            candidate = Path(self.settings.ffprobe_path)
            if candidate.exists():
                self._ffprobe = candidate
                return self._ffprobe
        found = shutil.which("ffprobe")
        if found:
            self._ffprobe = Path(found)
            return self._ffprobe
        self._ffprobe_missing = True
        return None

    def ffmpeg_available(self) -> bool:
        try:
            return self.resolve_ffmpeg() is not None
        except FFmpegNotFoundError:
            return False

    def ffprobe_available(self) -> bool:
        return self.resolve_ffprobe() is not None

    def ffmpeg_version(self) -> str:
        try:
            out = subprocess.run(
                [str(self.resolve_ffmpeg()), "-version"],
                capture_output=True, text=True, timeout=20,
            )
            return (out.stdout or out.stderr).splitlines()[0]
        except Exception:
            return "unavailable"

    # ------------------------------------------------------------------
    def probe(self, path: Path) -> MediaInfo:
        ffprobe = self.resolve_ffprobe()
        if ffprobe is not None:
            try:
                return self._probe_with_ffprobe(path, ffprobe)
            except MediaProcessingError:
                log.warning("ffprobe failed for %s; falling back to ffmpeg -i", path)
        return self._probe_with_ffmpeg(path)

    def _probe_with_ffprobe(self, path: Path, ffprobe: Path) -> MediaInfo:
        if not path.exists():
            raise MediaProcessingError("media file not found")
        cmd = [
            str(ffprobe), "-v", "error",
            "-show_streams", "-show_format", "-print_format", "json", str(path),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except (subprocess.TimeoutExpired, OSError) as exc:
            metrics.inc("ffprobe.failures")
            raise MediaProcessingError(f"ffprobe failed: {exc}") from exc
        if proc.returncode != 0:
            metrics.inc("ffprobe.failures")
            raise MediaProcessingError(
                f"ffprobe could not read the media file (invalid or corrupt video): "
                f"{(proc.stderr or '').strip()[:300]}"
            )
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:  # pragma: no cover
            raise MediaProcessingError("ffprobe returned invalid JSON") from exc

        info = MediaInfo()
        video_stream = None
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video" and video_stream is None:
                video_stream = stream
            if stream.get("codec_type") == "audio":
                info.has_audio = True

        if video_stream is None:
            raise MediaProcessingError("file does not contain a video stream")

        info.codec = video_stream.get("codec_name")
        info.width = video_stream.get("width")
        info.height = video_stream.get("height")
        info.fps = (
            parse_fraction(video_stream.get("avg_frame_rate"))
            or parse_fraction(video_stream.get("r_frame_rate"))
        )
        info.duration = (
            parse_duration(video_stream.get("duration"))
            or parse_duration(data.get("format", {}).get("duration"))
        )
        fmt = data.get("format", {})
        info.container = fmt.get("format_name")
        info.bitrate = fmt.get("bitrate")
        info.creation_time = fmt.get("tags", {}).get("creation_time")
        if info.fps is None and info.duration and video_stream.get("nb_frames"):
            try:
                info.fps = int(video_stream["nb_frames"]) / float(info.duration)
            except (ValueError, ZeroDivisionError):
                pass
        return info

    def _probe_with_ffmpeg(self, path: Path) -> MediaInfo:
        """Fallback: parse the stderr of ``ffmpeg -i`` when ffprobe is absent."""
        if not path.exists():
            raise MediaProcessingError("media file not found")
        cmd = [str(self.resolve_ffmpeg()), "-hide_banner", "-i", str(path)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except (subprocess.TimeoutExpired, OSError) as exc:
            metrics.inc("ffmpeg.failures")
            raise MediaProcessingError(f"ffmpeg failed: {exc}") from exc
        text = proc.stderr or ""
        if proc.returncode != 0 and "Duration" not in text:
            metrics.inc("ffmpeg.failures")
            raise MediaProcessingError(
                "ffmpeg could not read the media file (invalid or corrupt video)"
            )

        info = MediaInfo()
        dur = re.search(r"Duration:\s*(\d+:\d+:\d+(?:\.\d+)?)", text)
        if dur:
            info.duration = parse_duration(dur.group(1))
        bitrate = re.search(r"bitrate:\s*(\d+)\s*kb/s", text)
        if bitrate:
            info.bitrate = int(bitrate.group(1)) * 1000

        vmatch = re.search(
            r"Stream #\S+:\s*Video:\s*([^,\s]+)[^,]*,\s*\S+.*?(\d{2,5})x(\d{2,5})"
            r".*?(\d+(?:\.\d+)?)\s*fps", text
        )
        if not vmatch:
            vmatch = re.search(
                r"Stream #\S+:\s*Video:\s*([^,\s]+)[^,]*", text
            )
        if vmatch:
            info.codec = vmatch.group(1)
        wh = re.search(r"(\d{2,5})x(\d{2,5})", text)
        if wh:
            info.width = int(wh.group(1))
            info.height = int(wh.group(2))
        fps = re.search(r"(\d+(?:\.\d+)?)\s*fps", text)
        if fps:
            info.fps = float(fps.group(1))
        if re.search(r"Stream #\S+:\s*Audio:", text):
            info.has_audio = True
        if info.codec is None and "Video:" not in text:
            raise MediaProcessingError("file does not contain a video stream")
        return info

    # ------------------------------------------------------------------
    def extract_frames(
        self,
        video_path: Path,
        out_dir: Path,
        interval: float,
        on_progress: Optional[Callable[[int, int], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        timeout: float = 3600.0,
    ) -> list[FrameSample]:
        """Extract one frame every ``interval`` seconds in a single pass.

        Progress is reported by counting ``showinfo`` lines as frames are written.
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        rate = _fps_filter_rate(interval)
        cmd = [
            str(self.resolve_ffmpeg()), "-hide_banner", "-loglevel", "info",
            "-i", str(video_path),
            "-vf", f"fps={rate},showinfo",
            "-q:v", "2", "-f", "image2", str(out_dir / "frame_%06d.jpg"),
        ]
        return self._run_extract(cmd, out_dir, interval, on_progress, cancel_check, timeout)

    def extract_frames_range(
        self,
        video_path: Path,
        out_dir: Path,
        start: float,
        end: float,
        interval: float,
        on_progress: Optional[Callable[[int, int], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        timeout: float = 120.0,
    ) -> list[FrameSample]:
        """Extract frames inside [start, end] (used by fine-grained search).

        A single ffmpeg pass seeks just before ``start`` (fast input seek),
        keeps absolute presentation timestamps via ``-copyts``, and trims to
        the requested window — so every sample carries its exact media
        timestamp without decoding the whole file.
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        rate = _fps_filter_rate(interval)
        n_frames = max(1, int((end - start) / interval) + 3)
        seek_to = max(0.0, start - 5.0)
        cmd = [
            str(self.resolve_ffmpeg()), "-hide_banner", "-loglevel", "info",
            "-ss", f"{seek_to:.3f}", "-copyts", "-i", str(video_path),
            "-vf", f"fps={rate},trim=start={start:.3f}:end={end:.3f},showinfo",
            "-frames:v", str(n_frames),
            "-q:v", "2", "-f", "image2", str(out_dir / "fine_%06d.jpg"),
        ]
        return self._run_extract(cmd, out_dir, interval, on_progress, cancel_check, timeout)

    def _run_extract(
        self, cmd: list, out_dir: Path, interval: float,
        on_progress, cancel_check, timeout: float = 3600.0,
    ) -> list[FrameSample]:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )

        deadline = _time.monotonic() + max(1.0, timeout)
        timed_out = False

        def _kill() -> None:
            try:
                proc.terminate()
            except OSError:
                pass

        stopper = None
        if cancel_check is not None:
            def _poll_cancel() -> None:
                while proc.poll() is None:
                    if cancel_check():
                        _kill()
                        return
                    if _time.monotonic() > deadline:
                        timed_out = True
                        _kill()
                        return
                    threading.Event().wait(0.25)

            stopper = threading.Thread(target=_poll_cancel, daemon=True)
            stopper.start()

        timestamps: list[float] = []
        total = 0
        try:
            for line in proc.stderr:
                if "pts_time:" in line and re.search(r"\bn:\s*\d+", line):
                    match = re.search(r"pts_time:([0-9]+(?:\.[0-9]+)?)", line)
                    if match:
                        timestamps.append(float(match.group(1)))
                        total = len(timestamps)
                        if on_progress:
                            on_progress(total, -1)
                if _time.monotonic() > deadline:
                    timed_out = True
                    _kill()
                    break
        finally:
            proc.wait()
            if stopper is not None:
                stopper.join(timeout=2)

        if cancel_check is not None and cancel_check():
            raise _CancelledError()
        if timed_out:
            metrics.inc("ffmpeg.timeouts")
            raise MediaProcessingError(f"frame extraction exceeded timeout ({timeout:.0f}s)")

        files = sorted(out_dir.glob("*.jpg"))
        if not files:
            if proc.returncode != 0:
                metrics.inc("ffmpeg.failures")
                raise MediaProcessingError("frame extraction produced no output frames")
            return []

        # Map files (in order) to parsed timestamps; fall back to index*interval.
        samples: list[FrameSample] = []
        for idx, f in enumerate(files):
            ts = timestamps[idx] if idx < len(timestamps) else idx * interval
            samples.append(FrameSample(path=f, timestamp_seconds=ts))
        return samples

    def extract_frame(self, video_path: Path, timestamp: float, out_path: Path) -> bool:
        """Extract a single frame at ``timestamp`` (accurate seek)."""
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(self.resolve_ffmpeg()), "-hide_banner", "-loglevel", "error",
            "-ss", f"{timestamp:.3f}", "-i", str(video_path),
            "-frames:v", "1", "-q:v", "2", "-y", str(out_path),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return False
        return proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0

    def make_thumbnail(
        self, video_path: Path, out_path: Path, timestamp: float = 0.0, width: int = 480
    ) -> bool:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(self.resolve_ffmpeg()), "-hide_banner", "-loglevel", "error",
            "-ss", f"{max(0.0, timestamp):.3f}", "-i", str(video_path),
            "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", "-y", str(out_path),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return False
        return proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0


class _CancelledError(Exception):
    pass


def _fps_filter_rate(interval: float) -> str:
    """Render an interval into an ffmpeg ``fps`` filter rate expression."""
    if interval >= 1:
        return f"1/{int(interval)}" if float(int(interval)) == interval else f"{1.0 / interval:.10f}"
    return f"{1.0 / interval:.6f}"


def _is_windows() -> bool:
    import os
    return os.name == "nt"
