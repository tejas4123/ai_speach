# Diagrams

Every `.mmd` file is plain Mermaid. Its header comment (`%%`) lists the source files/lines it was drawn from. To view one: paste it into <https://mermaid.live>, or wrap it in a ` ```mermaid ` block in any Markdown file (GitHub and VS Code with a Mermaid extension render it).

| File | Type | Explains | Backed by |
|---|---|---|---|
| system-architecture | flowchart | All components + external services | `main.py`, `session.py`, `stt/tts/agent/tools/rag.py` |
| application-startup | flowchart | Import → lifespan → `build_deps` → ready, incl. fail-fast branches | `config.py:10`, `main.py:51-89`, `factory.py` |
| request-lifecycle | sequence | One caller turn, from `media` frames to `N:end` mark & finalize | `session.py:97-287` |
| module-dependencies | graph | Python import graph (verified by grep) | `from .x import` lines |
| database-relationships | ER | **No DB.** In-memory + file data shapes | `data/`, `session.py:73-93`, `latency.py` |
| authentication-flow | flowchart | `/call` token check; which routes are unauthenticated | `main.py:105-142` |
| error-handling-flow | flowchart | Where each failure is caught (TaskGroup, ToolRegistry, STT, WS) | `session.py:232-247`, `tools.py:55-71`, `stt.py:116-135` |
| deployment-flow | flowchart | Local + ngrok, Docker; no CI/CD | README, `Dockerfile` |
| feature-inbound-call | sequence | `/voice` → WebSocket → `_on_start` → greeting | `main.py`, `session.py:110-117` |
| feature-outbound-call | sequence | `POST /call` → Twilio REST | `main.py:110-129` |
| feature-barge-in | flowchart | 3 triggers → `barge_in` steps | `session.py:119-212`, `vad.py:93-106` |
| feature-early-endpointing-merge | sequence | Merging a split caller turn | `session.py:159-186` |
| feature-tool-calling | flowchart | Agent rounds, fragment accumulation, filler, gather | `agent.py:62-129` |
| feature-rag-search | flowchart | BM25 → (embeddings → RRF) | `rag.py`, `tools.py:89-93` |
| feature-sentence-chunking | flowchart | `SentenceChunker.feed` decision logic | `text.py` |
| feature-twilio-marks | sequence | "sent" vs "heard" with marks and `clear` | `session.py:128-138, 214-228, 271-279` |
| feature-session-internal-calls | graph | Who calls whom inside `CallSession` | `session.py` |

**Uncertainty markers:** dashed edges = optional/config-dependent or task spawn. The Twilio "leftover marks after clear" behavior is modelled on `scripts/simulate_call.py:101-107`, not verified against real Twilio.

Not syntax-checked with a Mermaid renderer (no CLI installed, and I didn't install one). If a diagram fails to render, tell me which one.
