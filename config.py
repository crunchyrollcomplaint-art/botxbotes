from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_api_id: int
    telegram_api_hash: str
    telegram_api_base_url: str
    local_bot_api: bool
    local_bot_api_binary: str
    local_bot_api_port: int
    local_bot_api_data_directory: Path
    local_bot_api_temp_directory: Path
    temp_directory: Path
    max_concurrent_jobs: int
    max_input_bytes: int
    max_output_bytes: int
    ffmpeg_binary: str
    ffprobe_binary: str
    update_mode: str
    port: int
    log_level: str
    local_bot_api_start_timeout_seconds: int
    queue_limit_per_user: int

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        api_id = _required_int("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
        if not api_hash:
            raise ValueError("TELEGRAM_API_HASH is required for the Local Bot API server")

        base_url = os.getenv("TELEGRAM_API_BASE_URL", "http://127.0.0.1:8081").strip().rstrip("/")
        local_mode = _bool_env("LOCAL_BOT_API", True)
        if local_mode and not base_url.startswith(("http://127.0.0.1", "http://localhost")):
            raise ValueError("LOCAL_BOT_API=true requires TELEGRAM_API_BASE_URL to point to 127.0.0.1")
        if not local_mode and base_url != "https://api.telegram.org":
            raise ValueError("LOCAL_BOT_API=false requires https://api.telegram.org")

        update_mode = os.getenv("UPDATE_MODE", "polling").strip().lower()
        if update_mode != "polling":
            raise ValueError("This compressor bot currently supports UPDATE_MODE=polling only")

        max_input = _nonnegative_int("MAX_INPUT_BYTES", 0)
        max_output = _nonnegative_int("MAX_OUTPUT_BYTES", 0)
        max_jobs = max(1, int(os.getenv("MAX_CONCURRENT_JOBS", "1")))
        queue_limit = max(1, int(os.getenv("QUEUE_LIMIT_PER_USER", "10")))

        return cls(
            telegram_bot_token=token,
            telegram_api_id=api_id,
            telegram_api_hash=api_hash,
            telegram_api_base_url=base_url,
            local_bot_api=local_mode,
            local_bot_api_binary=os.getenv("LOCAL_BOT_API_BINARY", "/usr/local/bin/telegram-bot-api").strip(),
            local_bot_api_port=max(1, int(os.getenv("LOCAL_BOT_API_PORT", "8081"))),
            local_bot_api_data_directory=Path(os.getenv("LOCAL_BOT_API_DATA_DIRECTORY", "/tmp/telegram-bot-api-data")),
            local_bot_api_temp_directory=Path(os.getenv("LOCAL_BOT_API_TEMP_DIRECTORY", "/tmp/telegram-bot-api-temp")),
            temp_directory=Path(os.getenv("TEMP_DIRECTORY", "/tmp/telegram-video-compressor")),
            max_concurrent_jobs=max_jobs,
            max_input_bytes=max_input,
            max_output_bytes=max_output,
            ffmpeg_binary=os.getenv("FFMPEG_BINARY", "ffmpeg").strip(),
            ffprobe_binary=os.getenv("FFPROBE_BINARY", "ffprobe").strip(),
            update_mode=update_mode,
            port=max(1, int(os.getenv("PORT", "10000"))),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            local_bot_api_start_timeout_seconds=max(10, int(os.getenv("LOCAL_BOT_API_START_TIMEOUT_SECONDS", "60"))),
            queue_limit_per_user=queue_limit,
        )


def _required_int(name: str) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        raise ValueError(f"{name} is required")
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _nonnegative_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    value = int(raw)
    if value < 0:
        raise ValueError(f"{name} must be zero or positive")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


settings = None
