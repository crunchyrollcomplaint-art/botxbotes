from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shutil
from pathlib import Path
from typing import Awaitable, Callable

from config import Settings
from services.memory_store import VideoMetadata

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[float, str], Awaitable[None]]

RESOLUTION_HEIGHTS: dict[str, int | None] = {
    "360": 360,
    "480": 480,
    "720": 720,
    "1080": 1080,
    "original": None,
}

# Conservative H.264 bitrate starting points. The final estimate also accounts for audio and duration.
BASE_VIDEO_BITRATES = {360: 800_000, 480: 1_400_000, 720: 2_500_000, 1080: 4_500_000}
_PROGRESS_TIME_RE = re.compile(r"out_time_ms=(\d+)")


class FFmpegError(RuntimeError):
    pass


class FFmpegService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if shutil.which(settings.ffmpeg_binary) is None and not Path(settings.ffmpeg_binary).exists():
            logger.warning("FFmpeg binary was not found at startup: %s", settings.ffmpeg_binary)
        if shutil.which(settings.ffprobe_binary) is None and not Path(settings.ffprobe_binary).exists():
            logger.warning("FFprobe binary was not found at startup: %s", settings.ffprobe_binary)

    async def probe(self, path: Path) -> VideoMetadata:
        command = [
            self.settings.ffprobe_binary,
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace")[-1200:]
            raise FFmpegError(f"FFprobe could not read this video: {detail}")
        try:
            payload = json.loads(stdout.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise FFmpegError("FFprobe returned invalid metadata") from exc

        video = next((s for s in payload.get("streams", []) if s.get("codec_type") == "video"), None)
        if not video:
            raise FFmpegError("The uploaded file does not contain a video stream")
        audio = next((s for s in payload.get("streams", []) if s.get("codec_type") == "audio"), None)
        duration = _float_or_zero(video.get("duration")) or _float_or_zero(payload.get("format", {}).get("duration"))
        if duration <= 0:
            raise FFmpegError("The video duration could not be determined")
        return VideoMetadata(
            duration_seconds=duration,
            width=int(video.get("width") or 0),
            height=int(video.get("height") or 0),
            video_bitrate=_int_or_none(video.get("bit_rate")),
            audio_bitrate=_int_or_none(audio.get("bit_rate")) if audio else None,
            format_name=str(payload.get("format", {}).get("format_name", "unknown")),
            file_size_bytes=path.stat().st_size,
        )

    def estimate_output_bytes(
        self,
        metadata: VideoMetadata,
        resolution: str,
        target_size_mb: int | None = None,
    ) -> int:
        if target_size_mb is not None:
            return max(1, target_size_mb) * 1024 * 1024
        height = RESOLUTION_HEIGHTS.get(resolution)
        if height is None:
            video_bitrate = metadata.video_bitrate or 4_000_000
        else:
            source_height = metadata.height or height
            source_bitrate = metadata.video_bitrate or BASE_VIDEO_BITRATES.get(_nearest_height(source_height), 2_500_000)
            scale = min(1.0, height / max(1, source_height))
            baseline = BASE_VIDEO_BITRATES[height]
            video_bitrate = int(min(source_bitrate * max(scale, 0.45), baseline))
            video_bitrate = max(350_000, video_bitrate)
        audio_bitrate = metadata.audio_bitrate or 128_000
        overhead_bitrate = 16_000
        seconds = max(1.0, metadata.duration_seconds)
        return int(seconds * (video_bitrate + audio_bitrate + overhead_bitrate) / 8)

    def estimate_output_text(self, metadata: VideoMetadata, resolution: str, target_size_mb: int | None = None) -> str:
        return format_bytes(self.estimate_output_bytes(metadata, resolution, target_size_mb))

    async def compress(
        self,
        input_path: Path,
        output_path: Path,
        metadata: VideoMetadata,
        resolution: str,
        target_size_mb: int | None = None,
        progress: ProgressCallback | None = None,
    ) -> Path:
        if resolution not in RESOLUTION_HEIGHTS:
            raise FFmpegError(f"Unsupported resolution preset: {resolution}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        filter_arg = _scale_filter(resolution)
        if target_size_mb is None:
            args = [
                self.settings.ffmpeg_binary, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(input_path),
            ]
            if filter_arg:
                args += ["-vf", filter_arg]
            args += [
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", "-f", "mp4", str(output_path),
            ]
            await self._run_with_progress(args, metadata.duration_seconds, progress, "Compressing", 0.0, 100.0)
        else:
            bitrate = self._target_video_bitrate(metadata, target_size_mb)
            passlog = output_path.with_suffix(".twopass")
            first_pass = [
                self.settings.ffmpeg_binary, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(input_path),
            ]
            if filter_arg:
                first_pass += ["-vf", filter_arg]
            first_pass += [
                "-c:v", "libx264", "-preset", "veryfast", "-b:v", str(bitrate),
                "-pass", "1", "-passlogfile", str(passlog), "-an", "-f", "mp4", os.devnull,
            ]
            second_pass = [
                self.settings.ffmpeg_binary, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(input_path),
            ]
            if filter_arg:
                second_pass += ["-vf", filter_arg]
            second_pass += [
                "-c:v", "libx264", "-preset", "veryfast", "-b:v", str(bitrate),
                "-pass", "2", "-passlogfile", str(passlog),
                "-c:a", "aac", "-b:a", "96k", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", "-f", "mp4", str(output_path),
            ]
            try:
                await self._run_with_progress(first_pass, metadata.duration_seconds, progress, "Analysing bitrate", 0.0, 45.0)
                await self._run_with_progress(second_pass, metadata.duration_seconds, progress, "Encoding", 45.0, 100.0)
            finally:
                for sidecar in output_path.parent.glob(f"{passlog.name}*"):
                    sidecar.unlink(missing_ok=True)

        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise FFmpegError("FFmpeg finished without creating an output file")
        return output_path

    def _target_video_bitrate(self, metadata: VideoMetadata, target_size_mb: int) -> int:
        target_bytes = target_size_mb * 1024 * 1024
        duration = max(1.0, metadata.duration_seconds)
        audio_bitrate = 96_000
        overhead = 0.04
        usable_bits = target_bytes * 8 * (1.0 - overhead)
        bitrate = int(usable_bits / duration - audio_bitrate)
        if bitrate < 160_000:
            raise FFmpegError("The requested target size is too small for this video's duration")
        return min(bitrate, 20_000_000)

    async def _run_with_progress(
        self,
        args: list[str],
        duration_seconds: float,
        callback: ProgressCallback | None,
        stage: str,
        start_percent: float,
        end_percent: float,
    ) -> None:
        command = list(args) + ["-progress", "pipe:1", "-nostats"]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stderr_task = asyncio.create_task(process.stderr.read() if process.stderr else asyncio.sleep(0))
        last_sent = -1.0
        try:
            while process.stdout is not None:
                raw = await process.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                match = _PROGRESS_TIME_RE.fullmatch(line)
                if match and callback:
                    elapsed = int(match.group(1)) / 1_000_000
                    ratio = min(1.0, max(0.0, elapsed / max(0.1, duration_seconds)))
                    percent = start_percent + ratio * (end_percent - start_percent)
                    if percent - last_sent >= 2.0 or percent >= end_percent:
                        last_sent = percent
                        try:
                            await callback(percent, stage)
                        except Exception:
                            logger.debug("Progress callback failed", exc_info=True)
            return_code = await process.wait()
            stderr = await stderr_task
            if return_code != 0:
                detail = stderr.decode("utf-8", errors="replace")[-1800:]
                raise FFmpegError(detail or f"FFmpeg exited with code {return_code}")
            if callback:
                await callback(end_percent, stage)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            if not stderr_task.done():
                stderr_task.cancel()
                await asyncio.gather(stderr_task, return_exceptions=True)


def _scale_filter(resolution: str) -> str | None:
    height = RESOLUTION_HEIGHTS[resolution]
    if height is None:
        return None
    # Do not upscale small videos; keep their aspect ratio and make width even.
    return f"scale=w='if(gt(ih,{height}),-2,iw)':h='if(gt(ih,{height}),{height},ih)'"


def format_bytes(value: int | float) -> str:
    number = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if number < 1024 or unit == "TB":
            return f"{number:.1f} {unit}" if unit != "B" else f"{number:.0f} B"
        number /= 1024
    return f"{number:.1f} TB"


def _float_or_zero(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _int_or_none(value: object) -> int | None:
    try:
        number = int(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _nearest_height(height: int) -> int:
    return min(BASE_VIDEO_BITRATES, key=lambda candidate: abs(candidate - height))
