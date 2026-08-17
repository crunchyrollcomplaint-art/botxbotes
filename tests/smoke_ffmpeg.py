from pathlib import Path
from types import SimpleNamespace
import asyncio

from services.ffmpeg_service import FFmpegService


async def main() -> None:
    root = Path('/tmp/telegram-compressor-smoke')
    root.mkdir(parents=True, exist_ok=True)
    source = root / 'source.mp4'
    output = root / 'output.mp4'
    target_output = root / 'target_output.mp4'
    settings = SimpleNamespace(ffmpeg_binary='ffmpeg', ffprobe_binary='ffprobe')
    service = FFmpegService(settings)
    create = await asyncio.create_subprocess_exec(
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'testsrc=size=640x360:rate=24', '-t', '2',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source),
    )
    assert await create.wait() == 0
    metadata = await service.probe(source)
    assert metadata.width == 640
    assert metadata.height == 360
    await service.compress(source, output, metadata, '360')
    assert output.exists() and output.stat().st_size > 0
    await service.compress(source, target_output, metadata, '360', target_size_mb=1)
    assert target_output.exists() and target_output.stat().st_size > 0
    print(f'smoke-ok input={metadata.file_size_bytes} output={output.stat().st_size} target={target_output.stat().st_size}')


if __name__ == '__main__':
    asyncio.run(main())
