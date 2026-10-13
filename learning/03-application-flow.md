# 03 · Application Flow

## 1. Startup (verified by reading; diagram: [application-startup.mmd](diagrams/application-startup.mmd))

Command (README / Dockerfile): `uvicorn app.main:app --port 8000`

| # | What happens | Where |
|---|---|---|
| 1 | Uvicorn imports `app.main`. That imports `app.config`, whose **module body runs `load_dotenv()`** — `.env` values enter `os.environ` (existing env vars win). | `config.py:10` |
| 2 | `logging.basicConfig(INFO)` configured. | `main.py:35` |
| 3 | `app = create_app()` runs at import with `deps=None`. It only defines routes and a `lifespan` — **no network calls yet**. | `main.py:76-147` |
| 4 | Uvicorn starts the lifespan: `app.state.deps = deps or await build_deps(Settings.from_env())`. | `main.py:79` |
| 5 | `Settings.from_env()` reads 28 env vars with defaults into a frozen dataclass. Invalid ints/floats raise `ValueError` here → startup fails. | `config.py:81-115` |
| 6 | `build_deps` → `factory.build_agent(settings)`: raises `RuntimeError` if `LLM_API_KEY` empty; creates `AsyncOpenAI`; optional `EmbeddingIndex`; `KnowledgeBase.from_directory(data/kb)` (reads + tokenizes markdown, builds BM25); `await kb.build_embeddings()` (**network call only if `EMBEDDINGS_ENABLED=true`**); `build_default_tools(kb, data/orders.json)` (reads JSON); returns `Agent`. | `factory.py:16-26` |
| 7 | Choose TTS: `ElevenLabsTTS` if `TTS_PROVIDER=elevenlabs` else `DeepgramTTS`. Constructor raises if the key is empty. Creates one shared `httpx.AsyncClient`. | `main.py:54-57`, `tts.py` |
| 8 | Build `SessionDeps` with **lambdas** for STT and VAD (nothing connects to Deepgram until a call starts), a `LatencyLogger` (creates `logs/`), and `transcript_dir`. | `main.py:59-73` |
| 9 | Log `ready: llm=… tts=… stt=…/… vad=…`. App serves requests. | `main.py:80-87` |

**Fail-fast keys:** `LLM_API_KEY`, and the TTS key. **Lazy key:** `DEEPGRAM_API_KEY` for STT is only checked when `DeepgramSTT(...)` is constructed — i.e. when the first `/media-stream` WebSocket opens (`stt.py:62`). With `TTS_PROVIDER=deepgram` the same key is checked at startup anyway.

## 2. Request lifecycle — the core loop (diagram: [request-lifecycle.mmd](diagrams/request-lifecycle.mmd))

### 2a. Call setup
1. Twilio → `POST /voice` → `voice()` returns TwiML `<Connect><Stream url="wss://{PUBLIC_HOST or Host}/media-stream"/></Connect>` (`main.py:93-108`).
2. Twilio opens `WS /media-stream` → `media_stream()` accepts, creates `CallSession(WebSocketTransport(ws), deps)` (`main.py:131-134`). The constructor calls `stt_factory()` and `vad_factory()`.
3. Twilio sends `{"event":"connected"}` (ignored by `handle_event`) then `{"event":"start", ...}` → `_on_start`: stores `streamSid`, `callSid`, **connects to Deepgram** (`stt.start(self)`), then if `greeting` is set calls `_begin_turn(text_source=_static_text(greeting))` (`session.py:110-117`). The greeting therefore goes through the same pipeline as an LLM reply, just with a fake one-item text stream.

### 2b. Every 20 ms: `media` event
`_on_media` (`session.py:119-126`): base64-decode → `stt.send(audio)` always → if `bot_speaking`, decode to PCM and feed `SpeechOnsetDetector`; if it fires → `barge_in("vad")`.

### 2c. Deepgram replies (in the STT receive task)
`on_transcript(text, is_final, speech_final)` (`session.py:142-148`):
- interim or final text with ≥ `BARGE_IN_MIN_WORDS` words while `bot_speaking` → `barge_in("stt")`
- `is_final` → append to `user_parts`
- `speech_final` (or later `on_utterance_end`) and there are parts → `_end_user_turn()`

### 2d. `_end_user_turn` (`session.py:159-186`)
join parts → `redact()` → transcript gets redacted text → decide:
- **merge** (response in flight, nothing sent, bot silent, last history msg is user) → cancel, pop previous user text, prepend it;
- else if something in flight or bot speaking → `barge_in("new_turn")`;
- append `{"role":"user"}` to history → `_begin_turn()`.

### 2e. `_begin_turn` → `_respond` (`session.py:188-247`)
`turn_no += 1`, new `TurnTimer`, reset `sent`/`played`, `turn_finalized=False`, spawn `response_task = _respond(...)`.
`_respond` runs `_produce` (agent → chunker → queue) and `_consume` (queue → `_play_sentence` → TTS → `_send_audio` + `_send_mark("N:i")`) in a `TaskGroup`; after the last sentence `_consume` sends `"N:end"`.

### 2f. Twilio echoes marks
`_on_mark` (`session.py:128-138`): ignore marks of older turns; `"N:i"` → `played = max(played, i+1)`; `"N:end"` → `bot_speaking=False`, reset VAD, `_finalize_turn(interrupted=False)` which appends the assistant message to history and writes the latency row.

## 3. Shutdown

**Per call:** Twilio sends `stop` → `close()`; or the socket drops → `WebSocketDisconnect` → `finally: session.close()` (`main.py:139-142`). `close()` (`session.py:291-303`) is idempotent: cancel response task, `_finalize_turn(interrupted=self.bot_speaking)`, `stt.close()` (sends `CloseStream`, closes socket, cancels tasks), `_save_transcript()` to `logs/transcripts/{call_sid}.json`.

**Process:** lifespan exit → `await tts.aclose()` (closes shared httpx client). Note: there's no explicit draining of in-progress calls; uvicorn handles WebSocket shutdown.

## 4. Other entry points

| Entry | What it runs |
|---|---|
| `python scripts/chat_cli.py` | `build_agent()` + a text REPL; redacts input; shows TTFT and chunking. **Calls the real LLM.** |
| `python scripts/simulate_call.py` | A fake Twilio WebSocket client against a running server. |
| `python tests/fake_server.py --port --log-dir` | Real `create_app(deps)` with offline fakes (used by `test_e2e.py`). |
| `python scripts/make_test_audio.py "text" out.wav` | **Calls Deepgram TTS** to make caller WAVs. |
| `python scripts/latency_report.py` | Reads `logs/latency.jsonl`, prints p50/p95 (skips turn 1 = greeting). |
