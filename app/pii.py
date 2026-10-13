"""PII redaction for transcripts (Indian identifiers first).

Run before transcripts are logged, stored, or (by default) sent to a
third-party LLM. Order matters: card numbers are checked before Aadhaar,
since a 16-digit card contains a 12-digit run.

STT sometimes writes numbers as words ("nine eight seven ..."). Runs of
four or more number words are converted to digits and redacted if the
digits form a card, Aadhaar or phone number; anything else (like an order
ID read aloud) is left exactly as spoken. Deepgram's `redact=pci,numbers`
option can also redact at the STT layer; production would use both.
"""

from __future__ import annotations

import re

_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_AADHAAR = re.compile(r"(?<!\d)[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?!\d)")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)(?:\+?91[ -]?)?[6-9]\d{4}[ -]?\d{5}(?!\d)")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_REPEAT_WORDS = {"double": 2, "triple": 3}
_NUM_TOKEN = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|double|triple|\d+)"
_SPOKEN_RUN = re.compile(rf"\b{_NUM_TOKEN}(?:[\s,-]+{_NUM_TOKEN}){{3,}}\b", re.IGNORECASE)


def _spoken_to_digits(run: str) -> str:
    out, repeat = [], 1
    for token in re.findall(r"[a-zA-Z]+|\d+", run.lower()):
        if token in _REPEAT_WORDS:
            repeat = _REPEAT_WORDS[token]
            continue
        digits = _DIGIT_WORDS.get(token, token)
        out.append(digits[0] * repeat + digits[1:])  # "double 9" -> 99
        repeat = 1
    return "".join(out)


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def redact(text: str) -> tuple[str, list[str]]:
    """Return (redacted_text, list_of_entity_types_found)."""
    found: list[str] = []

    def card_sub(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            found.append("CARD")
            return "[CARD]"
        return m.group()

    def spoken_sub(m: re.Match) -> str:
        if not re.search(r"[a-zA-Z]", m.group()):
            return m.group()  # plain digits: the patterns below handle them
        digits = _spoken_to_digits(m.group())
        if 13 <= len(digits) <= 19 and _CARD.fullmatch(digits) and _luhn_ok(digits):
            label = "CARD"
        elif _AADHAAR.fullmatch(digits):
            label = "AADHAAR"
        elif _PHONE.fullmatch(digits):
            label = "PHONE"
        else:
            return m.group()
        found.append(label)
        return f"[{label}]"

    text = _SPOKEN_RUN.sub(spoken_sub, text)
    text = _CARD.sub(card_sub, text)
    for label, pattern in (
        ("AADHAAR", _AADHAAR),
        ("PAN", _PAN),
        ("PHONE", _PHONE),
        ("EMAIL", _EMAIL),
    ):
        text, n = pattern.subn(f"[{label}]", text)
        found.extend([label] * n)
    return text, found
