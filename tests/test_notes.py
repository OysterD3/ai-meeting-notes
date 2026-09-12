from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from meeting_notes.notes import generate_notes


_STUB_PI = """#!/usr/bin/env python3
import json
import os
import sys

if os.environ.get("PI_STUB_CAPTURE"):
    with open(os.environ["PI_STUB_CAPTURE"], "w", encoding="utf-8") as capture:
        json.dump({"argv": sys.argv[1:], "stdin": sys.stdin.read()}, capture, ensure_ascii=False)
else:
    sys.stdin.read()
mode = os.environ.get("PI_STUB_MODE", "success")
if mode == "incomplete":
    print(json.dumps({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "partial"}}))
elif mode == "error":
    print(json.dumps({"type": "message_end", "message": {"role": "assistant", "stopReason": "error", "content": []}}))
else:
    print(json.dumps({"type": "message_update", "assistantMessageEvent": {"type": "thinking_delta", "delta": "ignore"}}))
    print(json.dumps({"type": "message_end", "message": {"role": "assistant", "stopReason": "stop", "content": [{"type": "thinking", "thinking": "ignore"}, {"type": "text", "text": "# Notas\\nAção:\\u2028 revisar café [00:00:01]"}] }}, ensure_ascii=False))
"""


def _transcript(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "source": str(path.with_name("gravação.m4a").resolve()),
                "duration_seconds": 5.0,
                "language": "pt",
                "model": "mlx-community/whisper-large-v3-turbo",
                "text": "Olá, revisar café.",
                "segments": [
                    {"start": 0.0, "end": 0.0, "text": ""},
                    {"start": 0.0, "end": 2.0, "text": "Olá, revisar café."},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class GenerateNotesTests(unittest.TestCase):
    def test_uses_pi_json_message_end_and_unicode_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transcript = root / "transcript.json"
            output = root / "notes.md"
            capture = root / "capture.json"
            _transcript(transcript)
            pi = root / "pi"
            pi.write_text(textwrap.dedent(_STUB_PI), encoding="utf-8")
            pi.chmod(pi.stat().st_mode | stat.S_IXUSR)
            environment = {
                "PATH": f"{root}{os.pathsep}{os.environ['PATH']}",
                "PI_STUB_CAPTURE": str(capture),
                "PI_PROVIDER": "test-provider",
                "PI_MODEL": "test-model",
            }
            with patch.dict(os.environ, environment, clear=False):
                self.assertEqual(generate_notes(transcript, output), output)

            self.assertEqual(output.read_text(encoding="utf-8"), "# Notas\nAção:\u2028 revisar café [00:00:01]\n")
            request = json.loads(capture.read_text(encoding="utf-8"))
            self.assertEqual(json.loads(request["stdin"])["text"], "Olá, revisar café.")
            self.assertEqual(len(json.loads(request["stdin"])["segments"]), 1)
            self.assertIn("--mode", request["argv"])
            self.assertIn("json", request["argv"])
            self.assertIn("--no-tools", request["argv"])
            self.assertIn("--provider", request["argv"])
            self.assertIn("test-provider", request["argv"])
            self.assertIn("--model", request["argv"])
            self.assertIn("test-model", request["argv"])

    def test_invalid_output_parent_does_not_invoke_pi(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transcript = root / "transcript.json"
            _transcript(transcript)
            parent = root / "not-a-directory"
            parent.write_text("This is a file.", encoding="utf-8")
            capture = root / "capture.json"
            pi = root / "pi"
            pi.write_text(textwrap.dedent(_STUB_PI), encoding="utf-8")
            pi.chmod(pi.stat().st_mode | stat.S_IXUSR)
            environment = {
                "PATH": f"{root}{os.pathsep}{os.environ['PATH']}",
                "PI_STUB_CAPTURE": str(capture),
                "PI_STUB_MODE": "success",
            }
            with patch.dict(os.environ, environment, clear=False):
                with self.assertRaises(FileExistsError):
                    generate_notes(transcript, parent / "notes.md")
            self.assertFalse(capture.exists(), "Pi must not run for an invalid output parent")

    def test_rejects_incomplete_or_error_events_without_writing_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transcript = root / "transcript.json"
            _transcript(transcript)
            pi = root / "pi"
            pi.write_text(textwrap.dedent(_STUB_PI), encoding="utf-8")
            pi.chmod(pi.stat().st_mode | stat.S_IXUSR)
            environment = {"PATH": f"{root}{os.pathsep}{os.environ['PATH']}"}
            for mode in ("incomplete", "error"):
                with self.subTest(mode=mode), patch.dict(
                    os.environ, {**environment, "PI_STUB_MODE": mode}, clear=False
                ):
                    output = root / f"{mode}.md"
                    with self.assertRaises(RuntimeError):
                        generate_notes(transcript, output)
                    self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
