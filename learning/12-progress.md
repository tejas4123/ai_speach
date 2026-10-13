# 12 · Learning Progress

_Update after every lesson. When you say **"continue learning"**, I read this file and resume from "Next lesson"._

## Session log

### 2026-10-14 — Session 1
- Codebase fully inspected (all 14 `app/` modules, 5 test files, 4 scripts, data, docs).
- Ran `pytest -q` → **37 passed in 19.66 s**.
- Generated `learning/00`–`12` and 17 diagrams.
- Started **Lesson 1: The big picture**.

## Lessons

| # | Lesson | Status | Quiz score | Notes |
|---|---|---|---|---|
| 1 | The big picture | 🟡 in progress | – | |
| 2 | Folder map & entry point | ⬜ | | |
| 3 | Startup & configuration | ⬜ | | |
| 4 | Telephony audio | ⬜ | | |
| 5 | asyncio in context | ⬜ | | |
| 6 | STT and turn endings | ⬜ | | |
| 7 | LLM agent & tool calling | ⬜ | | |
| 8 | RAG | ⬜ | | |
| 9 | Streaming speech out | ⬜ | | |
| 10 | Barge-in & early-endpoint merge | ⬜ | | |
| 11 | Security & compliance | ⬜ | | |
| 12 | Errors, logging, latency | ⬜ | | |
| 13 | Testing & debugging | ⬜ | | |
| 14 | Deployment | ⬜ | | |
| 15 | Advanced design & refactoring | ⬜ | | |

## Files studied
_(none yet)_

## Diagrams reviewed
_(none yet)_

## Questions answered correctly
_(none yet)_

## Concepts to revisit
_(none yet)_

## Exercises completed
_(none yet)_

## Changes made during experiments
- 2026-10-14: added browser test phone: new `app/web.py`, `app/static/index.html`; `app/main.py` imports `register_web_ui` and calls it at the end of `create_app` (2 lines). `session.py` untouched. Tests still 37/37.
- Added `.claude/launch.json` (uvicorn on port 8001).
- Live run: Deepgram STT + TTS worked; OpenAI returned 429 insufficient_quota → fallback line played (see 11-known-issues R0).
- 2026-10-14 "fix all the issues": new `app/stream.py`, `app/telephony.py`, `tests/test_security.py`, `tests/test_fixes.py`; edited `main.py`, `session.py`, `stt.py`, `agent.py`, `tools.py`, `factory.py`, `pii.py`, `config.py`, `latency.py`, `audio.py`, `web.py`, `static/index.html`, `scripts/simulate_call.py`, `scripts/latency_report.py`, `tests/fake_server.py`, `tests/test_e2e.py`, `.env.example`, `requirements.txt`, `README.md`. Found + fixed pre-existing bug: `/call` always returned 422. `git init` (no commit). Tests 60/60. Full list: 11-known-issues-and-risks.md.
- Good study material from these fixes: Lesson 11 (security) can now use `telephony.py` + `stream.py`; Lesson 6 can use the STT reconnect loop.

## Open questions
- How was `logs/transcripts/CAsim1791917113.json` ("I heard you say: …") produced? No agent in the repo emits that text.

## Next lesson
**Lesson 1** — waiting for your answers to the Lesson 1 questions + exercise.
