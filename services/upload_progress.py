from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import BinaryIO


UploadProgressCallback = Callable[[float], Awaitable[None]]


class ProgressFile:
    """File-like wrapper used by HTTPX multipart uploads.

    The Local Bot API receives the file over the container's loopback connection. This reports
    the percentage sent to that API request. The Local Bot API may still need additional time to
    forward the file to Telegram after the local upload reaches 100 percent.
    """

    def __init__(self, file: BinaryIO, total_bytes: int, callback: UploadProgressCallback) -> None:
        self._file = file
        self._total_bytes = max(1, total_bytes)
        self._callback = callback
        self._loop = asyncio.get_running_loop()
        self._scheduled: set[asyncio.Task] = set()
        self._sent_bytes = 0
        self._last_reported = -1.0
        self._last_report_time = 0.0

    def read(self, size: int = -1) -> bytes:
        chunk = self._file.read(size)
        if chunk:
            self._sent_bytes += len(chunk)
        percent = min(100.0, self._sent_bytes * 100.0 / self._total_bytes)
        now = time.monotonic()
        if percent >= 100.0 or percent - self._last_reported >= 2.0 or now - self._last_report_time >= 0.75:
            self._last_reported = percent
            self._last_report_time = now
            task = self._loop.create_task(self._callback(percent))
            self._scheduled.add(task)
            task.add_done_callback(self._scheduled.discard)
        return chunk

    def seek(self, offset: int, whence: int = 0) -> int:
        position = self._file.seek(offset, whence)
        if whence == 0 and offset == 0:
            self._sent_bytes = 0
            self._last_reported = -1.0
            self._last_report_time = 0.0
        return position

    def tell(self) -> int:
        return self._file.tell()

    def fileno(self) -> int:
        return self._file.fileno()

    def flush(self) -> None:
        self._file.flush()

    async def wait_for_callbacks(self) -> None:
        if self._scheduled:
            await asyncio.gather(*tuple(self._scheduled), return_exceptions=True)

    def __getattr__(self, name: str):
        return getattr(self._file, name)
