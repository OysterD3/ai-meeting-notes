"""Local MLX Whisper transcription."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any

import numpy as np


DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"


class TranscriptionError(RuntimeError):
    """The recording could not be transcribed."""


def transcribe(
    recording: Path,
    output_dir: Path,
    *,
    model: str = DEFAULT_MODEL,
    language: str | None = None,
) -> Path:
    """Transcribe *recording* locally and return its ``transcript.json`` path."""
    source = Path(recording).expanduser().resolve()
    if not source.is_file():
        raise TranscriptionError(f"Recording does not exist or is not a file: {source}")

    destination = Path(output_dir).expanduser()
    if destination.exists() and not destination.is_dir():
        raise TranscriptionError(f"Output path is not a directory: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    transcript_json = destination / "transcript.json"
    transcript_markdown = destination / "transcript.md"
    _refuse_existing(transcript_json, transcript_markdown)

    with tempfile.TemporaryDirectory(prefix="meeting-notes-") as temporary_dir:
        decoded = Path(temporary_dir) / "audio.wav"
        _decode(source, decoded)
        audio, duration_seconds = _read_audio(decoded)
        if not len(audio):
            raise TranscriptionError("Recording has no audio samples after decoding")
        result = _run_mlx(audio, model, language)

    text, detected_language, segments = _normalise_result(result)
    if not text.strip() or not any(segment["text"].strip() for segment in segments):
        raise TranscriptionError("No speech was recognized in the recording")

    document = {
        "source": str(source),
        "duration_seconds": duration_seconds,
        "language": detected_language,
        "model": model,
        "text": text,
        "segments": segments,
    }
    _write_json(transcript_json, document)
    _write_markdown(transcript_markdown, document)
    return transcript_json


def _refuse_existing(*paths: Path) -> None:
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise TranscriptionError(f"Refusing to overwrite existing output: {', '.join(existing)}")


def _decode(source: Path, decoded: Path) -> None:
    try:
        import imageio_ffmpeg
    except ImportError as error:
        raise TranscriptionError("imageio-ffmpeg is required to decode recordings") from error

    try:
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as error:
        raise TranscriptionError("Could not find the imageio-ffmpeg executable") from error

    command = [
        ffmpeg,
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        str(decoded),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as error:
        raise TranscriptionError(f"Could not run ffmpeg to decode recording: {error}") from error
    if completed.returncode:
        detail = completed.stderr.strip() or "unknown ffmpeg error"
        raise TranscriptionError(f"Could not decode recording: {detail}")


def _read_audio(decoded: Path) -> tuple[Any, float]:
    try:
        with wave.open(str(decoded), "rb") as audio_file:
            if (
                audio_file.getnchannels() != 1
                or audio_file.getframerate() != 16000
                or audio_file.getsampwidth() != 2
                or audio_file.getcomptype() != "NONE"
            ):
                raise TranscriptionError("ffmpeg did not produce 16 kHz mono signed-16-bit WAV audio")
            frame_count = audio_file.getnframes()
            raw_audio = audio_file.readframes(frame_count)
    except (OSError, EOFError, wave.Error) as error:
        raise TranscriptionError(f"Could not read decoded audio: {error}") from error

    waveform = np.frombuffer(raw_audio, dtype="<i2").astype(np.float32) / 32768.0
    return waveform, frame_count / 16000


def _run_mlx(audio: Any, model: str, language: str | None) -> dict[str, Any]:
    try:
        from mlx_whisper import transcribe as mlx_transcribe
    except ImportError as error:
        raise TranscriptionError("mlx-whisper is required for transcription") from error

    try:
        result = mlx_transcribe(
            audio,
            path_or_hf_repo=model,
            task="transcribe",
            language=language,
            verbose=False,
            word_timestamps=True,
            condition_on_previous_text=False,
        )
    except Exception as error:
        raise TranscriptionError(f"MLX Whisper transcription failed: {error}") from error
    if not isinstance(result, dict):
        raise TranscriptionError("MLX Whisper returned an invalid transcription result")
    return result


def _normalise_result(result: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
    text = result.get("text")
    language = result.get("language")
    raw_segments = result.get("segments")
    if not isinstance(text, str) or not isinstance(language, str) or not language:
        raise TranscriptionError("MLX Whisper returned an incomplete transcription result")
    if not isinstance(raw_segments, list):
        raise TranscriptionError("MLX Whisper returned invalid transcript segments")

    segments: list[dict[str, Any]] = []
    for segment in raw_segments:
        if not isinstance(segment, dict):
            raise TranscriptionError("MLX Whisper returned an invalid transcript segment")
        start, end, segment_text = segment.get("start"), segment.get("end"), segment.get("text")
        if (
            not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or not isinstance(segment_text, str)
        ):
            raise TranscriptionError("MLX Whisper returned an invalid transcript segment")
        segments.append({"start": start, "end": end, "text": segment_text})
    return text, language, segments


def _write_json(path: Path, document: dict[str, Any]) -> None:
    _write_atomically(path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")


def _write_markdown(path: Path, document: dict[str, Any]) -> None:
    lines = [
        "# Transcript",
        "",
        f"Source: {document['source']}",
        f"Duration: {document['duration_seconds']:.2f} seconds",
        f"Language: {document['language']}",
        f"Model: {document['model']}",
        "",
    ]
    for segment in document["segments"]:
        lines.append(
            f"[{_timestamp(segment['start'])} - {_timestamp(segment['end'])}] {segment['text'].strip()}"
        )
    if not document["segments"]:
        lines.append(f"[00:00:00] {document['text'].strip()}")
    lines.append("")
    _write_atomically(path, "\n".join(lines))


def _timestamp(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _write_atomically(path: Path, content: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
