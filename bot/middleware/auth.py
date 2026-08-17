from __future__ import annotations

from telegram import Update


def is_private_user_update(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type == "private" and update.effective_user)
