from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from services.compression_queue import CompressionQueue
from services.memory_store import MemoryStore


def _store(context: ContextTypes.DEFAULT_TYPE) -> MemoryStore:
    return context.application.bot_data["store"]


def _queue(context: ContextTypes.DEFAULT_TYPE) -> CompressionQueue:
    return context.application.bot_data["queue"]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _private(update):
        return
    await update.effective_message.reply_text(
        "Send me a video as a Telegram video or document. I will inspect it, show resolution and target-size choices, "
        "then return a compressed MP4.\n\nNo sign-in or database is needed. Use /help for commands."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _private(update):
        return
    await update.effective_message.reply_text(
        "How it works:\n"
        "1. Send a video.\n"
        "2. Choose 360p, 480p, 720p, 1080p, or keep the original resolution.\n"
        "3. Review the estimated output size, or enter an optional custom target size in MB.\n"
        "4. Start compression and watch the live compression and Telegram upload percentages.\n\n"
        "/status shows queue information.\n"
        "/cancel removes your pending upload.\n\n"
        "The bot keeps temporary files only while processing; sessions are in memory and can reset after a restart."
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _private(update):
        return
    user_id = update.effective_user.id
    queue = _queue(context)
    store = _store(context)
    pending = store.get_pending(user_id)
    if queue.is_busy_for_user(user_id):
        if queue.is_active_for_user(user_id):
            progress = store.progress(user_id)
            if progress:
                stage, percent = progress
                if stage == "upload":
                    label = "Uploading to Telegram"
                    text = f"{label}: {percent:.0f}%"
                elif stage == "telegram":
                    text = "Sending through Local Bot API… Telegram is processing the file."
                else:
                    text = f"Compressing: {percent:.0f}%"
                await update.effective_message.reply_text(text)
            else:
                await update.effective_message.reply_text("Your video is currently being processed.")
        else:
            position = store.queue_position(user_id)
            await update.effective_message.reply_text(f"Your compression job is queued. Approximate position: {position or 1}.")
    elif pending:
        await update.effective_message.reply_text("Your video is waiting for a resolution choice.")
    else:
        await update.effective_message.reply_text("You do not have a pending or active compression job.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _private(update):
        return
    user_id = update.effective_user.id
    pending = _store(context).clear_pending(user_id)
    if pending:
        pending.source_path.unlink(missing_ok=True)
        await update.effective_message.reply_text("Your pending video and choices were cancelled.")
    elif _queue(context).is_busy_for_user(user_id):
        await update.effective_message.reply_text("Your video is already being compressed. It cannot be cancelled safely now; please wait for the result.")
    else:
        await update.effective_message.reply_text("There is no pending compression job to cancel.")


def _private(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.type == "private" and update.effective_message)
