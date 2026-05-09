# AI Whisper Next

AI Whisper Next is a PySide6 desktop speech-to-text tool for Windows. It records from the microphone, transcribes Traditional Chinese with OpenAI audio models, pastes the result into the active app, and keeps recent transcription history for quick reuse.

This project is a rewrite of the original local AI Whisper app, with audio capture, VAD, transcription, hotkeys, UI Automation paste handling, clipboard fallback, and packaging work separated from the UI thread.

## Features

- Global hotkeys for recording and pasting transcribed text.
- Separate hotkeys for punctuation styles.
- OpenAI transcription model selection.
- Traditional Chinese normalization and custom text corrections.
- Voice activity detection before sending audio for transcription.
- Automatic segmentation during longer recordings.
- Recent transcription history with copy shortcuts.
- System tray and taskbar icon state changes while recording.
- PyInstaller packaging scripts for Windows builds.

## Development

```powershell
python -m pip install -e .[dev]
python -m ai_whisper
```

## Packaging

```powershell
.\scripts\pack.ps1
```

The generated build artifacts are ignored by Git.

## Configuration

The app stores local runtime settings in `config.json`. This file can contain an OpenAI API key and is intentionally ignored by Git.

When no local `config.json` exists, the app can read compatible legacy settings from the previous local AI Whisper project.

## Safety Notes

- Do not commit real API keys.
- Do not commit local logs, packaged output, virtual environments, or generated metadata.
- The included `.gitignore` excludes `config.json`, logs, `build/`, `dist/`, virtual environments, and package metadata.
