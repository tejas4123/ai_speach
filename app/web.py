"""Browser test phone: talk to the agent from a web page instead of a real call.

The page (static/index.html) speaks the same protocol as Twilio Media Streams:
it sends 20 ms mu-law frames, plays the bot's audio, echoes marks once the
audio before them has played, and drops queued audio on `clear`. So the
CallSession behind /browser-stream is exactly the one a phone call gets.
Its `start` message must carry DEMO_API_TOKEN, like POST /call.

Two extras, handled here so CallSession stays untouched:
* `ui_text` events from the page are fed to the session as a finished caller
  turn, so you can type instead of speak (skips STT, not the rest).
* A watcher pushes new transcript entries and speaking state to the page as
  `ui_*` events. Those only ever go to the browser, never to Twilio.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse

from .session import CallSession
from .stream import WebSocketTransport, serve_stream

STATIC_DIR = Path(__file__).resolve().parent / "static"


def register_web_ui(app: FastAPI) -> None:
    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.websocket("/browser-stream")
    async def browser_stream(ws: WebSocket) -> None:
        await serve_stream(
            ws,
            app.state.deps,
            app.state.deps.settings.demo_api_token,
            handle_extra=_typed_turn,
            on_session=lambda session, transport: asyncio.create_task(_watch(session, transport)),
        )


async def _typed_turn(session: CallSession, msg: dict) -> bool:
    if msg.get("event") != "ui_text":
        return False
    text = str(msg.get("text", "")).strip()
    if text:
        await session.on_transcript(text, True, True)
    return True


async def _watch(session: CallSession, transport: WebSocketTransport) -> None:
    """Mirror the session's transcript and state to the page every 100 ms."""
    seen = 0
    last_state = None
    while True:
        await asyncio.sleep(0.1)
        while seen < len(session.transcript):
            await transport.send_json({"event": "ui_transcript", "entry": session.transcript[seen]})
            seen += 1
        state = (session.turn_no, session.bot_speaking)
        if state != last_state:
            last_state = state
            await transport.send_json({"event": "ui_state", "turn": state[0], "bot_speaking": state[1]})
