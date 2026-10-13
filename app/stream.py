"""The WebSocket loop shared by /media-stream (Twilio) and /browser-stream.

Nothing reaches a CallSession until a `start` message carries the right
token in its customParameters, so a stranger who finds the URL can't open a
session and spend STT/LLM/TTS credit. Malformed messages are skipped
instead of ending the call.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Awaitable, Callable

from fastapi import WebSocket, WebSocketDisconnect

from .session import CallSession, SessionDeps
from .telephony import stream_token_ok

log = logging.getLogger(__name__)

# Return True if the message was handled and must not go to the session.
ExtraHandler = Callable[[CallSession, dict], Awaitable[bool]]
# Called once the session exists; may return a background task to cancel at the end.
OnSession = Callable[[CallSession, "WebSocketTransport"], "asyncio.Task | None"]


class WebSocketTransport:
    """Serialises sends: the response task and barge-in can both send at once."""

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self._lock = asyncio.Lock()

    async def send_json(self, data: dict) -> None:
        async with self._lock:
            await self.ws.send_text(json.dumps(data))


async def serve_stream(
    ws: WebSocket,
    deps: SessionDeps,
    expected_token: str,
    handle_extra: ExtraHandler | None = None,
    on_session: OnSession | None = None,
) -> None:
    await ws.accept()
    transport = WebSocketTransport(ws)
    session: CallSession | None = None
    background: asyncio.Task | None = None
    try:
        while session is None or not session.closed:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("ignoring malformed stream message: %.80r", raw)
                continue
            if not isinstance(msg, dict):
                continue

            if session is None:
                event = msg.get("event")
                if event == "connected":
                    continue
                if event != "start" or not stream_token_ok(msg, expected_token):
                    log.warning("rejected stream: missing or wrong token (first event=%s)", event)
                    await transport.send_json({"event": "error", "message": "unauthorized"})
                    await ws.close(code=1008)
                    return
                session = CallSession(transport, deps)
                if on_session is not None:
                    background = on_session(session, transport)

            if handle_extra is not None and await handle_extra(session, msg):
                continue
            await session.handle_event(msg)
    except WebSocketDisconnect:
        log.info("stream disconnected")
    finally:
        if session is not None:
            await session.close()
        if background is not None:
            background.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await background
