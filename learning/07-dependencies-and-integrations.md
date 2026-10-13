# 07 · Dependencies and Integrations

## External services

| Service | Purpose | Code | Config vars (names only) | Failure behavior |
|---|---|---|---|---|
| **Twilio Programmable Voice** (webhook) | Inbound calls hit `/voice` | `main.py:105-108` | — | n/a (Twilio calls us) |
| **Twilio Media Streams** (WebSocket) | Bidirectional call audio | `main.py:131-142`, `session.py` | `PUBLIC_HOST` | disconnect → session closed |
| **Twilio REST** | Outbound call | `main.py:113-129` | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, `DEMO_API_TOKEN` | 401/500/502 |
| **Deepgram Listen** (wss) | Streaming STT | `stt.py` | `DEEPGRAM_API_KEY`, `STT_MODEL`, `STT_LANGUAGE`, `ENDPOINTING_MS`, `UTTERANCE_END_MS` | errors logged; **no reconnect** — the bot goes deaf for the rest of the call |
| **Deepgram Speak** (https) | TTS (default) | `tts.py:25-49` | `DEEPGRAM_API_KEY`, `DEEPGRAM_TTS_MODEL` | non-200 → `RuntimeError` → fallback line |
| **ElevenLabs** (https) | TTS (multilingual) | `tts.py:52-83` | `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL`, `TTS_PROVIDER` | same |
| **OpenAI-compatible LLM** | Chat + tools | `agent.py`, `factory.py` | `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_TEMPERATURE` | exception → fallback line. No timeout set explicitly (SDK default). |
| **Embeddings API** (optional) | Hybrid RAG | `rag.py:105-127` | `EMBEDDINGS_ENABLED`, `EMBEDDING_MODEL` | startup failure if build fails; query-time failure → tool error JSON |
| **ngrok** (dev) | Public URL for Twilio | README only | `PUBLIC_HOST` | — |

Other config: `VAD_PROVIDER`, `VAD_ENERGY_THRESHOLD_DBFS`, `BARGE_IN_MIN_SPEECH_MS`, `BARGE_IN_MIN_WORDS`, `REDACT_BEFORE_LLM`, `LOG_DIR`. Undocumented in `.env.example`: `GREETING` (`config.py:111`), `CALLER_TTS_MODEL` (only `scripts/make_test_audio.py:28`).

## Python dependencies (`requirements.txt` — all `>=`, no upper pins, no lockfile)

| Package | Used for | Installed in `.venv` |
|---|---|---|
| fastapi | Web framework | 0.143.0 |
| uvicorn[standard] | ASGI server (+ websockets support) | 0.54.0 |
| websockets | Deepgram client; simulator | 17.2 |
| httpx | TTS streaming, Twilio REST, tests | 0.28.1 |
| openai | `AsyncOpenAI` for chat + embeddings | 3.29.0 |
| numpy | mu-law, VAD energy, embeddings math, latency percentiles | 2.5.4 |
| python-dotenv | `.env` loading | — |
| certifi | CA bundle for Deepgram wss (macOS python.org builds lack roots) `stt.py:29-30` | — |
| *(optional)* silero-vad + torch | `SileroVAD` | not installed |
| pytest, pytest-asyncio | tests | 9.1.1, 1.4.0 |

## Not present (verified)
No database, Redis/cache, message broker (Celery/RabbitMQ/Kafka), object storage, auth provider, or observability SDK. All appear only as "next steps" in README/notes.

## Integration diagrams
- [system-architecture.mmd](diagrams/system-architecture.mmd)
- [feature-inbound-call.mmd](diagrams/feature-inbound-call.mmd), [feature-outbound-call.mmd](diagrams/feature-outbound-call.mmd)
