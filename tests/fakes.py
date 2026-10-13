"""Offline stand-ins for STT, LLM and TTS, so the orchestration logic
(turn-taking, barge-in, marks, history) can be tested without API keys."""

from __future__ import annotations

import asyncio
import dataclasses
import re

import numpy as np

from app.audio import rms_dbfs, ulaw_to_pcm16
from app.config import Settings


def make_settings(**overrides) -> Settings:
    return dataclasses.replace(Settings.from_env(), **overrides)


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    def events(self, kind: str) -> list[dict]:
        return [m for m in self.sent if m["event"] == kind]

    def mark_names(self) -> list[str]:
        return [m["mark"]["name"] for m in self.events("mark")]


class FakeSTT:
    """Tests drive the session's STT callbacks directly."""

    def __init__(self) -> None:
        self.handler = None
        self.audio = bytearray()
        self.closed = False

    async def start(self, handler) -> None:
        self.handler = handler

    async def send(self, audio: bytes) -> None:
        self.audio.extend(audio)

    async def close(self) -> None:
        self.closed = True


class EnergySTT:
    """A pretend STT for end-to-end runs: it detects speech by loudness and,
    when a burst of speech ends, 'transcribes' it as the next scripted line."""

    def __init__(self, lines: list[str], silence_ms: int = 400) -> None:
        self.lines = list(lines)
        self.silence_ms = silence_ms
        self.handler = None
        self.speech_ms = 0
        self.quiet_ms = 0
        self.interim_sent = False

    async def start(self, handler) -> None:
        self.handler = handler

    async def send(self, audio: bytes) -> None:
        loud = rms_dbfs(ulaw_to_pcm16(audio)) > -35
        if loud:
            self.speech_ms += 20
            self.quiet_ms = 0
            if self.speech_ms >= 400 and not self.interim_sent:
                self.interim_sent = True
                await self.handler.on_transcript("hey wait", False, False)
        elif self.speech_ms:
            self.quiet_ms += 20
            if self.quiet_ms >= self.silence_ms:
                text = self.lines.pop(0) if self.lines else "okay"
                self.speech_ms = self.quiet_ms = 0
                self.interim_sent = False
                await self.handler.on_transcript(text, True, True)

    async def close(self) -> None:
        pass


class ScriptedAgent:
    def __init__(self, replies: list[str], first_token_delay: float = 0.0, token_delay: float = 0.0, fail: bool = False) -> None:
        self.replies = list(replies)
        self.first_token_delay = first_token_delay
        self.token_delay = token_delay
        self.fail = fail
        self.seen: list[list[dict]] = []

    async def stream_reply(self, history: list[dict]):
        self.seen.append([dict(m) for m in history])
        if self.fail:
            raise RuntimeError("LLM provider is down")
        text = self.replies.pop(0) if self.replies else "Okay."
        await asyncio.sleep(self.first_token_delay)
        for token in re.findall(r"\S+\s*", text):
            await asyncio.sleep(self.token_delay)
            yield token


class FakeTTS:
    """Yields mu-law 'audio' whose length is proportional to the text,
    roughly the pace of real speech (~15 characters per second)."""

    def __init__(self, chunk_delay: float = 0.0, ms_per_char: int = 0, chunks: int = 2) -> None:
        self.chunk_delay = chunk_delay
        self.ms_per_char = ms_per_char
        self.chunks = chunks
        self.calls: list[str] = []

    async def synthesize(self, text: str):
        self.calls.append(text)
        if self.ms_per_char:
            total = len(text) * self.ms_per_char * 8  # bytes at 8 kHz
            t = np.arange(total) / 8000
            tone = (6000 * np.sin(2 * np.pi * 220 * t)).astype(np.int16)
            from app.audio import pcm16_to_ulaw

            audio = pcm16_to_ulaw(tone)
            for i in range(0, len(audio), 1600):
                await asyncio.sleep(self.chunk_delay)
                yield audio[i : i + 1600]
        else:
            for _ in range(self.chunks):
                await asyncio.sleep(self.chunk_delay)
                yield b"\x7f" * 160

    async def aclose(self) -> None:
        pass
