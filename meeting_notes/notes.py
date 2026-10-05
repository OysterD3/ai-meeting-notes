"""Create meeting notes and an infographic through Pi or Claude Code."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import math
from pathlib import Path
import sys
import tempfile
from typing import Any

from . import engines, prompts
from .frames import FRAME_INTERVAL_SECONDS, extract_frames, timestamp
from .infographic import SCHEMA as INFOGRAPHIC_SCHEMA, find_chrome, render_html, render_png


_WINDOW_SECONDS = 420
_CONTEXT_SECONDS = 30
# Whisper keeps only the last 223 prompt tokens; one CJK character can take two tokens.
_MAX_GLOSSARY_CHARACTERS = 150
_PARALLEL_REQUESTS = 4

_GLOSSARY_SCHEMA = {
    "type": "object",
    "properties": {"terms": {"type": "array", "items": {"type": "string"}}},
    "required": ["terms"],
}
_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "time_ranges": {"type": "string"},
                    "points": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "claim": {"type": "string"},
                                "timestamps": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": ["decided", "agreed-direction", "proposal", "current-state", "question", "action", "unclear"],
                                },
                                "speaker": {"type": "string", "description": "a name only when the audio makes it clear, else unknown"},
                                "evidence": {"type": "string"},
                            },
                            "required": ["claim", "timestamps", "status", "speaker", "evidence"],
                        },
                    },
                },
                "required": ["title", "time_ranges", "points"],
            },
        },
        "corrections": {"type": "array", "items": {"type": "string"}},
        "screen_findings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["topics", "corrections", "screen_findings"],
}
_MARKDOWN_SCHEMA = {
    "type": "object",
    "properties": {"markdown": {"type": "string"}},
    "required": ["markdown"],
}
_VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "problem": {"type": "string"},
                    "evidence": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["location", "problem", "evidence", "fix"],
            },
        }
    },
    "required": ["issues"],
}
_FIX_SCHEMA = {
    "type": "object",
    "properties": {"markdown": {"type": "string"}, "changes": {"type": "array", "items": {"type": "string"}}},
    "required": ["markdown", "changes"],
}


def artifact_paths(notes_path: Path) -> tuple[Path, Path, Path]:
    """Return the notes, infographic HTML, and infographic PNG paths for *notes_path*."""
    notes_path = Path(notes_path)
    stem = "meeting" if notes_path.name == "notes.md" else notes_path.stem
    return (
        notes_path,
        notes_path.with_name(f"{stem}-infographic.html"),
        notes_path.with_name(f"{stem}-infographic.png"),
    )


def preflight(engine: str) -> None:
    """Check the external programs before any slow work starts."""
    engines.check_engine(engine)
    find_chrome()


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"transcript {field} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"transcript {field} must be finite")
    return value


def _load_transcript(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read transcript JSON: {error}") from error

    if not isinstance(data, dict):
        raise ValueError("transcript must be a JSON object")
    source = data.get("source")
    if not isinstance(source, str) or not Path(source).is_absolute():
        raise ValueError("transcript source must be an absolute path")
    duration = _number(data.get("duration_seconds"), "duration_seconds")
    if duration < 0:
        raise ValueError("transcript duration_seconds must not be negative")
    for field in ("language", "model", "text"):
        if not isinstance(data.get(field), str):
            raise ValueError(f"transcript {field} must be text")
    if not data["language"].strip() or not data["model"].strip() or not data["text"].strip():
        raise ValueError("transcript language, model, and text must not be empty")

    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("transcript must contain segments")
    previous_start = -1.0
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise ValueError(f"transcript segment {index} must be an object")
        start = _number(segment.get("start"), f"segment {index} start")
        end = _number(segment.get("end"), f"segment {index} end")
        text = segment.get("text")
        if start < 0 or end < start or start < previous_start:
            raise ValueError(f"transcript segment {index} has invalid timestamps")
        if end > duration + 1:
            raise ValueError(f"transcript segment {index} exceeds the duration")
        if not isinstance(text, str):
            raise ValueError(f"transcript segment {index} text must be a string")
        previous_start = start
    # Whisper can emit empty timestamp-only segments between spoken segments.
    data["segments"] = [segment for segment in segments if segment["text"].strip()]
    if not data["segments"]:
        raise ValueError("transcript has no spoken segments")
    return data


def _lines(segments: list[dict[str, Any]], start: float = 0, end: float = math.inf) -> str:
    return "\n".join(
        f"[{timestamp(segment['start'])} - {timestamp(segment['end'])}] {segment['text'].strip()}"
        for segment in segments
        if segment["end"] >= start and segment["start"] <= end
    )


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def generate_notes(
    transcript_path: Path, output_path: Path, *, engine: str = "pi", model: str | None = None
) -> Path:
    """Create notes.md, the infographic HTML, and the infographic PNG from a transcript JSON file."""
    from .transcribe import retranscribe

    transcript_path = Path(transcript_path)
    outputs = artifact_paths(Path(output_path))
    for path in outputs:
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"refusing to overwrite {path}")
    if outputs[0].parent.exists() and not outputs[0].parent.is_dir():
        raise FileExistsError(f"output parent is not a directory: {outputs[0].parent}")
    transcript = _load_transcript(transcript_path)
    recording = Path(transcript["source"])
    if not recording.is_file():
        raise RuntimeError(
            f"recording not found: {recording}. The notes step needs the recording for the second "
            "transcription pass and the screen checks."
        )
    preflight(engine)
    chrome = find_chrome()
    pass1 = transcript["segments"]
    duration = transcript["duration_seconds"]

    def ask(system: str, text: str, schema: dict[str, Any], images: list[tuple[str, Path]] | None = None) -> dict[str, Any]:
        return engines.ask(engine, system=system, text=text, schema=schema, images=images, model=model, cwd=work)

    with tempfile.TemporaryDirectory(prefix="meeting-notes-") as temporary:
        work = Path(temporary)

        _log("Finding domain terms for the second transcription pass...")
        terms = [term.strip() for term in ask(prompts.GLOSSARY, _lines(pass1), _GLOSSARY_SCHEMA)["terms"] if isinstance(term, str) and term.strip()]
        kept: list[str] = []
        for term in terms:
            if len("、".join([*kept, term])) > _MAX_GLOSSARY_CHARACTERS:
                break
            kept.append(term)
        vocabulary = "、".join(kept)

        _log("Transcribing the recording again with the vocabulary prompt...")
        pass2 = retranscribe(
            recording, model=transcript["model"], language=transcript["language"], initial_prompt=vocabulary, segments=pass1
        )

        _log("Extracting screen frames...")
        frames = extract_frames(recording, duration, work / "frames")

        windows = []
        start = 0.0
        while start < duration:
            windows.append((start, min(duration, start + _WINDOW_SECONDS)))
            start += _WINDOW_SECONDS

        def extract(window: tuple[float, float]) -> dict[str, Any]:
            begin, end = window
            text = (
                f"Window: {timestamp(begin)}–{timestamp(end)}\n\n"
                f"Pass 1 transcript:\n{_lines(pass1, begin - _CONTEXT_SECONDS, end + _CONTEXT_SECONDS)}\n\n"
                f"Pass 2 transcript:\n{_lines(pass2, begin - _CONTEXT_SECONDS, end + _CONTEXT_SECONDS)}"
            )
            images = [(f"Frame at {timestamp(seconds)}", path) for seconds, path in frames if begin <= seconds < end]
            return ask(prompts.EXTRACT, text, _EXTRACT_SCHEMA, images)

        _log(f"Extracting discussion points from {len(windows)} windows...")
        with ThreadPoolExecutor(_PARALLEL_REQUESTS) as pool:
            extracts = list(pool.map(extract, windows))
        screen_findings = [finding for result in extracts for finding in result.get("screen_findings", [])]

        method = [
            f"Pass 1: full local transcription with {transcript['model']}.",
            f"Pass 2: a second full transcription with a vocabulary prompt ({len(kept)} terms), used as a cross-check.",
            (
                f"Screen checks: one frame each {FRAME_INTERVAL_SECONDS} seconds ({len(frames)} frames) was read."
                if frames
                else "Screen checks: none, because the recording has no video track."
            ),
            "transcript.md and transcript.json are kept unchanged.",
        ]
        modified = datetime.fromtimestamp(recording.stat().st_mtime).strftime("%Y-%m-%d")
        facts = (
            f"Recording file name: {recording.name}\n"
            f"Recording file modified: {modified}\n"
            f"Duration: {timestamp(duration)} ({duration:.2f} seconds)\n"
            f"Spoken language code: {transcript['language']}\n"
            "Method facts:\n" + "\n".join(f"- {line}" for line in method)
        )
        evidence = (
            f"Pass 1 transcript:\n{_lines(pass1)}\n\nPass 2 transcript:\n{_lines(pass2)}\n\n"
            "Screen findings:\n" + "\n".join(f"- {finding}" for finding in screen_findings)
        )

        _log("Writing the notes draft...")
        draft = ask(
            prompts.DRAFT,
            f"{facts}\n\nPer-window extractions (JSON):\n{json.dumps(extracts, ensure_ascii=False)}",
            _MARKDOWN_SCHEMA,
        )["markdown"]

        _log("Reviewing the draft...")

        def verify(lens: str) -> list[dict[str, Any]]:
            system = prompts.VERIFY.replace("{lens}", prompts.VERIFY_LENSES[lens])
            return ask(system, f"{facts}\n\nNotes:\n{draft}\n\n{evidence}", _VERIFY_SCHEMA)["issues"]

        with ThreadPoolExecutor(_PARALLEL_REQUESTS) as pool:
            issues = [issue for found in pool.map(verify, prompts.VERIFY_LENSES) for issue in found]

        markdown = draft
        if issues:
            _log(f"Fixing {len(issues)} review issues...")
            markdown = ask(
                prompts.FIX,
                f"{facts}\n\nNotes:\n{draft}\n\nReviewer issues (JSON):\n{json.dumps(issues, ensure_ascii=False)}\n\n{evidence}",
                _FIX_SCHEMA,
            )["markdown"]
        markdown = markdown.strip() + "\n"

        _log("Creating the infographic...")
        poster = ask(prompts.INFOGRAPHIC, f"{facts}\n\nFinal notes:\n{markdown}", INFOGRAPHIC_SCHEMA)
        notes_path, html_path, png_path = outputs
        page = render_html(poster, language=transcript["language"], notes_name=notes_path.name)
        work_html = work / html_path.name
        work_html.write_text(page, encoding="utf-8")
        work_png = work / png_path.name
        render_png(chrome, work_html, work_png)

        notes_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with notes_path.open("x", encoding="utf-8") as output:
                output.write(markdown)
            with html_path.open("x", encoding="utf-8") as output:
                output.write(page)
            with png_path.open("xb") as output:
                output.write(work_png.read_bytes())
        except FileExistsError as error:
            raise FileExistsError(f"refusing to overwrite {error.filename}") from None
    return notes_path
