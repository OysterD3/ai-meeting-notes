"""Extract screen frames from a video recording for visual checks."""

from __future__ import annotations

from pathlib import Path
import subprocess


FRAME_INTERVAL_SECONDS = 20
_FRAME_WIDTH = 1440


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
    except ImportError as error:
        raise RuntimeError("imageio-ffmpeg is required to extract frames") from error
    return imageio_ffmpeg.get_ffmpeg_exe()


def timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def has_video(recording: Path) -> bool:
    probe = subprocess.run([_ffmpeg(), "-nostdin", "-i", str(recording)], capture_output=True, text=True, check=False)
    # ffmpeg exits with an error when no output is given, but it still prints the input streams.
    return any("Stream #" in line and "Video:" in line and "attached pic" not in line for line in probe.stderr.splitlines())


def extract_frames(recording: Path, duration: float, directory: Path) -> list[tuple[float, Path]]:
    """Save one JPEG frame each 20 seconds, at 10 s, 30 s, 50 s, and so on.

    Return (seconds, path) pairs. Return an empty list for audio-only recordings.
    """
    if not has_video(recording):
        return []
    directory.mkdir(parents=True, exist_ok=True)
    ffmpeg = _ffmpeg()
    frames: list[tuple[float, Path]] = []
    seconds = FRAME_INTERVAL_SECONDS / 2
    while seconds < duration:
        path = directory / f"frame-{timestamp(seconds).replace(':', '-')}.jpg"
        command = [
            ffmpeg, "-nostdin", "-v", "error", "-ss", f"{seconds:.3f}", "-i", str(recording),
            "-frames:v", "1", "-vf", f"scale={_FRAME_WIDTH}:-2", "-q:v", "3", "-y", str(path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode:
            raise RuntimeError(f"Could not extract a frame at {timestamp(seconds)}: {completed.stderr.strip()}")
        if path.is_file():
            frames.append((seconds, path))
        seconds += FRAME_INTERVAL_SECONDS
    return frames
