"""Streaming text-to-speech.

Both providers can return 8 kHz mu-law directly, which is exactly what Twilio
plays, so there's no transcoding step and we can forward bytes the moment
they arrive. We also reuse one HTTP client (keep-alive) so each sentence
doesn't pay for a fresh TCP + TLS handshake (~100-300 ms from India).
"""

from __future__ import annotations

import logging
from typing import AsyncIterator, Protocol

import httpx

log = logging.getLogger(__name__)


class TTS(Protocol):
    def synthesize(self, text: str) -> AsyncIterator[bytes]: ...

    async def aclose(self) -> None: ...


class DeepgramTTS:
    URL = "https://api.deepgram.com/v1/speak"

    def __init__(self, api_key: str, model: str, client: httpx.AsyncClient | None = None) -> None:
        if not api_key:
            raise RuntimeError("DEEPGRAM_API_KEY is not set")
        self.api_key = api_key
        self.model = model
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=30.0))

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        params = {"model": self.model, "encoding": "mulaw", "sample_rate": 8000, "container": "none"}
        headers = {"Authorization": f"Token {self.api_key}", "Content-Type": "application/json"}
        async with self.client.stream(
            "POST", self.URL, params=params, headers=headers, json={"text": text}
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise RuntimeError(f"Deepgram TTS {resp.status_code}: {body[:300]!r}")
            async for chunk in resp.aiter_bytes():
                if chunk:
                    yield chunk

    async def aclose(self) -> None:
        await self.client.aclose()


class ElevenLabsTTS:
    """ElevenLabs streaming endpoint. eleven_flash_v2_5 is their low-latency,
    multilingual model (it also speaks Hindi), which matters for Indian callers."""

    URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"

    def __init__(
        self, api_key: str, voice_id: str, model: str, client: httpx.AsyncClient | None = None
    ) -> None:
        if not api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is not set")
        self.api_key = api_key
        self.voice_id = voice_id
        self.model = model
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=30.0))

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        url = self.URL.format(voice_id=self.voice_id)
        headers = {"xi-api-key": self.api_key, "Content-Type": "application/json"}
        payload = {"text": text, "model_id": self.model}
        async with self.client.stream(
            "POST", url, params={"output_format": "ulaw_8000"}, headers=headers, json=payload
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise RuntimeError(f"ElevenLabs TTS {resp.status_code}: {body[:300]!r}")
            async for chunk in resp.aiter_bytes():
                if chunk:
                    yield chunk

    async def aclose(self) -> None:
        await self.client.aclose()
