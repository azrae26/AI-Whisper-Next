from __future__ import annotations

import re

from opencc import OpenCC

from .models import TextCorrection

_s2t = OpenCC("s2t")

TEXT_CORRECTION_DELIMITERS = ("→", "=", ",", ":", "|", "\t")

ZH_DIGIT_MAP: dict[str, str] = {
    "零": "0", "○": "0", "〇": "0",
    "一": "1", "二": "2", "兩": "2",
    "三": "3", "四": "4", "五": "5",
    "六": "6", "七": "7", "八": "8", "九": "9",
}
ZH_STRUCT_CHARS = frozenset("十百千萬億")
ZH_NUM_PATTERN = re.compile("[零○〇一二三四五六七八九十百千萬億兩]{2,}")
ZH_NUM_UNIT_PATTERN = re.compile("([零○〇一二三四五六七八九十百千萬億兩]+)([年月日季])")

POST_CORRECTIONS: dict[str, str] = {
    "?": "？",
    "羣": "群",
    "纔": "才",
    "裏": "裡",
    "臺灣": "台灣",
    "臺積電": "台積電",
    "臺北": "台北",
    "臺中": "台中",
    "臺南": "台南",
    "臺東": "台東",
    "臺西": "台西",
    "臺大": "台大",
    "臺科大": "台科大",
    "臺師大": "台師大",
    "臺幣": "台幣",
    "舞臺": "舞台",
    "平臺": "平台",
    "講臺": "講台",
    "陽臺": "陽台",
    "機臺": "機台",
    "臺階": "台階",
    "臺詞": "台詞",
    "臺燈": "台燈",
}


def parse_text_corrections(text: str) -> list[TextCorrection]:
    result: list[TextCorrection] = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        for sep in TEXT_CORRECTION_DELIMITERS:
            if sep in line:
                parts = line.split(sep, 1)
                if len(parts) == 2 and parts[0].strip():
                    result.append(TextCorrection(parts[0].strip(), parts[1].strip()))
                break
    return result


def corrections_to_text(corrections: list[TextCorrection]) -> str:
    return "\n".join(f"{c.source}→{c.target}" for c in corrections if c.source)


def apply_user_corrections(text: str, corrections: list[TextCorrection]) -> str:
    for item in corrections:
        if item.source:
            text = text.replace(item.source, item.target)
    return text


def _zh_num_to_arabic(zh: str) -> str:
    if not ZH_STRUCT_CHARS.intersection(zh):
        mapped = "".join(ZH_DIGIT_MAP.get(c, c) for c in zh)
        if mapped.isdigit():
            return mapped
    try:
        import cn2an
        normalized = zh.replace("○", "零").replace("〇", "零")
        return str(cn2an.cn2an(normalized, "smart"))
    except Exception:
        return zh


def convert_chinese_numbers(text: str) -> str:
    text = ZH_NUM_UNIT_PATTERN.sub(
        lambda m: _zh_num_to_arabic(m.group(1)) + m.group(2),
        text,
    )
    return ZH_NUM_PATTERN.sub(lambda m: _zh_num_to_arabic(m.group(0)), text)


def normalize_transcription_text(raw_text: str, corrections: list[TextCorrection]) -> str:
    text = _s2t.convert(raw_text.strip())
    for wrong, correct in POST_CORRECTIONS.items():
        text = text.replace(wrong, correct)
    text = convert_chinese_numbers(text)
    text = text.rstrip("。")
    return apply_user_corrections(text, corrections)

