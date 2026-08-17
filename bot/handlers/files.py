from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from telegram import InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot.handlers.commands import _private
from bot.keyboards.compression import resolution_keyboard, start_keyboard, target_keyboard
from services.compression_queue import CompressionQueue
from services.ffmpeg_service import FFmpegError, FFmpegService, format_bytes
from services.memory_store import MemoryStore

logger = logging.getLogger(__name__)

_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".3gp", ".ts", ".mpeg", ".mpg"}


def _store(context: ContextTypes.DEFAULT_TYPE) -> MemoryStore:
    return context.application.bot_data["store"]


def _queue(context: ContextTypes.DEFAULT_TYPE) -> CompressionQueue:
    return context.application.bot_data["queue"]


def _ffmpeg(context: ContextTypes.DEFAULT_TYPE) -> FFmpegService:
    return context.application.bot_data["ffmpeg"]


def _settings(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["settings"]


async def receive_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _private(update):
        return
    message = update.effective_message
    user_id = update.effective_user.id
    if _queue(context).is_busy_for_user(user_id):
        await message.reply_text("Your previous video is still queued or processing. Please wait, or use /status.")
        return

    telegram_file = None
    original_name = "video.mp4"
    announced_size = None
    if message.video:
        telegram_file = await context.bot.get_file(message.video.file_id)
        original_name = message.video.file_name or "video.mp4"
        announced_size = message.video.file_size
    elif message.document:
        original_name = message.document.file_name or "video"
        mime = message.document.mime_type or ""
        if not mime.startswith("video/") and Path(original_name).suffix.lower() not in _VIDEO_EXTENSIONS:
            await message.reply_text("Please send a video file, not a general document.")
            return
        telegram_file = await context.bot.get_file(message.document.file_id)
        announced_size = message.document.file_size
    else:
        return

    settings = _settings(context)
    if settings.max_input_bytes and announced_size and announced_size > settings.max_input_bytes:
        await message.reply_text(
            f"This deployment has a configured input safety limit of {format_bytes(settings.max_input_bytes)}."
        )
        return

    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(original_name).name)[:120] or "video.mp4"
    source_path = settings.temp_directory / f"incoming_{user_id}_{int(time.time())}_{safe_name}"
    settings.temp_directory.mkdir(parents=True, exist_ok=True)
    await message.reply_text("Downloading the video to temporary storage and reading its metadata…")
    try:
        await telegram_file.download_to_drive(
            custom_path=source_path,
            read_timeout=3600,
            write_timeout=3600,
            connect_timeout=60,
            pool_timeout=60,
        )
        metadata = await _ffmpeg(context).probe(source_path)
        pending = _store(context).create_pending(
            user_id=user_id,
            chat_id=message.chat_id,
            source_path=source_path,
            original_name=Path(original_name).name,
            metadata=metadata,
        )
        duration = _format_duration(metadata.duration_seconds)
        await message.reply_text(
            f"Video ready.\n"
            f"Input: {format_bytes(metadata.file_size_bytes)}\n"
            f"Source: {metadata.width}×{metadata.height}, {duration}\n\n"
            "Choose the output resolution:",
            reply_markup=resolution_keyboard(pending.token),
        )
    except (FFmpegError, TelegramError, OSError) as exc:
        source_path.unlink(missing_ok=True)
        logger.warning("Could not prepare video from user %s: %s", user_id, exc)
        await message.reply_text(f"Could not prepare this video: {exc}")
    except Exception as exc:
        source_path.unlink(missing_ok=True)
        logger.exception("Unexpected video intake error")
        await message.reply_text(f"Could not prepare this video: {exc}")


async def compression_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not update.effective_user:
        return
    await query.answer()
    parts = (query.data or "").split(":")
    if len(parts) < 3 or parts[0] != "cv":
        return
    token, action = parts[1], parts[2]
    pending = _store(context).get_by_token(token)
    if pending is None or pending.user_id != update.effective_user.id:
        await query.edit_message_text("This compression choice has expired. Please send the video again.")
        return

    if action == "cancel":
        _store(context).clear_pending(pending.user_id)
        pending.source_path.unlink(missing_ok=True)
        await query.edit_message_text("The pending video was cancelled.")
        return
    if action == "back":
        pending.resolution = None
        pending.target_size_mb = None
        await query.edit_message_text("Choose the output resolution:", reply_markup=resolution_keyboard(token))
        return
    if action == "custom":
        _store(context).mark_waiting_custom_size(pending.user_id, True)
        await query.edit_message_text("Send the target size as a whole number of MB, for example: 300")
        return
    if action == "res" and len(parts) >= 4:
        resolution = parts[3]
        if resolution not in {"360", "480", "720", "1080", "original"}:
            return
        _store(context).set_resolution(pending.user_id, resolution)
        _store(context).set_target_size(pending.user_id, None)
        estimate = _ffmpeg(context).estimate_output_text(pending.metadata, resolution)
        label = "original resolution" if resolution == "original" else f"{resolution}p"
        await query.edit_message_text(
            f"Selected: {label}.\nEstimated output: approximately {estimate}.\n\n"
            "You can start with this quality-based estimate, or request a target size:",
            reply_markup=target_keyboard(token),
        )
        return
    if action == "target" and len(parts) >= 4:
        try:
            target_mb = int(parts[3])
        except ValueError:
            return
        if target_mb < 10 or target_mb > 10 * 1024:
            await query.edit_message_text("Choose a target between 10 MB and 10 GB.", reply_markup=target_keyboard(token))
            return
        _store(context).set_target_size(pending.user_id, target_mb)
        _store(context).mark_waiting_custom_size(pending.user_id, False)
        estimate = _ffmpeg(context).estimate_output_text(pending.metadata, pending.resolution or "original", target_mb)
        await query.edit_message_text(
            f"Target size selected: under {target_mb} MB.\n"
            f"The encoder will use approximately {estimate} as its bitrate budget.\n\n"
            "Start when ready:",
            reply_markup=start_keyboard(token),
        )
        return
    if action == "start":
        if not pending.resolution:
            await query.edit_message_text("Please choose a resolution first.", reply_markup=resolution_keyboard(token))
            return
        position = await _queue(context).enqueue(pending)
        _store(context).clear_pending(pending.user_id)
        await query.edit_message_text(f"Queued successfully. Approximate queue position: {position}. Use /status to check progress.")


async def custom_size_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _private(update) or not update.effective_message or not update.effective_user:
        return
    user_id = update.effective_user.id
    store = _store(context)
    if not store.is_waiting_custom_size(user_id):
        return
    pending = store.get_pending(user_id)
    if pending is None:
        store.mark_waiting_custom_size(user_id, False)
        await update.effective_message.reply_text("That pending video has expired. Please send it again.")
        return
    raw = (update.effective_message.text or "").strip().lower().replace("mb", "").strip()
    try:
        target_mb = int(raw)
    except ValueError:
        await update.effective_message.reply_text("Please send only a whole number of MB, such as 300.")
        return
    if target_mb < 10 or target_mb > 10 * 1024:
        await update.effective_message.reply_text("Choose a target between 10 MB and 10 GB.")
        return
    store.mark_waiting_custom_size(user_id, False)
    store.set_target_size(user_id, target_mb)
    estimate = _ffmpeg(context).estimate_output_text(pending.metadata, pending.resolution or "original", target_mb)
    await update.effective_message.reply_text(
        f"Target size selected: under {target_mb} MB.\n"
        f"Estimated bitrate budget: approximately {estimate}.\n\nStart when ready:",
        reply_markup=start_keyboard(pending.token),
    )


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
