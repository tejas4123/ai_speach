"""Streaming speech-to-text over Deepgram's live WebSocket API.

We send raw 8 kHz mu-law straight from Twilio (no transcoding needed) and
receive JSON events:

* Results with is_final=false: interim guess, changes as more audio arrives.
  Useful for barge-in ("the caller is saying something").
* Results with is_final=true: this segment's text is locked in.
* speech_final=true on a final result: Deepgram's endpointing saw
  `endpointing` ms of silence, so the caller has probably finished the turn.
* UtteranceEnd: a word-timing based fallback that fires after
  `utterance_end_ms` with no new words, even in noisy audio where the
  silence-based endpointing never triggers.

If the socket drops mid-call we reconnect with backoff (audio sent while
reconnecting is lost), so one network blip doesn't leave the bot deaf.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import ssl
from typing import Protocol
from urllib.parse import urlencode

import certifi
import websockets

# python.org builds on macOS ship without root certs; httpx uses certifi, so match it.
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

log = logging.getLogger(__name__)


class TranscriptHandler(Protocol):
    async def on_transcript(self, text: str, is_final: bool, speech_final: bool) -> None: ...

    async def on_utterance_end(self) -> None: ...

    async def on_speech_started(self) -> None: ...


class STT(Protocol):
    async def start(self, handler: TranscriptHandler) -> None: ...

    async def send(self, audio: bytes) -> None: ...

    async def close(self) -> None: ...


class DeepgramSTT:
    URL = "wss://api.deepgram.com/v1/listen"

    def __init__(
        self,
        api_key: str,
        model: str = "nova-2",
        language: str = "en-IN",
        endpointing_ms: int = 300,
        utterance_end_ms: int = 1000,
        url: str = URL,
        max_reconnects: int = 3,
    ) -> None:
        if not api_key:
            raise RuntimeError("DEEPGRAM_API_KEY is not set")
        self.api_key = api_key
        self.params = {
            "encoding": "mulaw",
            "sample_rate": 8000,
            "channels": 1,
            "model": model,
            "language": language,
            "interim_results": "true",
            "endpointing": endpointing_ms,
            "utterance_end_ms": max(utterance_end_ms, 1000),  # Deepgram minimum
            "vad_events": "true",
            "smart_format": "true",
            "punctuate": "true",
        }
        self.base_url = url
        self.max_reconnects = max_reconnects
        self._ws = None
        self._tasks: list[asyncio.Task] = []
        self._closed = False
        self._handler: TranscriptHandler | None = None
        self.reconnects = 0
        self._drop_logged = False

    @property
    def url(self) -> str:
        return f"{self.base_url}?{urlencode(self.params)}"

    async def start(self, handler: TranscriptHandler) -> None:
        self._handler = handler
        await self._connect()
        self._tasks = [
            asyncio.create_task(self._run()),
            asyncio.create_task(self._keepalive()),
        ]

    async def _connect(self) -> None:
        self._ws = await websockets.connect(
            self.url,
            additional_headers={"Authorization": f"Token {self.api_key}"},
            max_size=None,
            ssl=SSL_CONTEXT if self.url.startswith("wss:") else None,
        )
        self._drop_logged = False
        log.info("deepgram connected model=%s lang=%s", self.params["model"], self.params["language"])

    async def _run(self) -> None:
        """Receive until closed; reconnect if Deepgram drops us mid-call."""
        failures = 0
        while True:
            if await self._receive(self._handler):
                failures = 0  # that connection worked; start the budget over
            if self._closed:
                return
            failures += 1
            if failures > self.max_reconnects:
                log.error("deepgram: giving up after %d reconnect attempts; caller audio is no longer transcribed", self.max_reconnects)
                return
            await asyncio.sleep(min(0.25 * 2 ** failures, 2.0))
            try:
                await self._connect()
                self.reconnects += 1
            except Exception as exc:
                log.warning("deepgram reconnect %d failed: %r", failures, exc)

    async def _receive(self, handler: TranscriptHandler) -> bool:
        """Returns True if at least one message arrived on this connection."""
        got_any = False
        if self._ws is None:
            return got_any
        try:
            async for raw in self._ws:
                got_any = True
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "Results":
                    alternatives = msg.get("channel", {}).get("alternatives") or [{}]
                    text = (alternatives[0].get("transcript") or "").strip()
                    await handler.on_transcript(
                        text, bool(msg.get("is_final")), bool(msg.get("speech_final"))
                    )
                elif kind == "UtteranceEnd":
                    await handler.on_utterance_end()
                elif kind == "SpeechStarted":
                    await handler.on_speech_started()
                elif kind == "Error" or "err_msg" in msg:
                    log.error("deepgram error: %s", msg)
        except websockets.ConnectionClosed as exc:
            if not self._closed:
                log.warning("deepgram connection closed: %s", exc)
        except Exception:  # keep the call alive; log loudly
            log.exception("deepgram receive loop crashed")
        if not self._closed:
            log.warning("deepgram stream ended unexpectedly; reconnecting")
        return got_any

    async def _keepalive(self) -> None:
        # Deepgram closes the socket after ~10 s without audio or a KeepAlive.
        while True:
            await asyncio.sleep(5)
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps({"type": "KeepAlive"}))

    async def send(self, audio: bytes) -> None:
        if self._ws is None or self._closed:
            return
        try:
            await self._ws.send(audio)
        except websockets.ConnectionClosed:
            if not self._drop_logged:  # 50 frames a second: say it once per outage
                self._drop_logged = True
                log.warning("dropping audio: deepgram socket closed")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps({"type": "CloseStream"}))
            with contextlib.suppress(Exception):
                await self._ws.close()
        for task in self._tasks:
            task.cancel()
