# 10 · Learning Roadmap

The 12 generic phases reordered for **this** codebase. There's no DB/ORM, so that phase becomes "data & state"; the real complexity is asyncio turn-taking, so it gets the most lessons.

| # | Lesson | Files | Diagram | Prerequisite concepts |
|---|---|---|---|---|
| 1 | **The big picture**: what a voice agent is, the cascaded pipeline, one call = one `CallSession` | README, `02-architecture.md` | system-architecture | HTTP vs WebSocket |
| 2 | **Folder map & entry point**: `uvicorn app.main:app`, `create_app`, routes | `main.py` | module-dependencies | FastAPI basics, decorators |
| 3 | **Startup & configuration**: `load_dotenv`, `Settings`, lifespan, `build_deps`, factories vs singletons | `config.py`, `main.py`, `factory.py` | application-startup | dataclasses, context managers |
| 4 | **Telephony audio**: mu-law, frames, Twilio events, TwiML | `audio.py`, `main.py` `/voice` | feature-inbound-call | bytes, base64 |
| 5 | **asyncio crash course in context**: tasks, cancel, TaskGroup, queues, async generators | `session.py` `_respond/_produce/_consume` | request-lifecycle | coroutines |
| 6 | **STT and turn endings**: Deepgram events, `speech_final`, `UtteranceEnd`, `_end_user_turn` | `stt.py`, `session.py` | request-lifecycle | callbacks / Protocols |
| 7 | **The LLM agent & tool calling**: streaming deltas, fragment reassembly, filler, rounds | `agent.py`, `tools.py` | feature-tool-calling | OpenAI chat format |
| 8 | **RAG**: chunking, BM25 math, embeddings, RRF | `rag.py`, `data/kb` | feature-rag-search | TF-IDF intuition |
| 9 | **Streaming speech out**: `SentenceChunker`, TTS streaming, marks | `text.py`, `tts.py`, `session._play_sentence` | feature-sentence-chunking, feature-twilio-marks | regex |
| 10 | **Barge-in & early-endpoint merge** (the hardest part) | `session.py` `barge_in/_finalize_turn/_on_mark`, `vad.py` | feature-barge-in, feature-early-endpointing-merge | lessons 5–9 |
| 11 | **Security & compliance**: PII redaction, `/call` token, what's unauthenticated | `pii.py`, `main.py` | authentication-flow | regex, Luhn |
| 12 | **Errors, logging, latency**: fallback line, tool errors, `TurnTimer` | `session._respond`, `tools.ToolRegistry.call`, `latency.py` | error-handling-flow | ExceptionGroup |
| 13 | **Testing & debugging**: fakes, e2e simulator, writing a new test | `tests/*`, `scripts/simulate_call.py` | — | pytest |
| 14 | **Deployment**: Docker, ngrok, Twilio config | `Dockerfile`, README | deployment-flow | containers |
| 15 | **Advanced**: scaling, race conditions, refactoring options, the risks list | `11-known-issues-and-risks.md` | all | everything |

## Milestones (you should be able to…)
- After 4: explain how a byte of the caller's voice reaches Deepgram.
- After 7: explain how `{"order_` + `id": "ke 1002"}` becomes a tool call.
- After 10: whiteboard barge-in including *why* history only keeps `sent[:played]`.
- After 13: add a test for an untested branch without help.
- After 15: answer "how would you scale to 1,000 calls?" with references to this code.
