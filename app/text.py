"""Turning a token stream into speakable chunks.

TTS needs a little context to get intonation right, so we don't send it
single tokens. Waiting for the full reply is too slow, so we cut at sentence
boundaries. For the very first chunk we're more aggressive and also cut at a
comma once enough text has built up: the first chunk decides how fast the
caller hears anything, and every later chunk plays while the next is generated.
"""

from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"[.!?।]+[\"')\]]*\s+")  # । = Hindi danda (।)
_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "rs", "no", "st", "vs", "etc", "e.g", "i.e", "approx"}
_MARKDOWN = re.compile(r"[*_#`>]+|\[(.*?)\]\(.*?\)")


def clean_for_tts(text: str) -> str:
    """Strip markdown the LLM might emit despite instructions."""
    text = _MARKDOWN.sub(lambda m: m.group(1) or "", text)
    return re.sub(r"\s+", " ", text).strip()


class SentenceChunker:
    def __init__(self, first_chunk_min_chars: int = 40) -> None:
        self.buffer = ""
        self.emitted = 0
        self.first_chunk_min_chars = first_chunk_min_chars

    def feed(self, text: str) -> list[str]:
        self.buffer += text
        out: list[str] = []
        while True:
            cut = self._find_sentence_cut()
            if cut is None and self.emitted == 0:
                cut = self._find_clause_cut()
            if cut is None:
                break
            chunk, self.buffer = self.buffer[:cut], self.buffer[cut:]
            chunk = clean_for_tts(chunk)
            if chunk:
                out.append(chunk)
                self.emitted += 1
        return out

    def flush(self) -> list[str]:
        chunk = clean_for_tts(self.buffer)
        self.buffer = ""
        if chunk:
            self.emitted += 1
            return [chunk]
        return []

    def _find_sentence_cut(self) -> int | None:
        for m in _SENTENCE_END.finditer(self.buffer):
            before = self.buffer[: m.start()]
            last_word = before.split()[-1].lower().rstrip(".") if before.split() else ""
            if last_word in _ABBREVIATIONS:
                continue
            return m.end()
        return None

    def _find_clause_cut(self) -> int | None:
        if len(self.buffer) < self.first_chunk_min_chars:
            return None
        idx = self.buffer.rfind(", ")
        if idx >= self.first_chunk_min_chars // 2:
            return idx + 2
        return None
