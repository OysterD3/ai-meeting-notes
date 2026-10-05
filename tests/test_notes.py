from __future__ import annotations

from importlib import resources
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]

# Canned answers, one per pipeline stage. The infographic text tests HTML escaping.
_RESPONSES = {
    "glossary": {"terms": ["Night Audit", "Ledger"]},
    "extract": {
        "topics": [{"title": "Posting", "time_ranges": "00:00:00–00:00:02", "points": []}],
        "corrections": [],
        "screen_findings": ["A table is on screen."],
    },
    "draft": {"markdown": "# Draft"},
    "verify": {"issues": [{"location": "重点", "problem": "p", "evidence": "e", "fix": "f"}]},
    "fix": {"markdown": "# Final notes\n\n## 重点\n", "changes": ["fixed"]},
    "infographic": {
        "title": "Review · 这轮讨论整理",
        "subtitle": "<script>alert(1)</script> **Key**",
        "header_meta": "2026-10-05 · 录影 00:25",
        "sections": [
            {
                "heading": "Night Audit",
                "tone": "blue",
                "columns": 2,
                "cards": [
                    {"title": "Posting", "badge": "结论", "bullets": ["a & b"], "callout": "c", "source": "录音：00:00:00–00:00:02"}
                ],
            }
        ],
        "closing_summary": "s",
        "footer_note": "f",
        "notes_link_label": "完整会议笔记",
        "transcript_link_label": "原始时间轴转录",
    },
}

_COMMON_STUB = """
import json, os, re, sys

def stage():
    system = sys.argv[sys.argv.index("--system-prompt") + 1]
    return re.search(r"STAGE: (\\w+)", system).group(1)

def capture(record):
    with open(os.environ["STUB_CAPTURE"], "a", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False) + "\\n")

RESPONSES = json.loads(os.environ["STUB_RESPONSES"])
"""

_CLAUDE_STUB = _COMMON_STUB + """
message = json.loads(sys.stdin.readline())["message"]
blocks = message["content"]
name = stage()
if name == "verify" and os.environ.get("STUB_NO_ISSUES"):
    RESPONSES[name] = {"issues": []}
capture({"engine": "claude", "stage": name, "argv": sys.argv[1:],
         "images": sum(block["type"] == "image" for block in blocks), "text": blocks[0]["text"]})
print(json.dumps({"type": "system", "subtype": "init", "tools": ["StructuredOutput"]}))
if os.environ.get("STUB_FAIL_STAGE") == name:
    print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": "usage limit"}))
else:
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                      "structured_output": RESPONSES[name]}, ensure_ascii=False))
"""

_PI_STUB = _COMMON_STUB + """
name = stage()
if name == "verify" and os.environ.get("STUB_NO_ISSUES"):
    RESPONSES[name] = {"issues": []}
if os.environ.get("STUB_BAD_STAGE") == name:
    del RESPONSES[name]["sections"][0]["tone"]
capture({"engine": "pi", "stage": name, "argv": sys.argv[1:],
         "images": sum(argument.startswith("@") for argument in sys.argv[1:]), "text": sys.stdin.read()})
text = "```json\\n" + json.dumps(RESPONSES[name], ensure_ascii=False) + "\\n```"
print(json.dumps({"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
                  "content": [{"type": "thinking", "thinking": "ignore"}, {"type": "text", "text": text}]}}))
"""

_CHROME_STUB = """
import json, os, sys
with open(os.environ["STUB_CAPTURE"], "a", encoding="utf-8") as output:
    output.write(json.dumps({"engine": "chrome", "argv": sys.argv[1:]}) + "\\n")
if "--dump-dom" in sys.argv:
    print('<html><body data-page-height="321"></body></html>')
for argument in sys.argv:
    if argument.startswith("--screenshot="):
        with open(argument.split("=", 1)[1], "wb") as image:
            image.write(b"\\x89PNG\\r\\n\\x1a\\nstub")
"""

_MLX_STUB = """
import json, os

def transcribe(audio, **options):
    with open(os.environ["STUB_CAPTURE"], "a", encoding="utf-8") as output:
        output.write(json.dumps({"engine": "mlx", "samples": len(audio), "initial_prompt": options.get("initial_prompt")}, ensure_ascii=False) + "\\n")
    return {"text": "Night Audit 先做 report。", "language": "zh",
            "segments": [{"start": 0.0, "end": 2.0, "text": "Night Audit 先做 report。"}]}
"""


def _ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _executable(path: Path, body: str) -> None:
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class NotesPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        stubs = self.root / "stubs"
        (stubs / "mlx_whisper").mkdir(parents=True)
        (stubs / "mlx_whisper" / "__init__.py").write_text(textwrap.dedent(_MLX_STUB), encoding="utf-8")
        _executable(stubs / "claude", _CLAUDE_STUB)
        _executable(stubs / "pi", _PI_STUB)
        _executable(stubs / "chrome", _CHROME_STUB)
        self.capture = self.root / "capture.jsonl"
        self.environment = {
            **os.environ,
            "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
            "PYTHONPATH": str(stubs),
            "MEETING_NOTES_CHROME": str(stubs / "chrome"),
            "STUB_CAPTURE": str(self.capture),
            "STUB_RESPONSES": json.dumps(_RESPONSES, ensure_ascii=False),
        }

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _recording(self, kind: str) -> Path:
        if kind == "video":
            path = self.root / "meeting.mp4"
            inputs = ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=5", "-f", "lavfi", "-i", "sine=sample_rate=16000"]
            codecs = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "45", "-shortest"]
        else:
            path = self.root / "meeting.m4a"
            inputs = ["-f", "lavfi", "-i", "sine=sample_rate=16000"]
            codecs = ["-c:a", "aac", "-t", "45"]
        subprocess.run([_ffmpeg(), "-v", "error", "-y", *inputs, *codecs, str(path)], check=True)
        return path

    def _transcript(self, recording: Path) -> Path:
        output = self.root / "output"
        output.mkdir(exist_ok=True)
        transcript = output / "transcript.json"
        transcript.write_text(
            json.dumps(
                {
                    "source": str(recording.resolve()),
                    "duration_seconds": 45.0,
                    "language": "zh",
                    "model": "mlx-community/whisper-large-v3-turbo",
                    "text": "奈特 Audit 先做 report。Lager 进出。",
                    "segments": [
                        {"start": 0.0, "end": 2.0, "text": "奈特 Audit 先做 report。"},
                        {"start": 35.0, "end": 40.0, "text": "Lager 进出。"},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return transcript

    def _cli(self, *args: str, **environment: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "meeting_notes", *args],
            cwd=ROOT,
            env={**self.environment, **environment},
            text=True,
            capture_output=True,
            check=False,
        )

    def _records(self) -> list[dict]:
        if not self.capture.exists():
            return []
        return [json.loads(line) for line in self.capture.read_text(encoding="utf-8").splitlines()]

    def test_writes_notes_and_infographic_with_each_engine(self) -> None:
        # (engine, recording, frames, reviewers report issues)
        cases = [("claude", "video", 2, True), ("pi", "video", 2, True), ("pi", "audio", 0, False)]
        for engine, kind, frames, issues in cases:
            with self.subTest(engine=engine, recording=kind):
                self.capture.unlink(missing_ok=True)
                transcript = self._transcript(self._recording(kind))
                output = transcript.parent
                for name in ("notes.md", "meeting-infographic.html", "meeting-infographic.png"):
                    (output / name).unlink(missing_ok=True)

                environment = {} if issues else {"STUB_NO_ISSUES": "1"}
                result = self._cli("notes", str(transcript), "--engine", engine, **environment)

                self.assertEqual(result.returncode, 0, result.stderr)
                expected = "# Final notes\n\n## 重点\n" if issues else "# Draft\n"
                self.assertEqual((output / "notes.md").read_text(encoding="utf-8"), expected)
                page = (output / "meeting-infographic.html").read_text(encoding="utf-8")
                css = resources.files("meeting_notes").joinpath("infographic.css").read_text(encoding="utf-8")
                self.assertIn(f"<style>\n{css}</style>", page)
                self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; <strong>Key</strong>", page)
                self.assertNotIn("<script>", page)
                self.assertIn("<li>a &amp; b</li>", page)
                self.assertIn('<a href="./notes.md">', page)
                self.assertTrue((output / "meeting-infographic.png").read_bytes().startswith(b"\x89PNG"))

                records = self._records()
                calls = [record for record in records if record["engine"] == engine]
                stages = ["glossary", "extract", "draft", "verify", "verify", "verify", "fix", "infographic"]
                if not issues:
                    stages.remove("fix")
                self.assertEqual([call["stage"] for call in calls], stages)
                extract = calls[1]
                self.assertEqual(extract["images"], frames)
                # Pass 2 runs in chunks cut between pass 1 segments; times are shifted back to the recording.
                pass2 = extract["text"].split("Pass 2 transcript:")[1]
                self.assertIn("[00:00:00 - 00:00:02] Night Audit 先做 report。", pass2)
                self.assertIn("[00:00:34 - 00:00:36] Night Audit 先做 report。", pass2)
                self.assertNotIn("奈特", pass2)
                self.assertTrue(all("Night Audit 先做 report。" in call["text"] for call in calls if call["stage"] == "verify"))
                self.assertTrue(all("A table is on screen." in call["text"] for call in calls if call["stage"] == "verify"))
                if engine == "claude":
                    argv = calls[0]["argv"]
                    self.assertIn("--safe-mode", argv)
                    self.assertEqual(argv[argv.index("--tools") + 1], "")
                else:
                    self.assertIn("--no-tools", calls[0]["argv"])
                if not frames:
                    self.assertIn("no video track", calls[2]["text"])
                mlx = [record for record in records if record["engine"] == "mlx"]
                self.assertEqual(
                    mlx,
                    [
                        {"engine": "mlx", "samples": 35200, "initial_prompt": "Night Audit、Ledger"},
                        {"engine": "mlx", "samples": 86400, "initial_prompt": "Night Audit、Ledger"},
                    ],
                )
                screenshot = [record for record in records if record["engine"] == "chrome"][-1]["argv"]
                self.assertIn("--window-size=1600,321", screenshot)

    def test_refuses_existing_infographic_before_any_engine_call(self) -> None:
        transcript = self._transcript(self._recording("audio"))
        (transcript.parent / "meeting-infographic.png").write_bytes(b"existing")

        result = self._cli("notes", str(transcript), "--engine", "claude")

        self.assertEqual(result.returncode, 2)
        self.assertIn("refusing to overwrite existing artifact", result.stderr)
        self.assertEqual(self._records(), [])

    def test_missing_recording_fails_before_any_engine_call(self) -> None:
        recording = self._recording("audio")
        transcript = self._transcript(recording)
        recording.unlink()

        result = self._cli("notes", str(transcript), "--engine", "claude")

        self.assertEqual(result.returncode, 1)
        self.assertIn("recording not found", result.stderr)
        self.assertEqual(self._records(), [])

    def test_engine_error_writes_no_artifacts(self) -> None:
        transcript = self._transcript(self._recording("audio"))
        cases = [
            ("claude", {"STUB_FAIL_STAGE": "draft"}, "usage limit"),
            ("pi", {"STUB_BAD_STAGE": "infographic"}, "$.sections[0].tone is missing"),
        ]
        for engine, environment, message in cases:
            with self.subTest(engine=engine):
                result = self._cli("notes", str(transcript), "--engine", engine, **environment)

                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                for name in ("notes.md", "meeting-infographic.html", "meeting-infographic.png"):
                    self.assertFalse((transcript.parent / name).exists(), name)

    def test_rejects_model_option_for_the_other_engine(self) -> None:
        transcript = self._transcript(self._recording("audio"))

        result = self._cli("notes", str(transcript), "--engine", "claude", "--pi-model", "openai/gpt")

        self.assertEqual(result.returncode, 2)
        self.assertIn("--pi-model only applies to --engine pi", result.stderr)
        self.assertEqual(self._records(), [])


if __name__ == "__main__":
    unittest.main()
