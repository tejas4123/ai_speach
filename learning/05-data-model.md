# 05 · Data Model

## Is there a database? **No.** (verified)

No SQL/NoSQL driver in `requirements.txt`, no ORM, no migrations, no schema files. Explaining "tables, relationships, transactions" would be invented content. Instead, here are the **real data structures** — files on disk and in-memory objects — which play the role a database would.

Diagram: [diagrams/database-relationships.mmd](diagrams/database-relationships.mmd) (an `erDiagram` of these shapes, clearly labelled *not a DB*).

## 1. Read-only reference data

### `data/orders.json` — "orders table"
A JSON object keyed by order ID. Loaded **once** at startup in `build_default_tools` (`tools.py:86`) into a Python dict captured by the `get_order_status` closure.

```json
"KE1002": { "status": "shipped", "items": ["Redmi Note 13 5G, 8GB/256GB"], "amount_rupees": 18999,
            "payment": "credit card, no-cost EMI 6 months", "expected_delivery": "in 2 days", "city": "Noida" }
```
- Keys: `KE1001`…`KE1005`. Fields vary per order (`expected_delivery`, `delivered_on`, `refund_eta`, `refund_status`) — **schemaless**.
- Access pattern: exact dict lookup after `normalize_order_id()` (`tools.py:74-82`).
- Changes to the file need a restart (no reload).

### `data/kb/*.md` — "knowledge base"
4 files, each `# Title` + `## Section` blocks. `load_markdown_chunks` (`rag.py:58-71`) turns **each `##` section into one `Chunk`**:

| Field | Example |
|---|---|
| `id` | `"returns#1"` (file stem + section index) |
| `title` | `"Items that cannot be returned"` |
| `text` | `"Returns and Refunds > Items that cannot be returned\n<body>"` |
| `tokens` | lowercase alphanumeric tokens minus stopwords |

19 chunks total. Indexed in memory by `BM25` (term frequencies + IDF) and optionally `EmbeddingIndex` (an L2-normalized `numpy` matrix).

## 2. Per-call in-memory state (`CallSession`, `session.py:73-93`)

| Field | Type | Meaning |
|---|---|---|
| `history` | `list[dict]` | **What the LLM sees.** OpenAI chat messages: `user` (redacted if `REDACT_BEFORE_LLM`), `assistant` (only text the caller heard), plus `assistant` with `tool_calls` and `tool` results appended **by `Agent.stream_reply`**. |
| `transcript` | `list[dict]` | **What gets stored.** `{"role":"user","text":<always redacted>}` / `{"role":"assistant","text":…,"interrupted":bool}`. No tool messages. |
| `user_parts` | `list[str]` | Final STT segments of the in-progress caller turn. |
| `turn_no` | `int` | Increments per bot turn (greeting is turn 1). Used in mark names. |
| `sent` | `list[str]` | Sentences whose audio was fully sent this turn. |
| `played` | `int` | How many of `sent` Twilio confirmed as played. |
| `bot_speaking`, `turn_finalized`, `closed` | `bool` | State flags. |
| `response_task` | `asyncio.Task` | The current turn's pipeline. |
| `timer` | `TurnTimer` | Stage timestamps for this turn. |

Lifetime: the WebSocket connection. Lost on crash/restart. Not shared between processes.

### "Transactions" in this codebase
There are no DB transactions, but there is a **consistency boundary** worth studying: the moment a turn is *finalized* (`_finalize_turn`, `session.py:214-228`). It is idempotent (guarded by `turn_finalized`) and commits exactly once:
- normal end → all `sent` sentences go into `history`;
- barge-in → only `sent[:played]`.

`barge_in()` deliberately calls `task.cancel()` and `_finalize_turn()` **before** its first `await`, so the response task can't append a sentence after the "commit" — the asyncio equivalent of holding a lock.

Agent-side invariant: tool-call + tool-result messages are appended **only after all tools finish** (`agent.py:111-129`), so a cancelled turn never leaves an assistant `tool_calls` message without its matching `tool` messages (the OpenAI API rejects that).

## 3. Append-only output files

| File | Writer | Format |
|---|---|---|
| `logs/latency.jsonl` | `LatencyLogger.write` (`latency.py:58-62`) on every `_finalize_turn` | one JSON object per turn: `call_sid, turn, interrupted, llm_ttft_ms, first_sentence_ms, tts_ttfb_ms, server_total_ms` |
| `logs/transcripts/{call_sid}.json` | `CallSession._save_transcript` (`session.py:305-313`) on `close()` | `{"call_sid":…, "turns":[…transcript…]}` — overwritten if the same call_sid repeats |

Both use synchronous file I/O inside the event loop (small, but blocking). `LOG_DIR` controls the location.

## Inspected sample (real file in your repo)

`logs/transcripts/CAsim1791917113.json` contains assistant lines like *"I heard you say: Hi."* — that text is **not produced by any code currently in the repo** (neither `Agent` nor `ScriptedAgent`). The user lines ("Hi. Where is my order KE1002?") look like real STT output, not `EnergySTT`'s scripted lines. In `logs/latency.jsonl`, `llm_ttft_ms ≈ 312 ms` matches a `ScriptedAgent`-style `first_token_delay=0.3`, and `tts_ttfb_ms ≈ 1030 ms` is far slower than `FakeTTS`. Best guess: a temporary hybrid setup (real STT/TTS, echo agent) that is no longer in the repo. **Inference, unverified.** Worth remembering how you produced that run.
