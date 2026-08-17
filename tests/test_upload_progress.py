import asyncio
from io import BytesIO

from httpx._multipart import MultipartStream

from services.upload_progress import ProgressFile


def test_progress_file_reports_upload_percentage():
    async def scenario() -> None:
        reports: list[float] = []

        async def callback(percent: float) -> None:
            reports.append(percent)

        raw = BytesIO(b"0123456789")
        tracked = ProgressFile(raw, 10, callback)
        stream = MultipartStream(data={}, files={"document": ("video.mp4", tracked, "video/mp4")})
        body = b"".join(stream)
        await tracked.wait_for_callbacks()
        assert b"0123456789" in body
        assert reports
        assert reports[-1] == 100.0

    asyncio.run(scenario())
