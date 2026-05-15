from __future__ import annotations

from dataclasses import dataclass, field


SUPPORTED_MODELS = [
    "gpt-4o-transcribe",
    "gpt-4o-mini-transcribe",
    "whisper-1",
]


@dataclass
class TextCorrection:
    source: str
    target: str


@dataclass
class AppConfig:
    apiKey: str = ""
    hotkey: str = "alt+`"
    hotkey_comma: str = "insert"
    history_hotkeys: list[str] = field(default_factory=lambda: [
        "alt+shift+1",
        "alt+shift+2",
        "alt+shift+3",
        "alt+shift+4",
        "alt+shift+5",
    ])
    model: str = "gpt-4o-transcribe"
    startup: bool = True
    geometry: str = "460x620"
    text_corrections: list[TextCorrection] = field(default_factory=list)
    segment_silence: float = 2.0
    segment_max_accum: float = 18.0
    segment_short_silence: float = 1.0
    warmup_idle_minutes: float = 10.0
    vad_confidence: float = 0.6
    vad_min_speech_sec: float = 0.35
    tap_trigger_enabled: bool = False
    tap_sensitivity: float = 1500.0


@dataclass
class TranscriptionResult:
    raw_text: str = ""
    clean_text: str = ""
    is_segment: bool = False
    received_at: float = 0.0
    error: str = ""

