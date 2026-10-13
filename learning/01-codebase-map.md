# 01 · Codebase Map

## Directory tree (actual, excluding `.venv`, `__pycache__`, `.pytest_cache`)

```
realtime-voice-agent/
├── app/                      ← the application (1,483 lines)
│   ├── __init__.py           (1)   package docstring only
│   ├── main.py               (147) FastAPI app, HTTP routes, WebSocket endpoint, dependency wiring
│   ├── session.py            (313) CallSession — the orchestrator / turn-taking state machine ★
│   ├── stt.py                (147) Deepgram live STT WebSocket client
│   ├── tts.py                (83)  Deepgram Aura + ElevenLabs streaming TTS (httpx)
│   ├── agent.py              (129) Streaming LLM loop with function calling + system prompt ★
│   ├── tools.py              (148) Tool registry + 3 tools (orders, KB search, transfer)
│   ├── rag.py                (160) Markdown chunking, BM25, embeddings, RRF
│   ├── text.py               (70)  SentenceChunker: token stream → speakable chunks ★
│   ├── vad.py                (118) EnergyVAD, SileroVAD, SpeechOnsetDetector
│   ├── audio.py              (87)  mu-law codec, resampler, loudness (numpy)
│   ├── pii.py                (55)  Regex PII redaction (+ Luhn check)
│   ├── latency.py            (62)  TurnTimer + LatencyLogger (JSONL)
│   ├── config.py             (115) Settings dataclass from env / .env
│   ├── factory.py            (26)  build_agent(): LLM client + KB + tools → Agent
│   ├── web.py                      Browser test phone: GET / and WS /browser-stream (added 2026-10-14)
│   ├── stream.py                   Shared WebSocket loop: token check → CallSession (added 2026-10-14)
│   ├── telephony.py                Twilio signature, TwiML, outbound call, live transfer (added 2026-10-14)
│   └── static/index.html           The web phone UI (mic → mu-law, playback, marks, transcript)
├── data/
│   ├── orders.json           5 fake orders KE1001–KE1005
│   └── kb/{delivery,payments,returns,warranty}.md   help articles (5 + 5 + 5 + 4 = 19 `##` sections)
├── tests/
│   ├── fakes.py              FakeTransport, FakeSTT, EnergySTT, ScriptedAgent, FakeTTS, make_settings
│   ├── fake_server.py        Runs real create_app() with fakes (used by e2e test)
│   ├── test_units.py         audio, VAD, chunker, PII, RAG, tools, agent tool loop
│   ├── test_session.py       CallSession orchestration (barge-in, marks, merge, PII, failure)
│   ├── test_e2e.py           Real server subprocess + simulate_call.py over real WebSockets
│   ├── test_security.py      Signatures, stream tokens, /call checks, call-SID sanitising (added)
│   └── test_fixes.py         STT reconnect, timeouts, transfer, trimming, spoken PII, latency (added)
├── scripts/
│   ├── simulate_call.py      Fake Twilio client: sends mic frames, plays bot audio, echoes marks
│   ├── chat_cli.py           Text-only REPL against the real Agent (needs LLM key)
│   ├── make_test_audio.py    Deepgram TTS → WAV for caller test audio (needs Deepgram key)
│   └── latency_report.py     p50/p95 table from logs/latency.jsonl
├── docs/BUILD_PLAN.md, docs/INTERVIEW_NOTES.md   owner's study/interview notes (not code)
├── logs/latency.jsonl, logs/transcripts/*.json    runtime output (gitignored)
├── samples/*.wav             caller audio used by the simulator (gitignored)
├── Dockerfile, .dockerignore, .gitignore, pyproject.toml, requirements*.txt, .env.example, README.md
└── learning/                 ← this study guide
```

★ = the three files the owner's own notes say interviewers will ask about.

Note: the project is **not a git repository** right now (`git` metadata absent), though `.gitignore` exists.

## File responsibilities (one line each)

| File | Owns | Depends on (internal) | Used by |
|---|---|---|---|
| `app/main.py` | Process entry; routes `/health`, `/voice`, `/call`, `/media-stream`; `build_deps()`; `WebSocketTransport` | config, factory, latency, session, stt, tts, vad | uvicorn, `tests/fake_server.py` |
| `app/session.py` | One call's state: history, transcript, turn number, response task, sent/played, barge-in | audio, config, latency, pii, stt (Protocol), text, tts (Protocol), vad | main, tests |
| `app/stt.py` | Deepgram WebSocket: connect, send audio, receive loop → callbacks, keepalive | — | main |
| `app/tts.py` | HTTP streaming synth → mu-law bytes | — | main |
| `app/agent.py` | System prompt; multi-round streamed completion; tool-call reassembly | tools | factory, chat_cli |
| `app/tools.py` | `Tool`, `ToolRegistry.call()` error-safe dispatch; `normalize_order_id`; 3 tools | rag | factory, tests |
| `app/rag.py` | `load_markdown_chunks`, `BM25`, `EmbeddingIndex`, `reciprocal_rank_fusion`, `KnowledgeBase` | — | factory, tools |
| `app/text.py` | `SentenceChunker`, `clean_for_tts` | — | session, chat_cli |
| `app/vad.py` | `EnergyVAD`, `SileroVAD`, `SpeechOnsetDetector`, `build_vad` | audio | main, session |
| `app/audio.py` | mu-law encode/decode tables, `resample`, `rms_dbfs`, constants | — | vad, session, simulator, tests |
| `app/pii.py` | `redact()` | — | session, chat_cli |
| `app/latency.py` | `TurnTimer.mark/report`, `LatencyLogger.write` | — | session, main |
| `app/config.py` | `Settings.from_env()`; calls `load_dotenv()` at import | — | everything that needs config |
| `app/factory.py` | `build_agent()`, `DATA_DIR` | agent, config, rag, tools | main, chat_cli, tests |

Dead code: `audio.silence_ulaw()`/`ULAW_SILENCE` were removed on 2026-10-14. `audio.resample()` is used only by the simulator and a test; `CallSession.on_speech_started()` is an intentional no-op.

## Dependency graphs

- High-level: [diagrams/module-dependencies.mmd](diagrams/module-dependencies.mmd)
- Inside the session: [diagrams/feature-session-internal-calls.mmd](diagrams/feature-session-internal-calls.mmd)
- Inside the agent/tools/RAG: [diagrams/feature-tool-calling.mmd](diagrams/feature-tool-calling.mmd), [diagrams/feature-rag-search.mmd](diagrams/feature-rag-search.mmd)

### Coupling observations

- **Low coupling by design**: `session.py` depends on *Protocols* (`STT`, `TTS`, `ReplyAgent`, `Transport`, `VAD`), not concrete classes. Concrete classes are chosen only in `main.build_deps()`. That is why the tests can swap in fakes.
- **Tight coupling**: `agent.stream_reply()` **mutates the `history` list it is given** (appends tool messages). `session.py` relies on that list being shared. This is deliberate but subtle.
- **Hard to test**: `DeepgramSTT`, `DeepgramTTS`, `ElevenLabsTTS`, `SileroVAD`, `EmbeddingIndex`, `/call` — all need network or optional packages; none have tests.

## All diagrams

| Diagram | Topic |
|---|---|
| [system-architecture](diagrams/system-architecture.mmd) | Components & external services |
| [application-startup](diagrams/application-startup.mmd) | Import → lifespan → ready |
| [request-lifecycle](diagrams/request-lifecycle.mmd) | One caller turn, end to end |
| [module-dependencies](diagrams/module-dependencies.mmd) | Python import graph |
| [database-relationships](diagrams/database-relationships.mmd) | In-memory/file data shapes (no DB) |
| [authentication-flow](diagrams/authentication-flow.mmd) | `/call` token check; what is *not* authenticated |
| [error-handling-flow](diagrams/error-handling-flow.mmd) | Where each failure is caught |
| [deployment-flow](diagrams/deployment-flow.mmd) | Local + ngrok + Docker |
| [feature-inbound-call](diagrams/feature-inbound-call.mmd) | `/voice` → WebSocket |
| [feature-outbound-call](diagrams/feature-outbound-call.mmd) | `POST /call` |
| [feature-barge-in](diagrams/feature-barge-in.mmd) | Interrupting the bot |
| [feature-early-endpointing-merge](diagrams/feature-early-endpointing-merge.mmd) | Merge split turns |
| [feature-tool-calling](diagrams/feature-tool-calling.mmd) | Agent tool loop |
| [feature-rag-search](diagrams/feature-rag-search.mmd) | BM25 / hybrid search |
| [feature-sentence-chunking](diagrams/feature-sentence-chunking.mmd) | `SentenceChunker.feed` |
| [feature-twilio-marks](diagrams/feature-twilio-marks.mmd) | sent vs heard |
| [feature-session-internal-calls](diagrams/feature-session-internal-calls.mmd) | Call graph inside `CallSession` |
