# ai-meeting-notes

Turn an existing meeting recording into a timestamped transcript, detailed Markdown notes, and a one-page infographic.

- **Local transcription:** MLX Whisper on an Apple Silicon Mac.
- **AI notes:** your selected model and existing authentication through [Pi](https://github.com/earendil-works/pi) (default) or [Claude Code](https://code.claude.com/docs) (`claude -p`).
- **Plain files:** no database, web server, or meeting bot.

```text
Recording → FFmpeg → MLX Whisper → transcript.json + transcript.md
                                          ↓
          Pi or claude -p: glossary → second MLX Whisper pass with the glossary
                         → screen frames each 20 s → extract → draft → 3 reviews → fix
                                          ↓
              notes.md + meeting-infographic.html + meeting-infographic.png (Chrome)
```

## Requirements

- An **Apple Silicon Mac**. Intel Macs, Windows, and Linux are not supported by this implementation.
- [uv](https://docs.astral.sh/uv/getting-started/installation/). Setup below uses Python 3.12.
- Node.js **22.19.0 or newer**, and Pi CLI **1.0.2** (the tested version).
- A model configured in Pi, using an API key or a supported subscription sign-in. Or, for `--engine claude`, the Claude Code CLI (`claude`), signed in. Provider usage limits and charges still apply.
- Google Chrome (or Chromium, Edge, or Brave) to render `meeting-infographic.png`. If the browser is not in `/Applications`, set `MEETING_NOTES_CHROME` to its executable.
- Internet access for the first model/dependency download and for a hosted notes provider.

FFmpeg is supplied by `imageio-ffmpeg`; a Homebrew install is not required. Whisper Turbo is downloaded to the Hugging Face cache on first use.

## Setup

```sh
git clone https://github.com/OysterD3/ai-meeting-notes.git
cd ai-meeting-notes
uv sync --locked --python 3.12

npm install -g @earendil-works/pi-coding-agent@1.0.2
pi
```

In Pi, use `/login` to configure authentication and `/model` to choose a model, then exit Pi. If Pi is already installed and configured, keep your existing sign-in; no credentials are stored in this project.

To use Claude Code instead of Pi, install Claude Code and sign in with `claude`. Then add `--engine claude` to the `run` or `notes` command.

## Use

Run transcription and note generation:

```sh
uv run meeting-notes run '/path/to/recording.mp4'
```

The default destination is `output/<recording-name>/`:

| File | Contents |
| --- | --- |
| `transcript.json` | Source path, duration, language, model, full text, and timestamped segments |
| `transcript.md` | Readable transcript with `HH:MM:SS` time ranges |
| `notes.md` | Key points, topic-by-topic discussion with timestamps, follow-ups table, open questions, and a check section |
| `meeting-infographic.html` | One-page poster of the notes |
| `meeting-infographic.png` | The poster as a 1600 px wide image |

Run either stage separately:

```sh
# Local transcription only; no transcript is sent to a notes provider.
uv run meeting-notes transcribe '/path/to/recording.mp4' --language zh

# Generate or retry notes from the saved transcript. The recording must still be at its original path.
uv run meeting-notes notes output/recording/transcript.json

# Use Claude Code (claude -p) instead of Pi.
uv run meeting-notes notes output/recording/transcript.json --engine claude

# Create a new revision without replacing the previous notes.
uv run meeting-notes notes output/recording/transcript.json \
  --output output/recording/notes-revised.md
```

A revision writes `notes-revised-infographic.html` and `notes-revised-infographic.png` next to `notes-revised.md`.

### Options

| Option | Commands | Purpose |
| --- | --- | --- |
| `--output DIR` | `run`, `transcribe` | Choose an output directory |
| `--output FILE` | `notes` | Choose the Markdown output file |
| `--language CODE` | `run`, `transcribe` | Set the speech language, such as `zh`; otherwise detect it |
| `--model HF_ASR_ID` | `run`, `transcribe` | Choose an MLX Whisper model; default: `mlx-community/whisper-large-v3-turbo` |
| `--engine pi\|claude` | `run`, `notes` | Choose the notes engine; default: `pi` |
| `--pi-model PROVIDER/MODEL` | `run`, `notes` | Choose the Pi model for this command |
| `--claude-model MODEL` | `run`, `notes` | Choose the Claude model for this command, such as `opus` or `sonnet` |

Without `--pi-model`, the tool uses `PI_PROVIDER` and `PI_MODEL` when both are set, otherwise Pi's configured default. See `uv run meeting-notes run --help` for command help.

## Privacy and safety

- Transcription is local. The tool does not upload the recording or the audio.
- Note generation sends data to the provider of the selected engine (Pi's provider, or Anthropic through Claude Code): both transcripts with timestamps, the recording file name, and **screen frames from the video, one each 20 seconds**. Frames can show documents, chat messages, names, and other personal data that is visible on screen. Audio-only recordings send no frames.
- The notes step is **not fully offline** when using a hosted provider. Check that provider's retention policy and obtain permission before processing or sharing a meeting.
- Pi is launched without tools, extensions, skills, project context, or a saved Pi session. Claude Code is launched with `--safe-mode` (no CLAUDE.md, skills, plugins, hooks, or MCP servers), no tools, and no saved session. Images go inside the request. The prompts treat the transcripts and frames as data, not instructions.
- Generated files are plain text on disk, not encrypted. `output/`, local recordings, `.env` files, and local Pi configuration are ignored by Git. If you choose a different output location, add it to `.gitignore` before committing.
- No real recordings, transcripts, notes, or meeting images are included in this repository.

## Limits and failure recovery

Speech recognition and AI notes can contain errors, especially with mixed-language speech, names, numbers, or overlapping speakers. The prompt requests timestamp evidence and asks the model not to invent owners, deadlines, or facts, but this is not a guarantee. Check important points against the recording.

There is no speaker identification. The notes do not use the video to say who is talking; screen frames are used only for the content that they show. There is no live recording. The entire audio waveform is loaded into memory. The notes step makes about 8 model requests plus 1 for each 7 minutes of recording, and the review requests send the full transcripts. Very long meetings can exceed available memory or the selected model's context limit. Each model request has a 15-minute timeout. For a 26-minute recording, the notes step took about 12 minutes with `--engine claude` and about 30 minutes with Pi; the time depends on the model.

A normal rerun refuses to replace existing artifacts. Use a new output directory or a new notes filename. If the notes step fails, it writes no notes files, and the completed transcript remains available, so the `notes` command can retry without transcribing again.

If dependencies are missing, run `uv sync --locked --python 3.12`. If Pi is missing or authentication fails, check `pi --version` and your Pi sign-in. For `--engine claude`, check `claude --version` and your Claude Code sign-in. For decoding failures, confirm the media file is readable and has an audio track.

## Development

```sh
uv sync --locked --python 3.12
uv run python -m unittest discover -s tests -v
uv build
```

Tests use short generated audio and video, and substitutes for the external MLX, Pi, Claude Code, and Chrome boundaries. They exercise the local command and conversion paths without downloading speech models or making paid AI calls. The test suite does not measure transcription or note accuracy.

## License

[MIT](LICENSE). The license covers this project's code; third-party packages, model weights, and AI services have their own terms.
