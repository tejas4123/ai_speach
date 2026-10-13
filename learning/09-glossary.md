# 09 · Glossary

## Telephony & audio
| Term | Meaning here |
|---|---|
| **PSTN** | Public phone network. Twilio bridges it to the internet. |
| **TwiML** | Twilio's XML instructions. Here: `<Connect><Stream url=…/>` (`main.py:93-99`). |
| **Media Streams** | Twilio feature that streams call audio over a WebSocket as JSON events (`start`, `media`, `mark`, `stop`, `clear`). |
| **streamSid / callSid** | Twilio IDs for the stream and call. `streamSid` must be in every outbound message; `callSid` names the transcript file. |
| **mu-law (μ-law, G.711)** | 8-bit logarithmic audio encoding used by phones. 8 kHz → 8000 bytes/s. `audio.py`. |
| **PCM16** | Linear 16-bit samples; needed for loudness/VAD. `ulaw_to_pcm16`. |
| **Frame** | 20 ms of audio = 160 samples = 160 mu-law bytes (`FRAME_BYTES`). |
| **dBFS** | Loudness relative to full scale (0 = max). `rms_dbfs`; VAD threshold −35. |
| **Mark** | A named bookmark sent after audio; Twilio echoes it when playback reaches it. Names here: `"<turn>:<index>"`, `"<turn>:end"`. |
| **clear** | Message telling Twilio to drop all buffered (unplayed) audio. |

## Speech
| Term | Meaning |
|---|---|
| **STT / ASR** | Speech-to-text. Deepgram here. |
| **Interim result** | `is_final=false`: provisional transcript, may change. |
| **is_final** | This segment's text is locked. |
| **speech_final** | Deepgram's endpointing thinks the speaker finished (after `ENDPOINTING_MS` silence). |
| **UtteranceEnd** | Deepgram fallback event: no new words for `UTTERANCE_END_MS` (min 1000). |
| **Endpointing** | Deciding the caller's turn is over. **Early endpointing** = deciding too soon. |
| **VAD** | Voice Activity Detection: is this frame speech? `EnergyVAD` / `SileroVAD`. |
| **Onset detection** | `SpeechOnsetDetector`: fires once after sustained speech. |
| **Barge-in** | Caller talks over the bot; the bot stops. |
| **TTS** | Text-to-speech. Deepgram Aura / ElevenLabs. |
| **TTFB / TTFT** | Time to first byte (TTS) / first token (LLM). |
| **Voice-to-voice latency** | Caller stops speaking → caller hears bot. Measured by the simulator. |
| **Backchannel** | "hmm", "okay" — listener noises that shouldn't count as interruption (not handled). |

## LLM / RAG
| Term | Meaning |
|---|---|
| **Function calling / tool calling** | The model returns structured `tool_calls` instead of text; we run them and send back `role: tool` messages. |
| **Tool round** | One completion that ended in tool calls. Max `max_tool_rounds=3`; the 4th round has no tools. |
| **Filler** | "One moment, let me check that." spoken while tools run. |
| **RAG** | Retrieval-augmented generation: fetch KB text, let the model answer from it. |
| **Chunk** | One `##` section of a KB markdown file. |
| **BM25** | Classic keyword ranking (term frequency × inverse document frequency, length-normalized). Params `k1=1.5`, `b=0.75`. |
| **Embedding** | Vector representation of text; cosine similarity = semantic closeness. |
| **RRF** | Reciprocal Rank Fusion: score = Σ 1/(60 + rank). Combines rankings without comparing raw scores. |
| **System prompt** | `SYSTEM_PROMPT` in `agent.py:25-40`. Persona "Asha". |

## Python / async
| Term | Meaning |
|---|---|
| **asyncio Task** | A scheduled coroutine; `task.cancel()` raises `CancelledError` inside it at its next `await`. |
| **TaskGroup** (3.11+) | Runs tasks together; if one fails, cancels the others and raises an `ExceptionGroup`. |
| **`except*`** | Catches matching exceptions out of an `ExceptionGroup`. |
| **Protocol** | Structural interface (`typing.Protocol`): any object with matching methods fits. |
| **Async generator** | `async def` with `yield`; consumed with `async for`. `stream_reply`, `synthesize`. |
| **Lifespan** | FastAPI startup/shutdown hook (`main.py:77-89`). |
| **ASGI** | Async server interface; Uvicorn runs the FastAPI ASGI app. |

## Project-specific names
`CallSession`, `SessionDeps`, `WebSocketTransport`, `ReplyAgent`, `Agent`, `ToolRegistry`, `Tool`, `KnowledgeBase`, `EmbeddingIndex`, `SentenceChunker`, `SpeechOnsetDetector`, `TurnTimer`, `LatencyLogger`, `Settings`, `FALLBACK_LINE`, `DATA_DIR`. Domain: **Kiran Electronics** (fake store), **Asha** (bot persona), order IDs `KE1001`–`KE1005`.

## India-specific
| Term | Meaning |
|---|---|
| **Aadhaar** | 12-digit national ID (first digit 2–9). |
| **PAN** | Tax ID, format `ABCDE1234F`. |
| **UPI / COD / EMI** | Payment methods: instant bank transfer / cash on delivery / instalments. |
| **Danda (।)** | Hindi full stop; the chunker splits on it. |
| **Luhn** | Checksum used to validate card numbers (`pii._luhn_ok`). |
