"""One phone call = one CallSession. This is the orchestrator.

It wires Twilio <-> STT <-> LLM <-> TTS together and owns turn-taking:

* Caller audio (Twilio "media" events) goes to streaming STT and, while the
  bot is talking, to the VAD for barge-in detection.
* When STT says the caller finished (speech_final / UtteranceEnd), we start
  a response task. Inside it a producer streams LLM text into a sentence
  queue and a consumer turns each sentence into TTS audio and sends it to
  Twilio immediately. The two run concurrently, so sentence 2 is being
  generated while sentence 1 is playing.
* After each sentence we send Twilio a "mark". Twilio echoes a mark back
  once the audio before it has actually played. That's how we know what
  the caller really heard, as opposed to what we merely sent.
* Barge-in: if the caller starts talking while the bot is speaking, we cancel
  the response task (stopping the LLM and TTS streams), send Twilio "clear"
  to flush its playback buffer, and record in history only the sentences
  that were actually played.
* Premature endpointing: if the caller pauses mid-thought, STT may end the
  turn too early. If more speech arrives before the bot has said anything,
  we cancel the in-flight response and merge both parts into one user turn.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Callable, Protocol

from .audio import ulaw_to_pcm16
from .config import Settings
from .latency import LatencyLogger, TurnTimer
from .pii import redact
from .stt import STT
from .text import SentenceChunker
from .tools import CURRENT_CALL_SID
from .tts import TTS
from .vad import VAD, SpeechOnsetDetector

log = logging.getLogger(__name__)

FALLBACK_LINE = "Sorry, I'm having a technical problem right now. Please try again in a moment."


class Transport(Protocol):
    async def send_json(self, data: dict) -> None: ...


class ReplyAgent(Protocol):
    def stream_reply(self, history: list[dict]) -> AsyncIterator[str]: ...


@dataclass
class SessionDeps:
    settings: Settings
    stt_factory: Callable[[], STT]
    agent: ReplyAgent
    tts: TTS
    vad_factory: Callable[[], VAD]
    latency_logger: LatencyLogger | None = None
    transcript_dir: str | None = None


async def _static_text(text: str) -> AsyncIterator[str]:
    yield text


class CallSession:
    def __init__(self, transport: Transport, deps: SessionDeps) -> None:
        self.t = transport
        self.d = deps
        self.s = deps.settings
        self.stt = deps.stt_factory()
        self.onset = SpeechOnsetDetector(deps.vad_factory(), min_speech_ms=self.s.barge_in_min_speech_ms)

        self.stream_sid: str | None = None
        self.call_sid = "unknown"
        self.history: list[dict] = []      # what the LLM sees (redacted user text)
        self.transcript: list[dict] = []   # what gets stored (always redacted)
        self.user_parts: list[str] = []    # final STT segments of the current caller turn

        self.turn_no = 0
        self.timer: TurnTimer | None = None
        self.response_task: asyncio.Task | None = None
        self.sent: list[str] = []          # sentences whose audio was sent this turn
        self.played = 0                    # sentences Twilio confirmed as played
        self.bot_speaking = False          # audio sent and not yet confirmed finished
        self.turn_finalized = True
        self.closed = False

    # ------------------------------------------------------------------ Twilio events

    async def handle_event(self, msg: dict) -> None:
        event = msg.get("event")
        if event == "media":
            await self._on_media(msg)
        elif event == "mark":
            self._on_mark(msg)
        elif event == "start":
            await self._on_start(msg)
        elif event == "stop":
            await self.close()
        elif event == "dtmf":
            log.info("dtmf digit %s", msg.get("dtmf", {}).get("digit"))

    async def _on_start(self, msg: dict) -> None:
        start = msg.get("start", {})
        self.stream_sid = start.get("streamSid") or msg.get("streamSid")
        self.call_sid = start.get("callSid", "unknown")
        log.info("call started call_sid=%s stream_sid=%s", self.call_sid, self.stream_sid)
        await self.stt.start(self)
        if self.s.greeting:
            self._begin_turn(text_source=_static_text(self.s.greeting))

    async def _on_media(self, msg: dict) -> None:
        media = msg.get("media", {})
        if media.get("track", "inbound") != "inbound":
            return
        audio = base64.b64decode(media.get("payload", ""))
        await self.stt.send(audio)
        if self.bot_speaking and self.onset.feed(ulaw_to_pcm16(audio)):
            await self.barge_in("vad")

    def _on_mark(self, msg: dict) -> None:
        name = msg.get("mark", {}).get("name", "")
        turn, _, index = name.partition(":")
        if not turn.isdigit() or int(turn) != self.turn_no:
            return  # mark from an older, already-finished turn
        if index == "end":
            self.bot_speaking = False
            self.onset.reset()
            self._finalize_turn(interrupted=False)
        elif index.isdigit():
            self.played = max(self.played, int(index) + 1)

    # ------------------------------------------------------------------ STT callbacks

    async def on_transcript(self, text: str, is_final: bool, speech_final: bool) -> None:
        if text and self.bot_speaking and len(text.split()) >= self.s.barge_in_min_words:
            await self.barge_in("stt")
        if is_final and text:
            self.user_parts.append(text)
        if speech_final and self.user_parts:
            await self._end_user_turn()

    async def on_utterance_end(self) -> None:
        if self.user_parts:
            await self._end_user_turn()

    async def on_speech_started(self) -> None:
        pass  # VAD and interim transcripts already cover barge-in

    # ------------------------------------------------------------------ turn-taking

    async def _end_user_turn(self) -> None:
        text = " ".join(self.user_parts).strip()
        self.user_parts.clear()
        if not text:
            return
        stored, kinds = redact(text)
        if kinds:
            log.info("redacted %s from caller turn", kinds)
        llm_text = stored if self.s.redact_before_llm else text
        self.transcript.append({"role": "user", "text": stored})
        log.info("caller: %s", stored)

        task = self.response_task
        in_flight = task is not None and not task.done()
        if in_flight and not self.sent and not self.bot_speaking and self.history and self.history[-1]["role"] == "user":
            # The bot hasn't said anything yet: endpointing fired too early.
            # Throw away that response and answer both parts together.
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            self.turn_finalized = True
            llm_text = f"{self.history.pop()['content']} {llm_text}"
            log.info("merged early-endpointed caller turn")
        elif in_flight or self.bot_speaking:
            await self.barge_in("new_turn")

        self.history.append({"role": "user", "content": llm_text})
        self._begin_turn()

    def _begin_turn(self, text_source: AsyncIterator[str] | None = None) -> None:
        self.turn_no += 1
        self.timer = TurnTimer(self.call_sid, self.turn_no)
        self.timer.mark("user_turn_end")
        self.sent = []
        self.played = 0
        self.turn_finalized = False
        CURRENT_CALL_SID.set(self.call_sid)  # copied into the task, read by transfer_to_human
        self.response_task = asyncio.create_task(self._respond(self.timer, self.turn_no, text_source))

    async def barge_in(self, reason: str) -> None:
        task = self.response_task
        if not self.bot_speaking and (task is None or task.done() or task.cancelling()):
            return  # nothing playing, or another barge-in is already stopping it
        log.info("barge-in (%s) during turn %d after %d/%d sentences played", reason, self.turn_no, self.played, len(self.sent))
        # Order matters: cancel (no await yet) and commit history first, so the
        # response task can't append anything after we've decided what was heard.
        if task is not None and not task.done():
            task.cancel()
        self._finalize_turn(interrupted=True)
        self.bot_speaking = False
        self.onset.reset()
        await self.t.send_json({"event": "clear", "streamSid": self.stream_sid})
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def _finalize_turn(self, interrupted: bool) -> None:
        if self.turn_finalized:
            return
        self.turn_finalized = True
        heard = self.sent[: self.played] if interrupted else self.sent
        text = " ".join(heard).strip()
        if text:
            self.history.append({"role": "assistant", "content": text})
            self.transcript.append({"role": "assistant", "text": text, "interrupted": interrupted})
            log.info("bot%s: %s", " (interrupted)" if interrupted else "", text)
        if self.timer is not None:
            self.timer.interrupted = interrupted
            if self.d.latency_logger is not None:
                row = self.d.latency_logger.write(self.timer)
                log.info("latency turn=%d %s", self.timer.turn, row)

    # ------------------------------------------------------------------ response pipeline

    async def _respond(self, timer: TurnTimer, turn_no: int, text_source: AsyncIterator[str] | None) -> None:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._produce(queue, timer, text_source))
                tg.create_task(self._consume(queue, timer, turn_no))
        except* Exception as group:
            for exc in group.exceptions:
                log.error("turn %d failed: %r", turn_no, exc)
            try:
                await self._play_sentence(FALLBACK_LINE, timer, turn_no)
                await self._send_mark(f"{turn_no}:end")
            except Exception:
                log.exception("fallback line failed too")
        if not self.sent:
            self._finalize_turn(interrupted=False)  # nothing to wait for

    async def _produce(self, queue: asyncio.Queue, timer: TurnTimer, text_source) -> None:
        chunker = SentenceChunker()
        source = text_source if text_source is not None else self.d.agent.stream_reply(self.history)
        filler = getattr(self.d.agent, "filler", None) if text_source is None else None
        timer.mark("llm_request")
        try:
            async for delta in source:
                # The tool-call filler isn't model output; don't let it flatter TTFT.
                timer.mark("filler" if filler and delta == filler else "llm_first_token")
                for sentence in chunker.feed(delta):
                    self._mark_sentence(timer)
                    await queue.put(sentence)
            for sentence in chunker.flush():
                self._mark_sentence(timer)
                await queue.put(sentence)
        finally:
            queue.put_nowait(None)

    @staticmethod
    def _mark_sentence(timer: TurnTimer) -> None:
        if "llm_first_token" in timer.marks:  # skip a sentence that is only the filler
            timer.mark("first_sentence")

    async def _consume(self, queue: asyncio.Queue, timer: TurnTimer, turn_no: int) -> None:
        while (sentence := await queue.get()) is not None:
            await self._play_sentence(sentence, timer, turn_no)
        if self.sent:
            await self._send_mark(f"{turn_no}:end")

    async def _play_sentence(self, sentence: str, timer: TurnTimer, turn_no: int) -> None:
        timer.mark("tts_request")
        async for chunk in self.d.tts.synthesize(sentence):
            timer.mark("tts_first_byte")
            await self._send_audio(chunk)
            timer.mark("first_audio_sent")
        index = len(self.sent)
        self.sent.append(sentence)
        await self._send_mark(f"{turn_no}:{index}")

    async def _send_audio(self, chunk: bytes) -> None:
        payload = base64.b64encode(chunk).decode("ascii")
        await self.t.send_json({"event": "media", "streamSid": self.stream_sid, "media": {"payload": payload}})
        self.bot_speaking = True

    async def _send_mark(self, name: str) -> None:
        await self.t.send_json({"event": "mark", "streamSid": self.stream_sid, "mark": {"name": name}})

    # ------------------------------------------------------------------ teardown

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        task = self.response_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._finalize_turn(interrupted=self.bot_speaking)
        await self.stt.close()
        await asyncio.to_thread(self._save_transcript)
        log.info("call ended call_sid=%s turns=%d", self.call_sid, self.turn_no)

    def _save_transcript(self) -> None:
        if not self.d.transcript_dir or not self.transcript:
            return
        # call_sid comes from the client's `start` message: never trust it as a path.
        safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", self.call_sid)[:64] or "unknown"
        path = Path(self.d.transcript_dir) / f"{safe_name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"call_sid": self.call_sid, "turns": self.transcript}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
