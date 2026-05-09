from ai_whisper.services.hotkey_service import HotkeyService, parse_hotkey


def test_parse_hotkey_special_keys():
    assert parse_hotkey("ctrl+alt+page up") == (["ctrl", "alt"], "page up")
    assert parse_hotkey("insert") == ([], "insert")
    assert parse_hotkey("pause") == ([], "pause")


def test_win32_history_vk_mapping():
    assert HotkeyService.parse_hotkey_win32("alt+shift+1") == (0x0001 | 0x0004, ord("1"))
    assert HotkeyService.parse_hotkey_win32("ctrl+f5") == (0x0002, 0x6F + 5)
    assert HotkeyService.parse_hotkey_win32("pause") == (0, 0x13)

