from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from telegram import Bot, InputFile
from telegram.error import TelegramError

from config import Settings
from services.ffmpeg_service import FFmpegError, FFmpegService, format_bytes
from services.memory_store import MemoryStore, PendingVideo
from services.upload_progress import ProgressFile

logger = logging.getLogger(__name__)


@dataclass
class CompressionJob:
    pending: PendingVideo


class CompressionQueue:
    def __init__(self, settings: Settings, store: MemoryStore, ffmpeg: FFmpegService) -> None:
        self.settings = settings
        self.store = store
        self.ffmpeg = ffmpeg
        self._queue: asyncio.Queue[CompressionJob] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._user_locks: dict[int, asyncio.Lock] = {}
        self._active_users: set[int] = set()
        self._stopping = False

    async def start(self) -> None:
        self.settings.temp_directory.mkdir(parents=True, exist_ok=True)
        self._stopping = False
        for index in range(self.settings.max_concurrent_jobs):
            self._workers.append(asyncio.create_task(self._worker(index), name=f"compression-worker-{index}"))

    async def stop(self) -> None:
        self._stopping = True
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        while not self._queue.empty():
            try:
                job = self._queue.get_nowait()
                job.pending.source_path.unlink(missing_ok=True)
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
        for pending in self.store.clear_all():
            pending.source_path.unlink(missing_ok=True)

    def queue_size(self) -> int:
        return self._queue.qsize()

    def active_count(self) -> int:
        return len(self._active_users)

    def is_busy_for_user(self, user_id: int) -> bool:
        return user_id in self._active_users or self.store.queue_position(user_id) is not None

    def is_active_for_user(self, user_id: int) -> bool:
        return user_id in self._active_users

    async def enqueue(self, pending: PendingVideo) -> int:
        if self._stopping:
            raise RuntimeError("Compression queue is stopping")
        position = self._queue.qsize() + len(self._active_users) + 1
        self.store.mark_queued(pending.user_id, position)
        await self._queue.put(CompressionJob(pending))
        return position

    async def _worker(self, worker_id: int) -> None:
        logger.info("Compression worker %s started", worker_id)
        while True:
            job = await self._queue.get()
            pending = job.pending
            lock = self._user_locks.setdefault(pending.user_id, asyncio.Lock())
            try:
                async with lock:
                    await self._run_job(pending)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Compression job failed for user %s", pending.user_id)
            finally:
                self.store.clear_queued(pending.user_id)
                self._queue.task_done()
                if not lock.locked():
                    self._user_locks.pop(pending.user_id, None)

    async def _run_job(self, pending: PendingVideo) -> None:
        self._active_users.add(pending.user_id)
        output_path = pending.source_path.with_name(f"compressed_{int(time.time())}_{pending.user_id}.mp4")
        try:
            bot = getattr(self, "bot", None)
            if bot is None:
                raise RuntimeError("CompressionQueue.bot must be assigned before workers start")
            resolution = pending.resolution or "original"
            self.store.set_progress(pending.user_id, "compression", 0.0)
            estimate = self.ffmpeg.estimate_output_text(pending.metadata, resolution, pending.target_size_mb)
            status = await _safe_send(
                bot,
                pending.chat_id,
                f"Compression: 0%\nResolution: {resolution_label(resolution)}\nEstimated output: {estimate}",
            )

            last_compression_update = -1.0
            last_compression_time = 0.0

            async def compression_progress(percent: float, stage: str) -> None:
                nonlocal last_compression_update, last_compression_time
                if status is None:
                    return
                now = time.monotonic()
                if percent < 100 and percent - last_compression_update < 5 and now - last_compression_time < 1.0:
                    return
                last_compression_update = percent
                last_compression_time = now
                self.store.set_progress(pending.user_id, "compression", percent)
                await _safe_edit(
                    bot,
                    pending.chat_id,
                    status.message_id,
                    f"Compression: {percent:.0f}%\nStage: {stage}\nEstimated output: {estimate}",
                )

            await self.ffmpeg.compress(
                pending.source_path,
                output_path,
                pending.metadata,
                resolution,
                pending.target_size_mb,
                compression_progress,
            )
            output_size = output_path.stat().st_size
            if self.settings.max_output_bytes and output_size > self.settings.max_output_bytes:
                raise FFmpegError(
                    f"Output is {format_bytes(output_size)}, above the configured safety limit "
                    f"of {format_bytes(self.settings.max_output_bytes)}."
                )

            await _safe_edit(
                bot,
                pending.chat_id,
                status.message_id if status else None,
                f"Compression: 100%\nPreparing Telegram upload…\nCompressed file: {format_bytes(output_size)}",
            )

            last_upload_update = -1.0
            last_upload_time = 0.0

            async def upload_progress(percent: float) -> None:
                nonlocal last_upload_update, last_upload_time
                now = time.monotonic()
                if percent < 100 and percent - last_upload_update < 5 and now - last_upload_time < 1.0:
                    return
                last_upload_update = percent
                last_upload_time = now
                self.store.set_progress(pending.user_id, "upload", percent)
                await _safe_edit(
                    bot,
                    pending.chat_id,
                    status.message_id if status else None,
                    f"Uploading to Telegram: {percent:.0f}%\nCompressed file: {format_bytes(output_size)}",
                )

            caption = (
                f"Compressed: {pending.original_name}\n"
                f"Resolution: {resolution_label(resolution)}\n"
                f"Output size: {format_bytes(output_size)}"
            )
            output_filename = f"compressed_{Path(pending.original_name).stem}.mp4"
            if self.settings.local_bot_api:
                self.store.set_progress(pending.user_id, "telegram", 0.0)
                # PTB converts a local Path to a file:// URI when local_mode=True.
                # The Local Bot API process can read the file directly from the same
                # container, avoiding a second Python-side multipart stream.
                await _safe_edit(
                    bot,
                    pending.chat_id,
                    status.message_id if status else None,
                    f"Sending through Local Bot API…\nCompressed file: {format_bytes(output_size)}",
                )
                await bot.send_document(
                    chat_id=pending.chat_id,
                    document=output_path,
                    filename=output_filename,
                    caption=caption[:1024],
                    read_timeout=3600,
                    write_timeout=3600,
                    connect_timeout=60,
                    pool_timeout=60,
                )
            else:
                self.store.set_progress(pending.user_id, "upload", 0.0)
                with output_path.open("rb") as raw_file:
                    tracked_file = ProgressFile(raw_file, output_size, upload_progress)
                    await bot.send_document(
                        chat_id=pending.chat_id,
                        document=InputFile(
                            tracked_file,
                            filename=output_filename,
                            read_file_handle=False,
                        ),
                        caption=caption[:1024],
                        read_timeout=3600,
                        write_timeout=3600,
                        connect_timeout=60,
                        pool_timeout=60,
                    )
                    await tracked_file.wait_for_callbacks()

            self.store.set_progress(pending.user_id, "complete", 100.0)
            await _safe_edit(
                bot,
                pending.chat_id,
                status.message_id if status else None,
                "Done. The compressed video was sent above.",
            )
        except FFmpegError as exc:
            await _safe_send(bot, pending.chat_id, f"Compression failed: {exc}")
        except TelegramError as exc:
            await _safe_send(bot, pending.chat_id, f"Telegram could not send the compressed file: {exc}")
        except Exception as exc:
            logger.exception("Unexpected compression failure")
            await _safe_send(bot, pending.chat_id, f"Compression failed unexpectedly: {exc}")
        finally:
            pending.source_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            self.store.clear_progress(pending.user_id)
            self._active_users.discard(pending.user_id)


def resolution_label(resolution: str) -> str:
    return "original resolution" if resolution == "original" else f"{resolution}p"


async def _safe_send(bot: Bot, chat_id: int, text: str):
    try:
        return await bot.send_message(chat_id=chat_id, text=text)
    except TelegramError:
        logger.warning("Could not send status message to chat %s", chat_id)
        return None


async def _safe_edit(bot: Bot, chat_id: int, message_id: int | None, text: str) -> None:
    if message_id is None:
        return
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
    except TelegramError:
        pass
