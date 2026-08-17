def test_runtime_modules_import():
    import main  # noqa: F401
    from bot.handlers import commands, files  # noqa: F401
    from bot.keyboards import compression  # noqa: F401
    from bot.middleware import auth  # noqa: F401
    from services import compression_queue, ffmpeg_service, local_bot_api, memory_store  # noqa: F401
    from web import app  # noqa: F401
