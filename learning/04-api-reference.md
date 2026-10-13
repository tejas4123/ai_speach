# 04 · API Reference

All routes are defined inside `create_app()` in `app/main.py`. There is no router module, no middleware, no versioning. FastAPI's automatic `/docs` and `/openapi.json` are also enabled (default FastAPI behavior, not configured explicitly).

---

## `GET /health`
- **Code:** `main.py:101-103`
- **Auth:** none
- **Response 200:** `{"status": "ok"}`
- **Notes:** Pure liveness — doesn't check Deepgram/LLM reachability. Used by `test_e2e.py` to wait for the server.

---

## `GET|POST /voice` — Twilio inbound-call webhook
- **Code:** `main.py:105-108`, helper `stream_twiml()` `main.py:93-99`
- **Auth:** `X-Twilio-Signature` must be valid for `https://PUBLIC_HOST/voice` + the form params (`telephony.signature_valid`). Skipped (with a startup warning) only if `TWILIO_AUTH_TOKEN` is empty. Bad/missing signature → **403**.
- **Request:** whatever Twilio posts (form fields like `CallSid`, `From`…) — **ignored**.
- **Response 200** `application/xml`:
  ```xml
  <?xml version="1.0" encoding="UTF-8"?><Response><Connect><Stream url="wss://HOST/media-stream"><Parameter name="token" value="MEDIA_STREAM_TOKEN" /></Stream></Connect></Response>
  ```
  `HOST` = `PUBLIC_HOST` setting, or the request's `Host` header if empty. `quoteattr` escapes it for XML.
- **Errors:** none explicit.
- **Flow:** [diagrams/feature-inbound-call.mmd](diagrams/feature-inbound-call.mmd)

---

## `POST /call` — make the bot dial a number
- **Code:** `main.py:110-129`
- **Auth:** header `X-Demo-Token` must equal `DEMO_API_TOKEN` (and that setting must be non-empty).
- **Request body (Pydantic `CallRequest`, module level):** `{"to": "+919876543210"}` — must match E.164 (`^\+[1-9]\d{6,14}$`) else **422**. ⚠️ Before 2026-10-14 this model sat inside `create_app()` and the endpoint always returned 422 (see 11-known-issues B1).
- **Steps:**
  1. Token check (constant-time, `telephony.tokens_match`) → else **401** `"set DEMO_API_TOKEN and send it as X-Demo-Token"`.
  2. Twilio creds present? → else **500**. `PUBLIC_HOST` set? → else **500** (no more falling back to the request's Host header).
  3. `httpx` POST to `https://api.twilio.com/2010-04-01/Accounts/{SID}/Calls.json` with basic auth (SID, auth token) and form `To`, `From=TWILIO_PHONE_NUMBER`, `Twiml=<same TwiML as /voice>` (timeout 15 s).
  4. Twilio status ≥ 300 → **502** with first 500 chars of Twilio's body.
- **Response 200:** `{"call_sid": "...", "status": "queued"}` (values from Twilio).
- **Other errors:** missing/invalid JSON body → **422** (FastAPI validation). Network failure to Twilio → `httpx` exception → **500** (unhandled).
- **External side effect:** a real, billable phone call. ⚠️ Never run in an exercise without explicit intent.
- **Flow:** [diagrams/feature-outbound-call.mmd](diagrams/feature-outbound-call.mmd), auth: [diagrams/authentication-flow.mmd](diagrams/authentication-flow.mmd)

---

## `WS /media-stream` — Twilio Media Streams
- **Code:** `main.media_stream` → `stream.serve_stream` → `CallSession.handle_event`
- **Auth:** the first non-`connected` message must be `start` with `customParameters.token == MEDIA_STREAM_TOKEN`; otherwise the server sends `{"event":"error","message":"unauthorized"}` and closes with code 1008. The `CallSession` is only created after this check.
- **Protocol:** text frames of JSON. Inbound events handled:

| Event | Handler | Effect |
|---|---|---|
| `connected` | — | ignored |
| `start` | `_on_start` | store `streamSid`/`callSid`, connect STT, play greeting |
| `media` | `_on_media` | forward audio to STT; VAD while bot speaks |
| `mark` | `_on_mark` | track played sentences / end of turn |
| `stop` | `close()` | end call, save transcript |
| `dtmf` | log only | keypad digit logged |

- **Outbound frames (server → Twilio):**
  - `{"event":"media","streamSid":…,"media":{"payload":"<base64 mu-law>"}}`
  - `{"event":"mark","streamSid":…,"mark":{"name":"<turn>:<index>|<turn>:end"}}`
  - `{"event":"clear","streamSid":…}` (barge-in: flush Twilio's buffer)
- **Errors:** client disconnect → `WebSocketDisconnect` caught, logged. Malformed JSON → skipped with a warning; the call continues.
- **Flow:** [diagrams/request-lifecycle.mmd](diagrams/request-lifecycle.mmd)

---

## `GET /` and `WS /browser-stream` — browser test phone (added 2026-10-14)
- **Code:** `app/web.py` (`register_web_ui`, called at the end of `create_app`), UI in `app/static/index.html`.
- **Auth:** `start.customParameters.token` must equal `DEMO_API_TOKEN` (the page has a token box).
- `/browser-stream` speaks the Twilio Media Streams protocol, so it drives a normal `CallSession`. Two extras:
  - inbound `{"event":"ui_text","text":"..."}` → `session.on_transcript(text, True, True)` (typed caller turn, skips STT);
  - outbound `ui_transcript` / `ui_state` events from a 100 ms watcher (`web._watch`) that mirrors `session.transcript`, `turn_no`, `bot_speaking`. These never go to Twilio.

---

## Outbound calls the server makes (not endpoints, but "APIs" you depend on)

| Callee | Code | Protocol |
|---|---|---|
| Deepgram Listen | `stt.py:52,86-97` | `wss://api.deepgram.com/v1/listen?encoding=mulaw&sample_rate=8000&…` header `Authorization: Token …` |
| Deepgram Speak | `tts.py:26,35-46` | `POST https://api.deepgram.com/v1/speak?model=…&encoding=mulaw&sample_rate=8000&container=none` streamed |
| ElevenLabs | `tts.py:56,68-80` | `POST /v1/text-to-speech/{voice}/stream?output_format=ulaw_8000` |
| LLM | `agent.py:81` | `client.chat.completions.create(stream=True, tools=…)` |
| Embeddings (optional) | `rag.py:113,122` | `client.embeddings.create` |
| Twilio REST | `main.py:120-126` | `POST Calls.json` |
