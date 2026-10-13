# 08 · Testing Guide

## How to run (verified 2026-10-14: **60 passed in ~28 s**; was 37 before the fixes)

```bash
source .venv/bin/activate
pytest -q
```
Config: `pyproject.toml` → `asyncio_mode = "auto"` (every `async def test_*` runs in an event loop without a decorator), `testpaths = ["tests"]`.

Single file / single test:
```bash
pytest -q tests/test_session.py::test_barge_in_from_vad -s
```

**All tests are offline** — no API keys or network calls (verified: every external dependency is faked). `make_settings()` still calls `Settings.from_env()` so your `.env` is *read*, but its keys are never used by fakes.

## What exists

Added 2026-10-14: `tests/test_security.py` (8 tests: signatures incl. Twilio's reference vector, `/voice`, `/call`, stream tokens, browser stream, call-SID sanitising, via FastAPI `TestClient`) and `tests/test_fixes.py` (15 tests: STT reconnect against a local fake Deepgram WebSocket server, tool timeout, transfer, history trimming, spoken-number PII, single `clear`, filler-aware latency).


| File | Count | Covers |
|---|---|---|
| `tests/test_units.py` | 26 (incl. parametrized) | mu-law known values & round trip; resample; VAD onset; chunker (sentences, abbreviations/decimals, comma first-chunk, Hindi danda); markdown cleaning; PII (7 cases + negatives + Luhn); BM25 picks right section; RRF; order-ID normalization (4 cases); tool registry errors; agent tool loop with fragmented streamed arguments |
| `tests/test_session.py` | 10 | greeting + marks; UtteranceEnd; barge-in via STT keeps only heard sentences; one word doesn't barge in; VAD barge-in threshold; early-endpoint merge; new turn interrupts; PII before LLM/storage; LLM failure → fallback; stop → STT closed + transcript saved |
| `tests/test_e2e.py` | 1 | Spawns `tests/fake_server.py` (real FastAPI + CallSession with fakes) and runs `scripts/simulate_call.py` over real WebSockets; asserts 1 barge-in, 3 latency prints, transcript + ≥4 latency rows |

## Fakes (`tests/fakes.py`) — learn these, they explain the Protocols

- `FakeTransport` records every `send_json`; helpers `events(kind)`, `mark_names()`.
- `FakeSTT` — tests call `session.on_transcript(...)` **directly** to simulate Deepgram.
- `EnergySTT` — for e2e: loudness-based fake STT that emits scripted text after 400 ms of silence, and an interim `"hey wait"` after 400 ms of speech (to trigger STT barge-in).
- `ScriptedAgent` — yields scripted replies word by word with delays; `fail=True` raises; records `seen` histories.
- `FakeTTS` — yields fake mu-law (`b"\x7f"*160` chunks, or a 220 Hz tone sized like real speech).

## Testing techniques used (worth copying)

- **`wait_for(predicate)`** polling helper instead of fixed sleeps (`test_session.py:45-50`).
- **Driving the state machine by hand**: send `mark("1:0")` to simulate Twilio playback confirmation.
- **Hand-built OpenAI stream chunks** with `SimpleNamespace` (`test_units.py:163-185`) — tests the agent with no SDK network.
- **Subprocess e2e** with a free port and health polling (`test_e2e.py:18-56`).

## Gaps (not covered by any test — verified by reading)

- `DeepgramSTT` message parsing (`_receive`), keepalive, close.
- `DeepgramTTS` / `ElevenLabsTTS` (incl. non-200 path).
- `/voice` TwiML, `/call` (auth, Twilio errors), `/health` is only used indirectly.
- `SileroVAD`, `EmbeddingIndex`, hybrid search path.
- `Settings.from_env` parsing (bad ints, booleans).
- `transfer_to_human`, `search_knowledge_base` "nothing found" branch.
- Tool-call + barge-in interplay (cancel while tools run), multiple tool rounds hitting `max_tool_rounds`.
- `close()` while bot speaking → `interrupted=True` row.

## How to add a test (pattern)

```python
# tests/test_session.py style
async def test_my_behavior():
    t, s = make_session(agent=ScriptedAgent(["Hello there."]))
    await s.handle_event(START)
    await s.on_transcript("hi", True, True)
    await s.response_task
    assert t.mark_names() == ["1:0", "1:end"]
```
For HTTP routes, use FastAPI's `TestClient(create_app(deps))` with fake deps (not used in the repo yet — would need `httpx`, already installed).
