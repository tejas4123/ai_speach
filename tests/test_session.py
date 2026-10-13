"""Orchestration tests: turn-taking, Twilio marks, barge-in, early-endpointing
merge, redaction and failure handling. All offline, using fakes."""

import asyncio
import json

import numpy as np

from app.audio import pcm16_to_ulaw
from app.session import FALLBACK_LINE, CallSession, SessionDeps
from app.vad import EnergyVAD
from tests.fakes import FakeSTT, FakeTransport, FakeTTS, ScriptedAgent, make_settings

START = {"event": "start", "streamSid": "MZ1", "start": {"streamSid": "MZ1", "callSid": "CA1"}}


def make_session(agent=None, tts=None, greeting="", transcript_dir=None, **settings):
    transport = FakeTransport()
    deps = SessionDeps(
        settings=make_settings(greeting=greeting, **settings),
        stt_factory=FakeSTT,
        agent=agent or ScriptedAgent(["Okay."]),
        tts=tts or FakeTTS(),
        vad_factory=lambda: EnergyVAD(-35),
        transcript_dir=transcript_dir,
    )
    return transport, CallSession(transport, deps)


def mark(name):
    return {"event": "mark", "streamSid": "MZ1", "mark": {"name": name}}


def loud_frame():
    t = np.arange(160) / 8000
    return pcm16_to_ulaw((9000 * np.sin(2 * np.pi * 300 * t)).astype(np.int16))


def media(frame):
    import base64

    return {"event": "media", "streamSid": "MZ1", "media": {"track": "inbound", "payload": base64.b64encode(frame).decode()}}


async def wait_for(predicate, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


async def test_greeting_then_full_turn():
    agent = ScriptedAgent(["Sure. Your order has shipped."])
    t, s = make_session(agent=agent, greeting="Hello, how can I help?")
    await s.handle_event(START)
    await s.response_task

    assert t.mark_names() == ["1:0", "1:end"]
    assert all(m["streamSid"] == "MZ1" for m in t.events("media"))
    assert s.bot_speaking  # audio sent, not yet confirmed played
    assert s.history == []  # nothing committed until Twilio confirms playback

    await s.handle_event(mark("1:0"))
    await s.handle_event(mark("1:end"))
    assert not s.bot_speaking
    assert s.history == [{"role": "assistant", "content": "Hello, how can I help?"}]

    await s.on_transcript("where is my", True, False)  # final segment, turn not over
    assert s.response_task.done()
    await s.on_transcript("order", True, True)  # speech_final ends the turn
    await s.response_task
    assert agent.seen[0][-1] == {"role": "user", "content": "where is my order"}
    assert t.mark_names()[2:] == ["2:0", "2:1", "2:end"]

    for name in ("2:0", "2:1", "2:end"):
        await s.handle_event(mark(name))
    assert s.history[-1] == {"role": "assistant", "content": "Sure. Your order has shipped."}


async def test_utterance_end_also_ends_turn():
    t, s = make_session()
    await s.handle_event(START)
    await s.on_transcript("hello there", True, False)
    await s.on_utterance_end()
    await s.response_task
    assert s.history[0] == {"role": "user", "content": "hello there"}


async def test_barge_in_from_transcript_keeps_only_what_was_heard():
    agent = ScriptedAgent(["First sentence here. Second sentence here. Third sentence here."])
    t, s = make_session(agent=agent, tts=FakeTTS(chunk_delay=0.05))
    await s.handle_event(START)
    await s.on_transcript("tell me something", True, True)

    await wait_for(lambda: "1:0" in t.mark_names())
    await s.handle_event(mark("1:0"))  # Twilio confirms sentence 1 was played
    assert s.played == 1

    await s.on_transcript("wait wait", False, False)  # interim words while bot talks
    assert t.events("clear") == [{"event": "clear", "streamSid": "MZ1"}]
    assert s.response_task.done() and s.response_task.cancelled()
    assert not s.bot_speaking
    assert s.history[-1] == {"role": "assistant", "content": "First sentence here."}
    assert s.transcript[-1]["interrupted"] is True

    # A late end-mark for the cancelled turn must not change anything.
    await s.handle_event(mark("1:end"))
    assert s.history[-1] == {"role": "assistant", "content": "First sentence here."}


async def test_one_interim_word_does_not_barge_in():
    t, s = make_session(agent=ScriptedAgent(["One. Two. Three."]), tts=FakeTTS(chunk_delay=0.05))
    await s.handle_event(START)
    await s.on_transcript("go", True, True)
    await wait_for(lambda: s.bot_speaking)
    await s.on_transcript("uh", False, False)
    assert t.events("clear") == []


async def test_barge_in_from_vad():
    t, s = make_session(agent=ScriptedAgent(["A long answer. With two sentences."]), tts=FakeTTS(chunk_delay=0.05))
    await s.handle_event(START)
    await s.on_transcript("question", True, True)
    await wait_for(lambda: s.bot_speaking)

    frame = loud_frame()
    frames_needed = -(-s.s.barge_in_min_speech_ms // 20)  # ceil: 250 ms -> 13 frames
    for _ in range(frames_needed - 1):
        await s.handle_event(media(frame))
    assert t.events("clear") == []  # a short noise isn't enough
    await s.handle_event(media(frame))
    assert len(t.events("clear")) == 1
    assert s.response_task.cancelled()


async def test_early_endpointing_merges_turns():
    agent = ScriptedAgent(["Sure, I can help with that return."], first_token_delay=0.3)
    t, s = make_session(agent=agent)
    await s.handle_event(START)
    await s.on_transcript("I want to", True, True)          # caller paused mid-thought
    await asyncio.sleep(0.05)
    await s.on_transcript("return my earbuds", True, True)  # ...and continued
    await s.response_task

    user_turns = [m for m in s.history if m["role"] == "user"]
    assert user_turns == [{"role": "user", "content": "I want to return my earbuds"}]
    assert t.events("clear") == []  # nothing had been played, so nothing to clear


async def test_new_turn_while_bot_speaking_interrupts():
    agent = ScriptedAgent(["One. Two. Three.", "Okay, stopping."])
    t, s = make_session(agent=agent, tts=FakeTTS(chunk_delay=0.05))
    await s.handle_event(START)
    await s.on_transcript("count for me", True, True)
    await wait_for(lambda: s.bot_speaking)
    await s.on_transcript("stop", True, True)  # single-word final while speaking
    assert len(t.events("clear")) == 1
    await s.response_task
    assert [m["role"] for m in s.history] == ["user", "user"]  # nothing was confirmed heard
    assert s.history[-1]["content"] == "stop"


async def test_pii_is_redacted_before_llm_and_storage():
    agent = ScriptedAgent(["Thanks."])
    t, s = make_session(agent=agent)
    await s.handle_event(START)
    await s.on_transcript("my aadhaar is 2345 6789 0123", True, True)
    await s.response_task
    assert agent.seen[0][-1]["content"] == "my aadhaar is [AADHAAR]"
    assert s.transcript[0]["text"] == "my aadhaar is [AADHAAR]"


async def test_llm_failure_plays_fallback_line():
    tts = FakeTTS()
    t, s = make_session(agent=ScriptedAgent([], fail=True), tts=tts)
    await s.handle_event(START)
    await s.on_transcript("hello", True, True)
    await s.response_task
    assert tts.calls == [FALLBACK_LINE]
    assert t.mark_names() == ["1:0", "1:end"]


async def test_stop_closes_stt_and_saves_redacted_transcript(tmp_path):
    t, s = make_session(transcript_dir=str(tmp_path))
    await s.handle_event(START)
    await s.on_transcript("call me on 9876543210", True, True)
    await s.response_task
    await s.handle_event({"event": "stop", "streamSid": "MZ1"})
    assert s.closed and s.stt.closed
    saved = json.loads((tmp_path / "CA1.json").read_text())
    assert saved["turns"][0]["text"] == "call me on [PHONE]"
