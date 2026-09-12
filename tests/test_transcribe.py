import importlib.util
import json
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from meeting_notes.transcribe import TranscriptionError, transcribe


@unittest.skipUnless(importlib.util.find_spec("imageio_ffmpeg"), "imageio-ffmpeg is not installed")
class TranscribeTests(unittest.TestCase):
    def _recording(self, directory: Path) -> Path:
        recording = directory / "sample.wav"
        with wave.open(str(recording), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\x00\x00\x10\x00" * 400)
        return recording

    def test_decodes_writes_slim_transcript_and_passes_audio_to_mlx(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            received = {}

            def fake_transcribe(audio, **kwargs):
                received["samples"] = len(audio)
                received["kwargs"] = kwargs
                return {
                    "text": "Hello meeting.",
                    "language": "en",
                    "segments": [{"start": 0.0, "end": 0.1, "text": "Hello meeting."}],
                }

            mlx_whisper = types.ModuleType("mlx_whisper")
            setattr(mlx_whisper, "transcribe", fake_transcribe)
            with patch.dict(sys.modules, {"mlx_whisper": mlx_whisper}):
                transcript = transcribe(self._recording(directory), directory / "output")

            document = json.loads(transcript.read_text(encoding="utf-8"))
            self.assertEqual(set(document), {"source", "duration_seconds", "language", "model", "text", "segments"})
            self.assertEqual(document["text"], "Hello meeting.")
            self.assertEqual(received["samples"], 1600)
            self.assertEqual(received["kwargs"]["task"], "transcribe")
            self.assertFalse(received["kwargs"]["condition_on_previous_text"])
            self.assertTrue(received["kwargs"]["word_timestamps"])
            markdown = (transcript.parent / "transcript.md").read_text(encoding="utf-8")
            self.assertIn("[00:00:00 - 00:00:00] Hello meeting.", markdown)

    def test_no_speech_does_not_publish_transcript(self):
        cases = [
            ("empty speech", " ", []),
            ("missing segments", "Hello meeting.", []),
            ("blank segments", "Hello meeting.", [{"start": 0.0, "end": 0.1, "text": " "}]),
        ]
        for label, text, segments in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                mlx_whisper = types.ModuleType("mlx_whisper")
                setattr(mlx_whisper, "transcribe", lambda *args, **kwargs: {
                    "text": text,
                    "language": "en",
                    "segments": segments,
                })
                output = directory / "output"
                with patch.dict(sys.modules, {"mlx_whisper": mlx_whisper}):
                    with self.assertRaisesRegex(TranscriptionError, "No speech"):
                        transcribe(self._recording(directory), output)
                self.assertFalse((output / "transcript.json").exists())
                self.assertFalse((output / "transcript.md").exists())
