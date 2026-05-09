# AI Whisper Next

PySide6 rewrite of AI Whisper. The first target is feature parity with the existing `F:\Cursor\AI Whisper` app while moving heavy audio, VAD, transcription, hotkey, UIA, clipboard, and packaging work away from the UI thread.

## Development

```powershell
python -m pip install -e .[dev]
python -m ai_whisper
```

The app automatically reads legacy settings from the old project when no local `config.json` exists. Do not commit real `config.json` files or API keys.

