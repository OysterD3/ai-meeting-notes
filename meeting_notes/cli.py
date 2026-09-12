"""Command-line interface for meeting-notes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


class CLIError(Exception):
    """An error caused by command-line input or an existing artifact."""


def _artifact_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _recording_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_file():
        raise CLIError(f"recording does not exist or is not a file: {path}")
    return path.resolve()


def _transcript_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_file():
        raise CLIError(f"transcript does not exist or is not a file: {path}")
    return path.resolve()


def _output_dir(recording: Path, value: str | None) -> Path:
    return (Path(value).expanduser() if value else Path("output") / recording.stem).resolve()


def _check_transcript_output(output_dir: Path) -> None:
    if output_dir.exists() and not output_dir.is_dir():
        raise CLIError(f"output path is not a directory: {output_dir}")
    for name in ("transcript.json", "transcript.md"):
        path = output_dir / name
        if _artifact_exists(path):
            raise CLIError(f"refusing to overwrite existing artifact: {path}")


def _check_notes_output(path: Path) -> None:
    if _artifact_exists(path):
        raise CLIError(f"refusing to overwrite existing artifact: {path}")


def _transcribe(recording: Path, output_dir: Path, model: str | None, language: str | None) -> Path:
    print(f"Transcribing {recording}...", file=sys.stderr)
    from .transcribe import transcribe

    options: dict[str, str] = {}
    if model:
        options["model"] = model
    if language:
        options["language"] = language
    transcript = transcribe(recording, output_dir, **options)
    print(f"Transcript JSON: {transcript}")
    print(f"Transcript Markdown: {transcript.with_suffix('.md')}")
    return transcript


def _notes(transcript: Path, output: Path, model: str | None) -> Path:
    print(f"Generating notes from {transcript}...", file=sys.stderr)
    from .notes import generate_notes

    options: dict[str, str] = {}
    if model:
        options["model"] = model
    notes = generate_notes(transcript, output, **options)
    print(f"Notes: {notes}")
    return notes


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="meeting-notes", description="Transcribe a recording and create meeting notes.")
    commands = parser.add_subparsers(dest="command", required=True)

    transcribe = commands.add_parser("transcribe", help="create a local transcript")
    transcribe.add_argument("recording", metavar="RECORDING")
    transcribe.add_argument("--output", metavar="DIR")
    transcribe.add_argument("--model", metavar="HF_ASR_ID")
    transcribe.add_argument("--language", metavar="CODE")

    notes = commands.add_parser("notes", help="create notes from a transcript JSON file")
    notes.add_argument("transcript", metavar="TRANSCRIPT_JSON")
    notes.add_argument("--output", metavar="FILE")
    notes.add_argument("--pi-model", dest="pi_model", metavar="PROVIDER/MODEL")

    run = commands.add_parser("run", help="transcribe, then create notes")
    run.add_argument("recording", metavar="RECORDING")
    run.add_argument("--output", metavar="DIR")
    run.add_argument("--model", metavar="HF_ASR_ID")
    run.add_argument("--language", metavar="CODE")
    run.add_argument("--pi-model", dest="pi_model", metavar="PROVIDER/MODEL")
    return parser


def _run(args: argparse.Namespace) -> int:
    if args.command == "transcribe":
        recording = _recording_path(args.recording)
        output_dir = _output_dir(recording, args.output)
        _check_transcript_output(output_dir)
        _transcribe(recording, output_dir, args.model, args.language)
        return 0

    if args.command == "notes":
        transcript = _transcript_path(args.transcript)
        output = Path(args.output).expanduser().resolve() if args.output else transcript.with_name("notes.md")
        _check_notes_output(output)
        _notes(transcript, output, args.pi_model)
        return 0

    recording = _recording_path(args.recording)
    output_dir = _output_dir(recording, args.output)
    notes_output = output_dir / "notes.md"
    _check_transcript_output(output_dir)
    _check_notes_output(notes_output)
    transcript = _transcribe(recording, output_dir, args.model, args.language)
    _notes(transcript, notes_output, args.pi_model)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the command-line interface."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except CLIError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except ImportError as error:
        print(f"error: required dependency is unavailable: {error}. Run `uv sync --python 3.12`.", file=sys.stderr)
        return 1
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
