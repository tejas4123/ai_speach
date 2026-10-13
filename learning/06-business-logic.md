# 06 · Business Logic (workflows, step by step)

Every step cites `file:line`. "Verified" = read in code and/or covered by a passing test (37/37 passed on 2026-10-14).

---

## W1. A normal conversational turn
Diagram: [request-lifecycle.mmd](diagrams/request-lifecycle.mmd) · Test: `test_session.py::test_greeting_then_full_turn`

1. Caller speech arrives as `media` frames → `stt.send` (`session.py:124`).
2. Deepgram sends finals; `on_transcript` appends to `user_parts` (`:145-146`).
3. `speech_final=True` → `_end_user_turn` (`:147-148`).
4. Text redacted; user message appended to `history`; `_begin_turn` (`:164-186`).
5. `_produce` calls `agent.stream_reply(self.history)` and feeds deltas into `SentenceChunker` (`:249-263`).
6. `_consume` takes each sentence → `_play_sentence` → `tts.synthesize` → `_send_audio` per chunk → `_send_mark("N:i")` (`:265-279`).
7. After the last sentence → `_send_mark("N:end")` (`:268-269`).
8. Twilio echoes marks as audio plays; on `N:end` the turn is finalized and the reply enters history (`:133-136`, `:214-228`).

**Key rule:** the assistant's words enter `history` **only after Twilio confirms playback** (test asserts `history == []` before the marks arrive, `test_session.py:62`).

---

## W2. Order status lookup
Diagram: [feature-tool-calling.mmd](diagrams/feature-tool-calling.mmd) · Tests: `test_units.py::test_tools`, `::test_agent_runs_tools_then_answers`

1. System prompt says: "For anything about a specific order, call get_order_status. Never invent order details." (`agent.py:36`)
2. Model streams a tool call; fragments are accumulated by `tc.index` (`agent.py:93-104`).
3. First tool fragment → filler "One moment, let me check that. " is yielded (if nothing was spoken yet) (`:94-96`).
4. Stream ends → `ToolRegistry.call("get_order_status", '{"order_id":"k e one zero zero two"}')` (`:111`).
5. `normalize_order_id` → `"KE1002"` (`tools.py:74-82`): regex splits letters/digit-runs, maps number words ("oh" → 0 too), uppercases.
6. Dict lookup → `{"found": true, "order_id": "KE1002", ...order}` or `{"found": false, "note": "Ask the caller to repeat the order ID."}` (`tools.py:95-100`).
7. Tool call + result appended to history; loop streams again so the model phrases the answer (`agent.py:112-129`).

---

## W3. Policy question (RAG)
Diagram: [feature-rag-search.mmd](diagrams/feature-rag-search.mmd) · Test: `test_units.py::test_bm25_finds_the_right_section`

1. Model calls `search_knowledge_base(query)` (`tools.py:89-93`).
2. `KnowledgeBase.search(query, k=3)` → `BM25.search(k=6)` (`rag.py:155-158`).
3. If embeddings are enabled and built → `EmbeddingIndex.search(k=6)` → `reciprocal_rank_fusion` → top 3 (`:159-160`).
4. Empty result → `{"results": [], "note": "Nothing relevant found. Do not guess; offer a human agent."}`; otherwise the chunk texts.
5. The prompt tells the model to "answer only from what it returns" (`agent.py:35`) — grounding is **prompt-enforced, not code-enforced**.

---

## W4. Transfer to human
`transfer_to_human(reason)` only logs `TRANSFER requested` and returns a message for the model to say (`tools.py:102-107`). **No real transfer happens** — the call continues with the bot. (Verified; the code comment says production would use Twilio `<Dial>`.)

---

## W5. Barge-in (caller interrupts)
Diagram: [feature-barge-in.mmd](diagrams/feature-barge-in.mmd), [feature-twilio-marks.mmd](diagrams/feature-twilio-marks.mmd) · Tests: `test_barge_in_from_transcript_keeps_only_what_was_heard`, `test_barge_in_from_vad`, `test_one_interim_word_does_not_barge_in`, `test_new_turn_while_bot_speaking_interrupts`

Three triggers:
| Trigger | Condition | Code |
|---|---|---|
| `"vad"` | `bot_speaking` and onset detector saw ≥ `BARGE_IN_MIN_SPEECH_MS` (250) of near-continuous speech (gaps ≤ 100 ms tolerated) | `session.py:125-126`, `vad.py:93-106` |
| `"stt"` | `bot_speaking` and any transcript (interim or final) with ≥ `BARGE_IN_MIN_WORDS` (2) words | `session.py:143-144` |
| `"new_turn"` | caller finished a new turn while a response is in flight or bot speaking (and the merge rule didn't apply) | `session.py:182-183` |

`barge_in()` (`:197-212`): no-op if nothing to interrupt → cancel response task (no await) → `_finalize_turn(interrupted=True)` keeps `sent[:played]` → `bot_speaking=False`, reset VAD → send `clear` → await the cancelled task.

---

## W6. Early-endpointing merge
Diagram: [feature-early-endpointing-merge.mmd](diagrams/feature-early-endpointing-merge.mmd) · Test: `test_early_endpointing_merges_turns`

Condition (`session.py:173`): a response is in flight **and** nothing sent **and** bot not speaking **and** `history[-1]` is a user message.
→ cancel + await the task, set `turn_finalized=True` (so nothing is logged for the discarded turn), pop the previous user content and prepend it: `"I want to" + " " + "return my earbuds"`.
Subtlety: once the filler or any sentence has been sent, `self.sent` is non-empty, so a late continuation becomes a **barge-in**, not a merge.

---

## W7. PII redaction
Test: `test_redacts_pii`, `test_does_not_redact_order_ids_or_amounts`, `test_card_requires_luhn`, `test_pii_is_redacted_before_llm_and_storage`

`redact(text)` (`pii.py:35-55`) order: **CARD** (13–19 digits *and* Luhn-valid) → AADHAAR (12 digits starting 2–9) → PAN (`ABCDE1234F`, case-insensitive) → PHONE (Indian mobile, optional +91) → EMAIL.
Applied in `_end_user_turn`: transcript always gets the redacted text; the LLM gets it only if `REDACT_BEFORE_LLM=true` (default). The `log.info("caller: %s", stored)` also logs redacted text. **Not** applied to assistant text or tool results.

---

## W8. Failure fallback
Diagram: [error-handling-flow.mmd](diagrams/error-handling-flow.mmd) · Test: `test_llm_failure_plays_fallback_line`

Any exception from `_produce` or `_consume` (LLM down, TTS non-200) is caught by `except* Exception` in `_respond` (`session.py:238-245`) → speak `FALLBACK_LINE` ("Sorry, I'm having a technical problem… connect you to a colleague.") + `N:end` mark. If that also fails (e.g. TTS is the broken part), it's logged and the caller hears silence. Note the fallback promises a transfer that **doesn't happen**.

---

## W9. Latency measurement
`TurnTimer.mark(name)` records the **first** timestamp per name (`latency.py:33-34`). Marks set: `user_turn_end` (`_begin_turn`), `llm_request`, `llm_first_token`, `first_sentence` (`_produce`), `tts_request`, `tts_first_byte`, `first_audio_sent` (`_play_sentence`). Report deltas in `latency.py:41-50`.
Gotcha: with a tool call, `llm_first_token` is the time to the **filler**, not to the real answer.
