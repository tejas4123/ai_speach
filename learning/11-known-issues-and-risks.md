# 11 · Known Issues and Risks

Updated 2026-10-14 after the "fix all the issues" pass. Each item keeps its original ID so you can follow what changed. Verified = read in code + covered by a passing test (60/60 pass).

## Status at a glance

| ID | Issue | Status | Where / test |
|---|---|---|---|
| **B1** | `POST /call` **never worked**: `CallRequest` was defined inside `create_app()` with `from __future__ import annotations`, so FastAPI read `body` as a query param → every request 422, and `/docs` crashed | ✅ Fixed (found by the new tests) | `main.py` module-level `CallRequest`; `test_security.py::test_call_checks_token_and_number_before_touching_twilio` |
| S1 | Path traversal via `callSid` in transcript filename | ✅ Fixed | `session._save_transcript` sanitises; `test_call_sid_cannot_escape_transcript_dir` |
| S2 | `/media-stream` unauthenticated | ✅ Fixed | `stream.serve_stream` requires `start.customParameters.token`; TwiML carries it (`telephony.stream_twiml`); tests `test_media_stream_*` |
| S3 | `/voice` didn't check `X-Twilio-Signature` | ✅ Fixed (when `TWILIO_AUTH_TOKEN` is set; startup warns otherwise) | `telephony.signature_valid`; verified against Twilio's reference vector |
| S4 | `/call` token compared with `!=`; no number validation | ✅ Fixed (`hmac.compare_digest`, E.164 pattern) · ⚠️ still no rate limit | `telephony.tokens_match` |
| S5 | `/call` built the stream URL from the caller's `Host` header | ✅ Fixed: `/call` now requires `PUBLIC_HOST` | `main.outbound_call` |
| S6 | PII spoken as words not redacted | ✅ Fixed for card/Aadhaar/phone spoken as digits ("double nine…") · ⚠️ assistant text and tool results still not redacted (tool data is fake) | `pii.redact`; `test_redacts_numbers_spoken_as_words` |
| new | Browser phone `/browser-stream` was open to anyone | ✅ Requires `DEMO_API_TOKEN` | `web.py`; `test_browser_stream_needs_demo_token_and_accepts_typed_turns` |
| R0 | **OpenAI account has no credits** | ❌ **Not fixable in code.** Add billing, or switch to Groq (see below) | live run log |
| R1 | STT dropped → bot deaf | ✅ Reconnects up to 3× with backoff | `stt._run`; `test_stt_reconnects_after_the_socket_drops` |
| R2 | Fallback line promised a transfer that didn't happen | ✅ Honest wording; `transfer_to_human` now really dials `HUMAN_AGENT_NUMBER` via Twilio (or says no human is available) | `factory.build_transfer`, `telephony.TwilioClient.transfer`; transfer tests |
| R3 | No LLM / tool timeouts | ✅ `LLM_TIMEOUT_S` (15 s), `max_retries=1`; tools time out after 8 s | `factory.build_agent`, `tools.ToolRegistry.call`; `test_slow_tool_times_out_*` |
| R4 | Blocking file I/O | ✅ Transcript save moved to a thread · ⚠️ latency row stays synchronous on purpose (one ~200-byte append; offloading it would risk out-of-order rows) | `session.close` |
| R5 | Double `clear` on simultaneous barge-ins | ✅ Guard on `task.cancelling()` | `test_simultaneous_barge_ins_send_one_clear` |
| R6 | Malformed JSON ended the call | ✅ Skipped with a warning | `stream.serve_stream`; `test_media_stream_with_token_starts_call_and_skips_malformed_json` |
| R7 | Dependency drift | ✅ Upper bounds on major versions in `requirements.txt` · ⚠️ no lockfile; Docker uses 3.12, venv 3.14 | `requirements.txt` |
| N1 | TTFT measured the filler | ✅ New `filler_ms`; `llm_ttft_ms` = real model text | `session._produce`, `latency.py`; `test_latency_does_not_count_the_filler_as_first_token` |
| N3 | Unbounded history | ✅ `MAX_HISTORY_MESSAGES` (40), trimmed at a user message so tool pairs stay intact | `agent.recent`; `test_recent_keeps_whole_turns` |
| N4 | `stream_reply` mutates history | ↔ Kept (intentional, documented) | — |
| N5 | Dead code `silence_ulaw` | ✅ Removed | — |
| N6 | `GREETING`, `CALLER_TTS_MODEL` undocumented | ✅ In `.env.example` (plus new vars) | — |
| N7 | Not a git repo | ✅ `git init` done (nothing committed; `.env`, logs, samples, interview notes are ignored) | — |
| N8 | Single process, in-memory call state | ↔ Architectural; set `MEDIA_STREAM_TOKEN` when running several instances. Scaling steps in README | — |

## Fixing R0 (your action)
Either add credit at platform.openai.com → Billing, or use Groq's free tier in `.env`:
```
LLM_API_KEY=<your Groq key>
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=llama-3.3-70b-versatile
```
(Embeddings don't work on Groq; keep `EMBEDDINGS_ENABLED=false`.)

## Behaviour changes you need to know
- **`PUBLIC_HOST` must match your ngrok host exactly**: the signature check rebuilds `https://PUBLIC_HOST/voice`. If it's stale, Twilio calls get **403**.
- **`scripts/simulate_call.py` needs the stream token.** Set `MEDIA_STREAM_TOKEN` in `.env` (the simulator reads it), or pass `--token`. Without it the server picks a random token per run and refuses the simulator.
- **Browser phone** needs your `DEMO_API_TOKEN` in its token box.

## Still open / needs investigation
- Twilio's exact mark behaviour after `clear` (simulator assumption, `simulate_call.py:105-107`).
- The live transfer (`<Say>` + `<Dial>` via Calls API) is unit-tested only with fakes. Try it on a real call with `HUMAN_AGENT_NUMBER` set to a second phone.
- Silero VAD path untested; Deepgram `nova-3` + `multi` behaviour.
- Pre-existing FastAPI warning: duplicate operation ID for `/voice` (GET+POST on one handler). Cosmetic.
