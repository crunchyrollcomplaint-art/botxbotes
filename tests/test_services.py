from pathlib import Path
from types import SimpleNamespace

from services.ffmpeg_service import FFmpegService, RESOLUTION_HEIGHTS, format_bytes
from services.memory_store import MemoryStore, VideoMetadata


def service() -> FFmpegService:
    return FFmpegService(SimpleNamespace(ffmpeg_binary="ffmpeg", ffprobe_binary="ffprobe"))


def metadata() -> VideoMetadata:
    return VideoMetadata(
        duration_seconds=600,
        width=1920,
        height=1080,
        video_bitrate=8_000_000,
        audio_bitrate=192_000,
        format_name="mp4",
        file_size_bytes=600_000_000,
    )


def test_supported_resolutions_are_present():
    assert set(("360", "480", "720", "1080", "original")) <= set(RESOLUTION_HEIGHTS)


def test_quality_estimate_is_positive_and_lower_for_360p():
    ffmpeg = service()
    estimate_360 = ffmpeg.estimate_output_bytes(metadata(), "360")
    estimate_1080 = ffmpeg.estimate_output_bytes(metadata(), "1080")
    assert estimate_360 > 0
    assert estimate_360 < estimate_1080


def test_target_size_estimate_uses_requested_mb():
    ffmpeg = service()
    assert ffmpeg.estimate_output_bytes(metadata(), "720", 500) == 500 * 1024 * 1024


def test_format_bytes_is_human_readable():
    assert format_bytes(1024 * 1024) == "1.0 MB"


def test_store_keeps_one_pending_video_per_user(tmp_path: Path):
    store = MemoryStore()
    first = store.create_pending(
        user_id=7,
        chat_id=7,
        source_path=tmp_path / "first.mp4",
        original_name="first.mp4",
        metadata=metadata(),
    )
    second = store.create_pending(
        user_id=7,
        chat_id=7,
        source_path=tmp_path / "second.mp4",
        original_name="second.mp4",
        metadata=metadata(),
    )
    assert store.get_pending(7) is second
    assert store.get_by_token(first.token) is None
    assert store.get_by_token(second.token) is second


def test_store_custom_size_and_queue_state(tmp_path: Path):
    store = MemoryStore()
    pending = store.create_pending(
        user_id=8,
        chat_id=8,
        source_path=tmp_path / "video.mp4",
        original_name="video.mp4",
        metadata=metadata(),
    )
    store.set_resolution(8, "720")
    store.set_target_size(8, 200)
    store.mark_waiting_custom_size(8)
    store.mark_queued(8, 2)
    assert pending.resolution == "720"
    assert pending.target_size_mb == 200
    assert store.is_waiting_custom_size(8)
    assert store.queue_position(8) == 2


def test_store_tracks_live_progress():
    store = MemoryStore()
    store.set_progress(9, "upload", 31.5)
    assert store.progress(9) == ("upload", 31.5)
    store.clear_progress(9)
    assert store.progress(9) is None


def test_target_keyboard_keeps_only_custom_size_option():
    from bot.keyboards.compression import target_keyboard

    buttons = [button for row in target_keyboard("token").inline_keyboard for button in row]
    labels = {button.text for button in buttons}
    assert "Custom target size (MB)" in labels
    assert "Under 200 MB" not in labels
    assert "Under 500 MB" not in labels
    assert "Under 1 GB" not in labels
