from ai_whisper.text_processing import (
    convert_chinese_numbers,
    normalize_transcription_text,
    parse_text_corrections,
)


def test_parse_text_corrections_delimiters():
    parsed = parse_text_corrections("臺灣→台灣\n羣=群\n纔,才\n裏:裡\nA|B\nC\tD")
    assert [(x.source, x.target) for x in parsed] == [
        ("臺灣", "台灣"),
        ("羣", "群"),
        ("纔", "才"),
        ("裏", "裡"),
        ("A", "B"),
        ("C", "D"),
    ]


def test_convert_chinese_numbers_units_and_sequences():
    assert convert_chinese_numbers("二○二六年三月十日") == "2026年3月10日"
    assert convert_chinese_numbers("代號一二三四") == "代號1234"


def test_normalize_transcription_text_keeps_old_pipeline():
    corrections = parse_text_corrections("平台→平臺")
    assert normalize_transcription_text("平臺在二○二六年。", corrections) == "平臺在2026年"
