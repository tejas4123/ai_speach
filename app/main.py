"""FastAPI entry point.

Endpoints:
  POST /voice         Twilio webhook for incoming calls. Checks X-Twilio-Signature,
                      returns TwiML that opens a Media Stream to /media-stream.
  POST /call          Have the bot call a phone number (handy from India:
                      Twilio dials your verified mobile, so you don't pay
                      for an international call). Needs X-Demo-Token.
  WS   /media-stream  Twilio Media Streams WebSocket, one CallSession per call.
                      Only accepted with the stream token from our TwiML.
  GET  /health        Liveness check.
  GET  /, WS /browser-stream   Browser test phone (app/web.py).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .config import Settings
from .factory import build_agent
from .latency import LatencyLogger
from .session import SessionDeps
from .stream import serve_stream
from .stt import DeepgramSTT
from .telephony import E164, TwilioClient, signature_valid, stream_twiml, tokens_match
from .tts import DeepgramTTS, ElevenLabsTTS
from .vad import build_vad
from .web import register_web_ui

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("voice-agent")


# Module level on purpose: with `from __future__ import annotations`, FastAPI
# can't resolve a model defined inside create_app() and treats it as a query param.
class CallRequest(BaseModel):
    to: str = Field(pattern=E164.pattern, description="E.164 number, e.g. +919876543210")


async def build_deps(settings: Settings) -> SessionDeps:
    agent = await build_agent(settings)

    if settings.tts_provider == "elevenlabs":
        tts = ElevenLabsTTS(settings.elevenlabs_api_key, settings.elevenlabs_voice_id, settings.elevenlabs_model)
    else:
        tts = DeepgramTTS(settings.deepgram_api_key, settings.deepgram_tts_model)

    return SessionDeps(
        settings=settings,
        stt_factory=lambda: DeepgramSTT(
            settings.deepgram_api_key,
            model=settings.stt_model,
            language=settings.stt_language,
            endpointing_ms=settings.endpointing_ms,
            utterance_end_ms=settings.utterance_end_ms,
        ),
        agent=agent,
        tts=tts,
        vad_factory=lambda: build_vad(settings.vad_provider, settings.vad_energy_threshold_dbfs),
        latency_logger=LatencyLogger(settings.log_dir),
        transcript_dir=str(Path(settings.log_dir) / "transcripts"),
    )


def create_app(deps: SessionDeps | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.deps = deps or await build_deps(Settings.from_env())
        s = app.state.deps.settings
        log.info(
            "ready: llm=%s tts=%s stt=%s/%s vad=%s",
            s.llm_model, s.tts_provider, s.stt_model, s.stt_language, s.vad_provider,
        )
        if not s.twilio_auth_token:
            log.warning("TWILIO_AUTH_TOKEN is not set: /voice accepts unsigned requests")
        yield
        await app.state.deps.tts.aclose()

    app = FastAPI(title="Realtime Voice Agent", lifespan=lifespan)

    def settings() -> Settings:
        return app.state.deps.settings

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.api_route("/voice", methods=["GET", "POST"])
    async def voice(request: Request) -> Response:
        s = settings()
        if s.twilio_auth_token:
            # Twilio signs the public URL it called, which differs from
            # request.url behind ngrok or a load balancer.
            url = f"https://{s.public_host}{request.url.path}" if s.public_host else str(request.url)
            if request.url.query:
                url += f"?{request.url.query}"
            body = (await request.body()).decode() if request.method == "POST" else ""
            params = parse_qsl(body, keep_blank_values=True)
            if not signature_valid(s.twilio_auth_token, url, params, request.headers.get("x-twilio-signature", "")):
                log.warning("rejected /voice: bad or missing X-Twilio-Signature")
                raise HTTPException(status_code=403, detail="invalid Twilio signature")
        host = s.public_host or request.headers.get("host", "")
        return Response(content=stream_twiml(host, s.media_stream_token), media_type="application/xml")

    @app.post("/call")
    async def outbound_call(body: CallRequest, x_demo_token: str = Header(default="")) -> dict:
        s = settings()
        if not tokens_match(s.demo_api_token, x_demo_token):
            raise HTTPException(status_code=401, detail="set DEMO_API_TOKEN and send it as X-Demo-Token")
        twilio = TwilioClient(s.twilio_account_sid, s.twilio_auth_token, s.twilio_phone_number)
        if not twilio.configured:
            raise HTTPException(status_code=500, detail="Twilio credentials are not configured")
        if not s.public_host:
            raise HTTPException(status_code=500, detail="PUBLIC_HOST is not set, so Twilio can't reach /media-stream")
        resp = await twilio.create_call(body.to, stream_twiml(s.public_host, s.media_stream_token))
        if resp.status_code >= 300:
            raise HTTPException(status_code=502, detail=resp.text[:500])
        data = resp.json()
        return {"call_sid": data.get("sid"), "status": data.get("status")}

    @app.websocket("/media-stream")
    async def media_stream(ws: WebSocket) -> None:
        await serve_stream(ws, app.state.deps, settings().media_stream_token)

    register_web_ui(app)
    return app


app = create_app()
