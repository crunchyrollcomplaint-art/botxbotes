from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class VideoMetadata:
    duration_seconds: float
    width: int
    height: int
    video_bitrate: int | None
    audio_bitrate: int | None
    format_name: str
    file_size_bytes: int


@dataclass
class PendingVideo:
    user_id: int
    chat_id: int
    token: str
    source_path: Path
    original_name: str
    metadata: VideoMetadata
    resolution: str | None = None
    target_size_mb: int | None = None
    created_at: float = field(default_factory=time.time)


class MemoryStore:
    def __init__(self) -> None:
        self._pending: dict[int, PendingVideo] = {}
        self._tokens: dict[str, int] = {}
        self._waiting_custom_size: set[int] = set()
        self._queued: dict[int, int] = {}
        self._progress: dict[int, tuple[str, float]] = {}

    def create_pending(self, *, user_id: int, chat_id: int, source_path: Path, original_name: str, metadata: VideoMetadata) -> PendingVideo:
        self.clear_pending(user_id)
        token = secrets.token_urlsafe(8)
        pending = PendingVideo(user_id, chat_id, token, source_path, original_name, metadata)
        self._pending[user_id] = pending
        self._tokens[token] = user_id
        return pending

    def get_pending(self, user_id: int) -> PendingVideo | None:
        return self._pending.get(user_id)

    def get_by_token(self, token: str) -> PendingVideo | None:
        user_id = self._tokens.get(token)
        return self._pending.get(user_id) if user_id is not None else None

    def set_resolution(self, user_id: int, resolution: str) -> PendingVideo | None:
        pending = self.get_pending(user_id)
        if pending:
            pending.resolution = resolution
        return pending

    def set_target_size(self, user_id: int, target_size_mb: int | None) -> PendingVideo | None:
        pending = self.get_pending(user_id)
        if pending:
            pending.target_size_mb = target_size_mb
        return pending

    def mark_waiting_custom_size(self, user_id: int, waiting: bool = True) -> None:
        if waiting:
            self._waiting_custom_size.add(user_id)
        else:
            self._waiting_custom_size.discard(user_id)

    def is_waiting_custom_size(self, user_id: int) -> bool:
        return user_id in self._waiting_custom_size

    def mark_queued(self, user_id: int, position: int) -> None:
        self._queued[user_id] = position

    def queue_position(self, user_id: int) -> int | None:
        return self._queued.get(user_id)

    def clear_queued(self, user_id: int) -> None:
        self._queued.pop(user_id, None)

    def set_progress(self, user_id: int, stage: str, percent: float) -> None:
        self._progress[user_id] = (stage, max(0.0, min(100.0, percent)))

    def progress(self, user_id: int) -> tuple[str, float] | None:
        return self._progress.get(user_id)

    def clear_progress(self, user_id: int) -> None:
        self._progress.pop(user_id, None)

    def clear_pending(self, user_id: int) -> PendingVideo | None:
        pending = self._pending.pop(user_id, None)
        self._waiting_custom_size.discard(user_id)
        if pending:
            self._tokens.pop(pending.token, None)
        return pending

    def clear_user(self, user_id: int) -> PendingVideo | None:
        self.clear_queued(user_id)
        return self.clear_pending(user_id)

    def clear_all(self) -> list[PendingVideo]:
        pending = list(self._pending.values())
        self._pending.clear()
        self._tokens.clear()
        self._waiting_custom_size.clear()
        self._queued.clear()
        self._progress.clear()
        return pending
