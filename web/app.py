from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Telegram Video Compressor")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "telegram-video-compressor"}

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": "telegram-video-compressor", "health": "/health"}

    return app
