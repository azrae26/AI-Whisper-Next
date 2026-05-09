import json

from ai_whisper.services.settings_store import SettingsStore


def test_settings_roundtrip_without_secret_fixture(tmp_path):
    path = tmp_path / "config.json"
    store = SettingsStore(path)
    cfg = store.get()
    cfg.hotkey = "pause"
    cfg.apiKey = ""
    store.save(cfg)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["hotkey"] == "pause"
    assert data["apiKey"] == ""

