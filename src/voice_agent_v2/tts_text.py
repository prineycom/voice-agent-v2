"""Deterministic Russian segmentation and TTS-only shaping for Silero.

Visible LLM text is never rewritten by this module.  The returned synthesis text
is private to the TTS request boundary and must never be copied to UI/history or
content-bearing diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .contracts import StageFailure

SEGMENTATION_VERSION = "voice-agent.silero-ru-segmentation.v1"
SHAPING_VERSION = "voice-agent.silero-ru-shaping.v1"
TARGET_MIN_CHARS = 40
TARGET_MAX_CHARS = 100
HARD_MAX_CHARS = 240
MAX_SEGMENTS = 16

_SENTENCE_MARKS = frozenset(".!?…。！？")
_CLAUSE_MARKS = frozenset(",;:")
_COMMON_DOT_ABBREVIATIONS = frozenset({
    "г", "гг", "ул", "д", "кв", "стр", "рис", "им", "т", "е", "н", "э",
    "тд", "тп", "др", "проф", "акад", "руб", "коп",
})
_SSML_OR_TAG = re.compile(
    r"<\s*(?:/?\s*(?:[^\W\d_]|[_:])|[!?])[^>]*>", re.UNICODE
)


def _reject_markup(text: str) -> None:
    if _SSML_OR_TAG.search(text) or "\x00" in text:
        raise StageFailure("tts", "tts_plain_text_required")


def _looks_like_tag_prefix(text: str) -> bool:
    return bool(
        re.match(r"^<\s*(?:/?\s*(?:[^\W\d_]|[_:])|[!?])", text, re.UNICODE)
        or re.fullmatch(r"<\s*/?\s*", text)
    )


def _unmatched_tag_start(text: str) -> int | None:
    last_close = text.rfind(">")
    for index in range(last_close + 1, len(text)):
        if text[index] == "<" and _looks_like_tag_prefix(text[index:]):
            return index
    return None


def _boundary_followed_by_space_or_end(text: str, end: int) -> bool:
    return end == len(text) or text[end].isspace()


def _sentence_boundaries(text: str) -> list[int]:
    boundaries: list[int] = []
    index = 0
    while index < len(text):
        if text[index] not in _SENTENCE_MARKS:
            index += 1
            continue
        start = index
        while index + 1 < len(text) and text[index + 1] in _SENTENCE_MARKS:
            index += 1
        end = index + 1
        mark = text[start]
        if mark == ".":
            if start > 0 and end < len(text) and text[start - 1].isdigit() and text[end].isdigit():
                index += 1
                continue
            word_match = re.search(r"([\wА-Яа-яЁё]+)$", text[:start])
            word = word_match.group(1).lower() if word_match else ""
            if word in _COMMON_DOT_ABBREVIATIONS or (len(word) == 1 and word.isalpha()):
                index += 1
                continue
        if _boundary_followed_by_space_or_end(text, end):
            boundaries.append(end)
        index += 1
    return boundaries


def _clause_boundaries(text: str) -> list[int]:
    boundaries: list[int] = []
    for index, mark in enumerate(text):
        end = index + 1
        if mark in _CLAUSE_MARKS:
            if (
                mark == ","
                and index > 0
                and end < len(text)
                and text[index - 1].isdigit()
                and text[end].isdigit()
            ):
                continue
            if _boundary_followed_by_space_or_end(text, end):
                boundaries.append(end)
        elif mark in {"—", "–"}:
            before_safe = index > 0 and text[index - 1].isspace()
            after_safe = end == len(text) or text[end].isspace()
            if before_safe and after_safe:
                boundaries.append(end)
    return boundaries


class RussianTTSSegmenter:
    """Incremental punctuation-aware segmenter with a fail-closed hard cap."""

    def __init__(self) -> None:
        self._buffer = ""
        self._validation_tail = ""
        self._segments = 0
        self._finished = False

    @property
    def buffered_chars(self) -> int:
        return len(self._buffer)

    def feed(self, visible_piece: str, *, final: bool = False) -> tuple[str, ...]:
        if self._finished:
            raise StageFailure("tts", "tts_segmenter_already_final")
        if not isinstance(visible_piece, str):
            raise StageFailure("tts", "tts_text_out_of_bounds")
        validation_text = self._validation_tail + visible_piece
        _reject_markup(validation_text)
        unmatched_tag = _unmatched_tag_start(validation_text)
        if unmatched_tag is None:
            self._validation_tail = ""
            validated_piece = validation_text
        else:
            self._validation_tail = validation_text[unmatched_tag:]
            validated_piece = validation_text[:unmatched_tag]
            if final:
                raise StageFailure("tts", "tts_plain_text_required")
        piece = validated_piece.strip()
        if piece:
            if self._buffer and not self._buffer[-1].isspace():
                self._buffer += " "
            self._buffer += piece
        emitted: list[str] = []
        while self._buffer:
            boundary = self._next_boundary(final=final)
            if boundary is None:
                break
            segment = self._buffer[:boundary].strip()
            self._buffer = self._buffer[boundary:].lstrip()
            if not segment:
                continue
            if len(segment) > HARD_MAX_CHARS:
                raise StageFailure("tts", "tts_segment_out_of_bounds")
            self._segments += 1
            if self._segments > MAX_SEGMENTS:
                raise StageFailure("tts", "tts_segment_queue_out_of_bounds")
            emitted.append(segment)
        if final:
            self._finished = True
            if self._buffer:
                raise StageFailure("tts", "tts_segment_out_of_bounds")
        return tuple(emitted)

    def finish(self) -> tuple[str, ...]:
        return self.feed("", final=True)

    def _next_boundary(self, *, final: bool) -> int | None:
        text = self._buffer
        sentence = [position for position in _sentence_boundaries(text) if position >= TARGET_MIN_CHARS]
        target_sentence = next((position for position in sentence if position <= TARGET_MAX_CHARS), None)
        if target_sentence is not None:
            return target_sentence
        if len(text) >= TARGET_MAX_CHARS:
            clauses = [
                position for position in _clause_boundaries(text)
                if TARGET_MIN_CHARS <= position <= TARGET_MAX_CHARS
            ]
            if clauses:
                return clauses[0]
            if sentence and sentence[0] <= HARD_MAX_CHARS:
                return sentence[0]
            later_clause = next(
                (
                    position for position in _clause_boundaries(text)
                    if TARGET_MIN_CHARS <= position <= HARD_MAX_CHARS
                ),
                None,
            )
            if later_clause is not None:
                return later_clause
        if len(text) >= HARD_MAX_CHARS:
            whitespace = max(
                (index for index, character in enumerate(text[:HARD_MAX_CHARS + 1]) if character.isspace()),
                default=-1,
            )
            if whitespace <= 0:
                raise StageFailure("tts", "tts_segment_has_no_safe_boundary")
            return whitespace
        if final and text.strip():
            return len(text)
        return None


@dataclass(frozen=True)
class ShapedTTS:
    visible_text: str
    synthesis_text: str
    version: str = SHAPING_VERSION


_ONES = (
    "ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять",
    "десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать",
    "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать",
)
_TENS = ("", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто")
_HUNDREDS = ("", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот")
_MONTHS = (
    "", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
    "сентября", "октября", "ноября", "декабря",
)
_DIGITS = ("ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять")
_DAY_ORDINAL = (
    "", "первое", "второе", "третье", "четвёртое", "пятое", "шестое", "седьмое",
    "восьмое", "девятое", "десятое", "одиннадцатое", "двенадцатое",
    "тринадцатое", "четырнадцатое", "пятнадцатое", "шестнадцатое",
    "семнадцатое", "восемнадцатое", "девятнадцатое", "двадцатое",
    "двадцать первое", "двадцать второе", "двадцать третье", "двадцать четвёртое",
    "двадцать пятое", "двадцать шестое", "двадцать седьмое", "двадцать восьмое",
    "двадцать девятое", "тридцатое", "тридцать первое",
)
_ORDINAL_GENITIVE_ONES = {
    1: "первого", 2: "второго", 3: "третьего", 4: "четвёртого", 5: "пятого",
    6: "шестого", 7: "седьмого", 8: "восьмого", 9: "девятого",
    10: "десятого", 11: "одиннадцатого", 12: "двенадцатого", 13: "тринадцатого",
    14: "четырнадцатого", 15: "пятнадцатого", 16: "шестнадцатого",
    17: "семнадцатого", 18: "восемнадцатого", 19: "девятнадцатого",
}
_ORDINAL_GENITIVE_TENS = {
    20: "двадцатого", 30: "тридцатого", 40: "сорокового", 50: "пятидесятого",
    60: "шестидесятого", 70: "семидесятого", 80: "восьмидесятого", 90: "девяностого",
}


def _under_thousand(value: int, *, feminine: bool = False) -> list[str]:
    words: list[str] = []
    if value >= 100:
        words.append(_HUNDREDS[value // 100])
        value %= 100
    if value < 20:
        if value:
            if feminine and value == 1:
                words.append("одна")
            elif feminine and value == 2:
                words.append("две")
            else:
                words.append(_ONES[value])
        return words
    words.append(_TENS[value // 10])
    value %= 10
    if value:
        if feminine and value == 1:
            words.append("одна")
        elif feminine and value == 2:
            words.append("две")
        else:
            words.append(_ONES[value])
    return words


def _plural(value: int, one: str, few: str, many: str) -> str:
    last_two = value % 100
    if 11 <= last_two <= 14:
        return many
    last = value % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def number_to_russian(value: int) -> str:
    if value == 0:
        return _ONES[0]
    if value < 0 or value > 999_999_999:
        return str(value)
    groups = (
        (1_000_000, False, ("миллион", "миллиона", "миллионов")),
        (1_000, True, ("тысяча", "тысячи", "тысяч")),
    )
    words: list[str] = []
    for scale, feminine, forms in groups:
        count, value = divmod(value, scale)
        if count:
            words.extend(_under_thousand(count, feminine=feminine))
            words.append(_plural(count, *forms))
    words.extend(_under_thousand(value))
    return " ".join(words)


def _ordinal_genitive(value: int) -> str:
    if value in _ORDINAL_GENITIVE_ONES:
        return _ORDINAL_GENITIVE_ONES[value]
    if value in _ORDINAL_GENITIVE_TENS:
        return _ORDINAL_GENITIVE_TENS[value]
    tens, ones = divmod(value, 10)
    if 2 <= tens <= 9 and ones:
        return f"{_TENS[tens]} {_ORDINAL_GENITIVE_ONES[ones]}"
    return number_to_russian(value)


def _spoken_year(year: int) -> str:
    if year == 2_000:
        return "двухтысячного"
    if 2_001 <= year <= 2_099:
        return f"две тысячи {_ordinal_genitive(year - 2_000)}"
    return number_to_russian(year)


def _match_case(source: str, replacement: str) -> str:
    if source.isupper():
        return replacement.upper()
    if source[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def shape_russian_tts(visible_text: str) -> ShapedTTS:
    if not isinstance(visible_text, str) or not visible_text.strip() or len(visible_text) > HARD_MAX_CHARS:
        raise StageFailure("tts", "tts_text_out_of_bounds")
    _reject_markup(visible_text)
    if _unmatched_tag_start(visible_text) is not None:
        raise StageFailure("tts", "tts_plain_text_required")
    text = visible_text.strip()

    abbreviations = {
        r"\bPDF\b": "Пи-Ди-Эф",
        r"\bSSD\b": "эс-эс-ди",
        r"\bHTTP\b": "эйч-ти-ти-пи",
        r"\bИИ\b": "искусственный интеллект",
        r"\bт\.\s*е\.": "то есть",
        r"\bи\s+т\.\s*д\.": "и так далее",
        r"\bи\s+т\.\s*п\.": "и тому подобное",
    }
    for pattern, replacement in abbreviations.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    def date_replacement(match: re.Match[str]) -> str:
        day, month, year = (int(part) for part in match.groups())
        if not (1 <= day <= 31 and 1 <= month <= 12 and 1 <= year <= 9999):
            return match.group(0)
        return f"{_DAY_ORDINAL[day]} {_MONTHS[month]} {_spoken_year(year)} года"

    text = re.sub(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)", date_replacement, text)

    def time_replacement(match: re.Match[str]) -> str:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            return match.group(0)
        return (
            f"{number_to_russian(hour)} {_plural(hour, 'час', 'часа', 'часов')} "
            f"{number_to_russian(minute)} {_plural(minute, 'минута', 'минуты', 'минут')}"
        )

    text = re.sub(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", time_replacement, text)

    def decimal_replacement(match: re.Match[str]) -> str:
        whole, fraction = match.group(1), match.group(2)
        spoken_fraction = " ".join(_DIGITS[int(digit)] for digit in fraction)
        return f"{number_to_russian(int(whole))} запятая {spoken_fraction}"

    text = re.sub(r"(?<!\d)(\d+)[,.](\d+)(?!\d)", decimal_replacement, text)
    text = re.sub(
        r"(?<![\w+])\d+(?![\w])",
        lambda match: number_to_russian(int(match.group(0))),
        text,
    )
    text = text.replace("+", " плюс ")

    yo_words = {
        "елка": "ёлка", "елку": "ёлку", "мед": "мёд", "теплый": "тёплый",
        "теплая": "тёплая", "теплое": "тёплое", "самолет": "самолёт",
        "самолетом": "самолётом",
    }
    for source, replacement in yo_words.items():
        text = re.sub(
            rf"\b{source}\b",
            lambda match, replacement=replacement: _match_case(match.group(0), replacement),
            text,
            flags=re.IGNORECASE,
        )

    # The '+' markers below are Silero-only stress hints.  They never alter the
    # visible string and diagnostics are forbidden from accepting text fields.
    text = re.sub(r"\bстаринный\s+замок\b", "старинный з+амок", text, flags=re.IGNORECASE)
    text = re.sub(r"\bна\s+двери\s+висит\s+замок\b", "на двери висит зам+ок", text, flags=re.IGNORECASE)
    text = re.sub(r"\bоткрою\s+замок\b", "открою зам+ок", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    if not text or len(text) > 320:
        raise StageFailure("tts", "tts_normalized_text_out_of_bounds")
    return ShapedTTS(visible_text=visible_text, synthesis_text=text)
