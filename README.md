# ai-meeting-notes

Turn an existing meeting recording into a timestamped transcript and detailed Markdown notes.

- **Local transcription:** MLX Whisper on an Apple Silicon Mac.
- **AI notes:** your selected model and existing authentication through [Pi](https://github.com/earendil-works/pi).
- **Plain files:** no database, web server, or meeting bot.

```text
Recording → FFmpeg → MLX Whisper → transcript.json + transcript.md
                                          ↓
                               Pi → selected AI provider → notes.md
```

## Requirements

- An **Apple Silicon Mac**. Intel Macs, Windows, and Linux are not supported by this implementation.
- [uv](https://docs.astral.sh/uv/getting-started/installation/). Setup below uses Python 3.12.
- Node.js **22.19.0 or newer**, and Pi CLI **0.85.1** (the tested version).
- A model configured in Pi, using an API key or a supported subscription sign-in. Provider usage limits and charges still apply.
- Internet access for the first model/dependency download and for a hosted notes provider.

FFmpeg is supplied by `imageio-ffmpeg`; a Homebrew install is not required. Whisper Turbo is downloaded to the Hugging Face cache on first use.

## Setup

```sh
git clone https://github.com/OysterD3/ai-meeting-notes.git
cd ai-meeting-notes
uv sync --locked --python 3.12

npm install -g @earendil-works/pi-coding-agent@0.85.1
pi
```

In Pi, use `/login` to configure authentication and `/model` to choose a model, then exit Pi. If Pi is already installed and configured, keep your existing sign-in; no credentials are stored in this project.

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
| `notes.md` | Topic-by-topic notes, reasons, examples, decisions, actions, and open questions |

Run either stage separately:

```sh
# Local transcription only; no transcript is sent to a notes provider.
uv run meeting-notes transcribe '/path/to/recording.mp4' --language zh

# Generate or retry notes from the saved transcript.
uv run meeting-notes notes output/recording/transcript.json

# Create a new revision without replacing the previous notes.
uv run meeting-notes notes output/recording/transcript.json \
  --output output/recording/notes-revised.md
```

### Options

| Option | Commands | Purpose |
| --- | --- | --- |
| `--output DIR` | `run`, `transcribe` | Choose an output directory |
| `--output FILE` | `notes` | Choose the Markdown output file |
| `--language CODE` | `run`, `transcribe` | Set the speech language, such as `zh`; otherwise detect it |
| `--model HF_ASR_ID` | `run`, `transcribe` | Choose an MLX Whisper model; default: `mlx-community/whisper-large-v3-turbo` |
| `--pi-model PROVIDER/MODEL` | `run`, `notes` | Choose the notes model for this command |

Without `--pi-model`, the tool uses `PI_PROVIDER` and `PI_MODEL` when both are set, otherwise Pi's configured default. See `uv run meeting-notes run --help` for command help.

## Privacy and safety

- The recording and extracted audio are processed locally. This tool does not upload them.
- Note generation sends **the transcript JSON** to the provider selected through Pi. This includes the text, timestamps, and metadata such as the recording's absolute file path.
- The notes step is **not fully offline** when using a hosted provider. Check that provider's retention policy and obtain permission before processing or sharing a meeting.
- Pi is launched without tools, extensions, skills, project context, or a saved Pi session. The prompt treats the transcript as data, not instructions.
- Generated files are plain text on disk, not encrypted. `output/`, local recordings, `.env` files, and local Pi configuration are ignored by Git. If you choose a different output location, add it to `.gitignore` before committing.
- No real recordings, transcripts, notes, or meeting images are included in this repository.

## Limits and failure recovery

Speech recognition and AI notes can contain errors, especially with mixed-language speech, names, numbers, or overlapping speakers. The prompt requests timestamp evidence and asks the model not to invent owners, deadlines, or facts, but this is not a guarantee. Check important points against the recording.

There is no speaker identification, screen analysis, live recording, or infographic export command. The entire audio waveform is loaded into memory, and the notes step sends the full transcript in one request. Very long meetings can exceed available memory or the selected model's context limit. Pi note generation has a 10-minute timeout.

A normal rerun refuses to replace existing artifacts. Use a new output directory or a new notes filename. If Pi fails, the completed transcript remains available, so the `notes` command can retry without transcribing again.

If dependencies are missing, run `uv sync --locked --python 3.12`. If Pi is missing or authentication fails, check `pi --version` and your Pi sign-in. For decoding failures, confirm the media file is readable and has an audio track.

## Development

```sh
uv sync --locked --python 3.12
uv run python -m unittest discover -s tests -v
uv build
```

Tests use short generated audio and substitutes for the external MLX/Pi boundaries. They exercise the local command and conversion paths without downloading speech models or making paid AI calls. The test suite does not measure transcription or note accuracy.

## License

[MIT](LICENSE). The license covers this project's code; third-party packages, model weights, and AI services have their own terms.
