"""Webhook signatures, stream tokens, /call checks, and untrusted call SIDs.
Runs the real FastAPI app in-process with offline fakes."""

import json
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from app.main import create_app
from app.session import CallSession, SessionDeps
from app.telephony import twilio_signature
from app.vad import EnergyVAD
from tests.fakes import FakeSTT, FakeTransport, FakeTTS, ScriptedAgent, make_settings


def make_client(**settings) -> TestClient:
    defaults = dict(
        greeting="Hi.",
        public_host="example.com",
        twilio_auth_token="12345",
        demo_api_token="demo",
        media_stream_token="tok",
    )
    deps = SessionDeps(
        settings=make_settings(**{**defaults, **settings}),
        stt_factory=FakeSTT,
        agent=ScriptedAgent(["Okay."]),
        tts=FakeTTS(),
        vad_factory=lambda: EnergyVAD(-35),
    )
    return TestClient(create_app(deps))


def start_msg(token=None):
    start = {"streamSid": "MZ1", "callSid": "CA1"}
    if token is not None:
        start["customParameters"] = {"token": token}
    return json.dumps({"event": "start", "streamSid": "MZ1", "start": start})


# ---------------------------------------------------------------- Twilio signature


def test_signature_matches_twilio_reference_vector():
    # Same inputs and expected value as Twilio's own request-validator tests.
    params = {
        "CallSid": "CA1234567890ABCDE", "Caller": "+14158675309", "Digits": "1234",
        "From": "+14158675309", "To": "+18005551212",
    }
    sig = twilio_signature("12345", "https://mycompany.com/myapp.php?foo=1&bar=2", list(params.items()))
    assert sig == "RSOYDt4T1cUTdK1PDd93/VVr8B8="


def test_voice_rejects_unsigned_and_accepts_signed_requests():
    form = [("CallSid", "CA" + "0" * 32), ("From", "+919876543210")]
    with make_client() as client:
        assert client.post("/voice", data=dict(form)).status_code == 403
        bad = client.post("/voice", data=dict(form), headers={"X-Twilio-Signature": "nope"})
        assert bad.status_code == 403

        sig = twilio_signature("12345", "https://example.com/voice", form)
        ok = client.post(
            "/voice", content=urlencode(form),
            headers={"X-Twilio-Signature": sig, "Content-Type": "application/x-www-form-urlencoded"},
        )
        assert ok.status_code == 200
        assert 'url="wss://example.com/media-stream"' in ok.text
        assert '<Parameter name="token" value="tok" />' in ok.text


def test_voice_without_auth_token_is_open_for_local_dev():
    with make_client(twilio_auth_token="") as client:
        assert client.post("/voice").status_code == 200


# ---------------------------------------------------------------- /call


def test_call_checks_token_and_number_before_touching_twilio():
    with make_client() as client:
        assert client.post("/call", json={"to": "+919876543210"}).status_code == 401
        assert client.post("/call", json={"to": "+919876543210"}, headers={"X-Demo-Token": "wrong"}).status_code == 401
        assert client.post("/call", json={"to": "9876"}, headers={"X-Demo-Token": "demo"}).status_code == 422
    with make_client(demo_api_token="") as client:  # unset token never matches
        assert client.post("/call", json={"to": "+919876543210"}, headers={"X-Demo-Token": ""}).status_code == 401


# ---------------------------------------------------------------- /media-stream


def test_media_stream_rejects_missing_or_wrong_token():
    with make_client() as client:
        for token in (None, "wrong"):
            with client.websocket_connect("/media-stream") as ws:
                ws.send_text(json.dumps({"event": "connected"}))
                ws.send_text(start_msg(token))
                assert ws.receive_json() == {"event": "error", "message": "unauthorized"}


def test_media_stream_with_token_starts_call_and_skips_malformed_json():
    with make_client() as client:
        with client.websocket_connect("/media-stream") as ws:
            ws.send_text("this is not json")  # ignored, call goes on
            ws.send_text(start_msg("tok"))
            assert ws.receive_json()["event"] == "media"  # greeting audio
            ws.send_text("{broken")
            ws.send_text(json.dumps({"event": "stop", "streamSid": "MZ1"}))


def test_browser_stream_needs_demo_token_and_accepts_typed_turns():
    with make_client() as client:
        with client.websocket_connect("/browser-stream") as ws:
            ws.send_text(start_msg("tok"))  # the Twilio stream token is not the browser's token
            assert ws.receive_json()["event"] == "error"
        with client.websocket_connect("/browser-stream") as ws:
            ws.send_text(start_msg("demo"))
            ws.send_text(json.dumps({"event": "ui_text", "text": "where is my order"}))
            seen = []
            while not any(m.get("event") == "ui_transcript" and m["entry"]["role"] == "user" for m in seen):
                seen.append(ws.receive_json())
            ws.send_text(json.dumps({"event": "stop", "streamSid": "MZ1"}))


# ---------------------------------------------------------------- untrusted call SID


async def test_call_sid_cannot_escape_transcript_dir(tmp_path):
    out = tmp_path / "transcripts"
    deps = SessionDeps(
        settings=make_settings(greeting=""),
        stt_factory=FakeSTT,
        agent=ScriptedAgent(["Okay."]),
        tts=FakeTTS(),
        vad_factory=lambda: EnergyVAD(-35),
        transcript_dir=str(out),
    )
    s = CallSession(FakeTransport(), deps)
    await s.handle_event({"event": "start", "start": {"streamSid": "MZ1", "callSid": "../../evil"}})
    await s.on_transcript("hello", True, True)
    await s.response_task
    await s.close()
    assert [p.name for p in out.iterdir()] == ["______evil.json"]  # "../../" became underscores
    assert not (tmp_path / "evil.json").exists()
