from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import httpx

from config import Settings

logger = logging.getLogger(__name__)


class LocalBotApiError(RuntimeError):
    pass


class LocalBotApiServer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.process: asyncio.subprocess.Process | None = None
        self._log_task: asyncio.Task | None = None

    async def start(self) -> None:
        if not self.settings.local_bot_api:
            logger.info("Local Bot API disabled; using %s", self.settings.telegram_api_base_url)
            return

        self.settings.local_bot_api_data_directory.mkdir(parents=True, exist_ok=True)
        self.settings.local_bot_api_temp_directory.mkdir(parents=True, exist_ok=True)
        command = [
            self.settings.local_bot_api_binary,
            "--api-id", str(self.settings.telegram_api_id),
            "--api-hash", self.settings.telegram_api_hash,
            "--local",
            "--http-port", str(self.settings.local_bot_api_port),
            "--dir", str(self.settings.local_bot_api_data_directory),
            "--temp-dir", str(self.settings.local_bot_api_temp_directory),
        ]
        logger.info("Starting Local Bot API server on 127.0.0.1:%s", self.settings.local_bot_api_port)
        try:
            self.process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError as exc:
            raise LocalBotApiError(f"Local Bot API binary not found: {self.settings.local_bot_api_binary}") from exc
        self._log_task = asyncio.create_task(self._forward_logs(), name="local-bot-api-logs")
        try:
            await self._wait_until_ready()
        except Exception:
            await self.stop()
            raise

    async def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + self.settings.local_bot_api_start_timeout_seconds
        url = f"{self.settings.telegram_api_base_url}/bot{self.settings.telegram_bot_token}/getMe"
        async with httpx.AsyncClient(timeout=2.0) as client:
            while time.monotonic() < deadline:
                if self.process is not None and self.process.returncode is not None:
                    raise LocalBotApiError(f"Local Bot API stopped during startup: {self.process.returncode}")
                try:
                    response = await client.get(url)
                    if response.status_code == 200 and response.json().get("ok"):
                        logger.info("Local Telegram Bot API server is ready")
                        return
                except (httpx.HTTPError, ValueError):
                    pass
                await asyncio.sleep(0.5)
        raise LocalBotApiError("Local Bot API did not become ready before the startup timeout")

    async def _forward_logs(self) -> None:
        if self.process is None or self.process.stdout is None:
            return
        while True:
            line = await self.process.stdout.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                logger.info("local-bot-api: %s", text)

    async def stop(self) -> None:
        process = self.process
        self.process = None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        if self._log_task is not None:
            self._log_task.cancel()
            await asyncio.gather(self._log_task, return_exceptions=True)
            self._log_task = None


async def log_out_from_cloud_bot_api(token: str) -> None:
    """Release cloud polling before the same bot token is used in local mode."""
    url = f"https://api.telegram.org/bot{token}/logOut"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(url)
            payload = response.json()
        if not payload.get("ok"):
            logger.warning("Cloud Bot API logOut: %s", payload.get("description", "unknown error"))
    except (httpx.HTTPError, ValueError) as exc:
        raise LocalBotApiError("Could not release the bot from Telegram Cloud API") from exc
