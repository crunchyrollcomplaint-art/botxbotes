from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def resolution_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("360p", callback_data=f"cv:{token}:res:360"),
            InlineKeyboardButton("480p", callback_data=f"cv:{token}:res:480"),
        ],
        [
            InlineKeyboardButton("720p", callback_data=f"cv:{token}:res:720"),
            InlineKeyboardButton("1080p", callback_data=f"cv:{token}:res:1080"),
        ],
        [InlineKeyboardButton("Keep original resolution", callback_data=f"cv:{token}:res:original")],
        [InlineKeyboardButton("Cancel", callback_data=f"cv:{token}:cancel")],
    ])


def target_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Start with this quality estimate", callback_data=f"cv:{token}:start")],
        [InlineKeyboardButton("Custom target size (MB)", callback_data=f"cv:{token}:custom")],
        [InlineKeyboardButton("Back to resolutions", callback_data=f"cv:{token}:back")],
        [InlineKeyboardButton("Cancel", callback_data=f"cv:{token}:cancel")],
    ])


def start_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Start compression", callback_data=f"cv:{token}:start")],
        [InlineKeyboardButton("Choose another resolution", callback_data=f"cv:{token}:back")],
        [InlineKeyboardButton("Cancel", callback_data=f"cv:{token}:cancel")],
    ])
