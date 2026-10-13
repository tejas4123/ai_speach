# 00 · Project Overview

> Status: written 2026-10-14 after reading every file under `app/`, `tests/`, `scripts/`, `data/`, `docs/`, plus `README.md`, `Dockerfile`, `pyproject.toml`, `requirements*.txt` and `.env.example`. `.env` was **not** read for values (only its variable names).

## What the application does (verified)

**Realtime Voice Agent** is a **phone customer-support bot** for a fictional Indian store, *Kiran Electronics*. A person phones in (or the bot phones them), speaks naturally, and an LLM-powered agent named **Asha** answers by voice.

It can:

| Capability | Where it lives |
|---|---|
| Take real phone calls through **Twilio** (inbound `/voice`, outbound `POST /call`) | `app/main.py` |
| Stream caller audio to **Deepgram** for live speech-to-text | `app/stt.py` |
| Ask an **OpenAI-compatible LLM** (OpenAI, Groq…) for a reply, with **function calling** | `app/agent.py` |
| Look up **orders** from a JSON file, search a **knowledge base** (BM25, optional embeddings), "transfer to human" (log only) | `app/tools.py`, `app/rag.py`, `data/` |
| Turn the reply into speech with **Deepgram Aura** or **ElevenLabs**, streamed sentence by sentence | `app/tts.py`, `app/text.py` |
| Let the caller **interrupt** ("barge-in") — the bot stops and only remembers what was actually heard | `app/session.py`, `app/vad.py` |
| **Redact PII** (Aadhaar, PAN, card, phone, email) before text reaches the LLM / logs / stored transcript | `app/pii.py` |
| Record **per-turn latency** to `logs/latency.jsonl` and transcripts to `logs/transcripts/` | `app/latency.py`, `app/session.py` |

## What it is *not*

- Not a CRUD web app: there is **no database, no ORM, no user accounts, no migrations**. "Data" is two read-only files (`data/orders.json`, `data/kb/*.md`) plus append-only log files.
- Not multi-service: a **single FastAPI process**. All concurrency is `asyncio` coroutines.
- No queues, caches, or background workers (the README lists these as "what I'd do next").

## Main business features

1. **Inbound call** — Twilio hits `/voice`, gets TwiML telling it to open a WebSocket to `/media-stream`.
2. **Outbound call** — `POST /call` (with `X-Demo-Token`) asks Twilio's REST API to dial a number.
3. **Conversation turn** — caller speech → STT → LLM (maybe tools) → sentence chunker → TTS → audio back to Twilio.
4. **Order status lookup** — tool `get_order_status`.
5. **Policy Q&A** — tool `search_knowledge_base` over 4 markdown help articles (19 `##` sections).
6. **Human handoff** — tool `transfer_to_human` (demo: only logs).
7. **Barge-in** — caller interrupts the bot via VAD or interim STT words.
8. **Early-endpointing merge** — caller paused mid-sentence; the two halves are merged into one turn.
9. **PII redaction**, **latency logging**, **transcript storage**.

## Tech stack (verified from `requirements.txt` and imports)

Python ≥ 3.11 (venv here: 3.14.2; Docker: 3.12) · FastAPI + Starlette + Uvicorn · `websockets` (client to Deepgram) · `httpx` (TTS + Twilio REST) · `openai` SDK (`AsyncOpenAI`) · `numpy` (audio + embeddings) · `python-dotenv` · `certifi`. Tests: `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`).

## Who wrote this and why (from `docs/`)

`docs/BUILD_PLAN.md` and `docs/INTERVIEW_NOTES.md` show this is a **portfolio/interview project**: the code is complete and tested offline, and the owner's job is to run it with real APIs, collect latency numbers, and be able to defend every design decision. That is exactly what this `learning/` folder is for.

## Read next

→ [01-codebase-map.md](01-codebase-map.md) for every file, then [02-architecture.md](02-architecture.md).
