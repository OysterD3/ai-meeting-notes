from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "meeting_notes", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


class CLITests(unittest.TestCase):
    def test_help(self) -> None:
        result = run_cli("--help")

        self.assertEqual(result.returncode, 0)
        self.assertIn("transcribe", result.stdout)
        self.assertIn("notes", result.stdout)
        self.assertIn("run", result.stdout)

    def test_transcribe_rejects_missing_recording_before_loading_asr(self) -> None:
        result = run_cli("transcribe", "missing-recording.mp4")

        self.assertEqual(result.returncode, 2)
        self.assertIn("recording does not exist", result.stderr)

    def test_run_rejects_existing_notes_before_transcription(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recording = root / "meeting.mp4"
            output = root / "result"
            recording.touch()
            output.mkdir()
            (output / "notes.md").write_text("existing notes", encoding="utf-8")

            result = run_cli("run", str(recording), "--output", str(output))

        self.assertEqual(result.returncode, 2)
        self.assertIn("refusing to overwrite existing artifact", result.stderr)
        self.assertIn("notes.md", result.stderr)


if __name__ == "__main__":
    unittest.main()
