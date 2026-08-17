from __future__ import annotations

import asyncio
import logging
import os
import signal

import uvicorn
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters
from telegram.request import HTTPXRequest

from bot.handlers.commands import cancel, help_command, start, status
from bot.handlers.files import compression_callback, custom_size_text, receive_video
from config import Settings
from services.compression_queue import CompressionQueue
from services.ffmpeg_service import FFmpegService
from services.local_bot_api import LocalBotApiServer, log_out_from_cloud_bot_api
from services.memory_store import MemoryStore
from web.app import create_app

logger = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def build_application(settings: Settings, bot_data: dict) -> Application:
    bot_request = HTTPXRequest(
        connection_pool_size=8,
        read_timeout=7200,
        write_timeout=7200,
        connect_timeout=60,
        pool_timeout=120,
        media_write_timeout=7200,
    )
    updates_request = HTTPXRequest(
        connection_pool_size=4,
        read_timeout=70,
        write_timeout=60,
        connect_timeout=60,
        pool_timeout=120,
    )
    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .base_url(f"{settings.telegram_api_base_url}/bot")
        .base_file_url(f"{settings.telegram_api_base_url}/file/bot")
        .local_mode(settings.local_bot_api)
        .request(bot_request)
        .get_updates_request(updates_request)
        .build()
    )
    application.bot_data.update(bot_data)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CallbackQueryHandler(compression_callback))
    application.add_handler(MessageHandler(filters.VIDEO | filters.Document.ALL, receive_video))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, custom_size_text))
    return application


async def run() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    settings.temp_directory.mkdir(parents=True, exist_ok=True)

    local_api = LocalBotApiServer(settings)
    if settings.local_bot_api:
        await log_out_from_cloud_bot_api(settings.telegram_bot_token)
        await local_api.start()

    store = MemoryStore()
    ffmpeg = FFmpegService(settings)
    queue = CompressionQueue(settings, store, ffmpeg)
    bot_data = {"settings": settings, "store": store, "ffmpeg": ffmpeg, "queue": queue}
    application = build_application(settings, bot_data)
    queue.bot = application.bot

    web_server = uvicorn.Server(uvicorn.Config(
        create_app(),
        host=os.getenv("WEB_HOST", "0.0.0.0"),
        port=settings.port,
        log_level=settings.log_level.lower(),
        proxy_headers=True,
    ))
    web_task = asyncio.create_task(web_server.serve(), name="health-web-server")
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signame in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signame, stop_event.set)
        except NotImplementedError:
            pass

    try:
        await application.initialize()
        await application.start()
        await queue.start()
        if application.updater is None:
            raise RuntimeError("Telegram updater is unavailable")
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("Video compressor bot is polling through the Local Bot API")
        await stop_event.wait()
    finally:
        try:
            if application.updater is not None:
                await application.updater.stop()
            await queue.stop()
            await application.stop()
            await application.shutdown()
        finally:
            web_server.should_exit = True
            await web_task
            await local_api.stop()
            logger.info("Bot stopped cleanly")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
