"""Reliability fixes: STT reconnect, tool timeouts, transfer, history
trimming, spoken-number redaction, single `clear` per barge-in, and
latency that isn't flattered by the tool filler."""

import asyncio
import json

import pytest
import websockets

from app.agent import recent
from app.factory import DATA_DIR, build_transfer
from app.latency import LatencyLogger
from app.pii import redact
from app.rag import KnowledgeBase
from app.session import CallSession, SessionDeps
from app.stt import DeepgramSTT
from app.tools import CURRENT_CALL_SID, Tool, ToolRegistry, build_default_tools
from app.vad import EnergyVAD
from tests.fakes import FakeSTT, FakeTransport, FakeTTS, ScriptedAgent, make_settings

START = {"event": "start", "streamSid": "MZ1", "start": {"streamSid": "MZ1", "callSid": "CA1"}}


@pytest.fixture(scope="module")
def kb():
    return KnowledgeBase.from_directory(DATA_DIR / "kb")


# ---------------------------------------------------------------- STT reconnect


async def test_stt_reconnects_after_the_socket_drops():
    connections = 0

    async def fake_deepgram(ws):
        nonlocal connections
        connections += 1
        if connections == 1:
            await ws.close()  # first connection dies straight away
            return
        await ws.send(json.dumps({"type": "Results", "is_final": True, "speech_final": True,
                                  "channel": {"alternatives": [{"transcript": "still here"}]}}))
        await asyncio.sleep(5)

    class Handler:
        def __init__(self):
            self.texts = []

        async def on_transcript(self, text, is_final, speech_final):
            self.texts.append(text)

        async def on_utterance_end(self): ...

        async def on_speech_started(self): ...

    async with websockets.serve(fake_deepgram, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        stt = DeepgramSTT("key", url=f"ws://127.0.0.1:{port}")
        handler = Handler()
        await stt.start(handler)
        for _ in range(100):
            if handler.texts:
                break
            await asyncio.sleep(0.05)
        await stt.close()
    assert handler.texts == ["still here"]
    assert stt.reconnects == 1


# ---------------------------------------------------------------- tools


async def test_slow_tool_times_out_with_an_error_the_llm_can_explain():
    async def slow() -> str:
        await asyncio.sleep(5)
        return "never"

    registry = ToolRegistry(timeout_s=0.05)
    registry.register(Tool("slow", "", {"type": "object", "properties": {}}, slow))
    assert "timed out" in json.loads(await registry.call("slow", "{}"))["error"]
    assert "error" in json.loads(await registry.call("slow", "[1, 2]"))  # not an object


async def test_transfer_without_a_human_number_is_honest(kb):
    tools = build_default_tools(kb, DATA_DIR / "orders.json")
    result = json.loads(await tools.call("transfer_to_human", '{"reason": "angry"}'))
    assert result["status"] == "unavailable"


async def test_transfer_uses_the_current_call_sid(kb):
    calls = []

    async def fake_transfer(call_sid, reason):
        calls.append((call_sid, reason))
        return {"status": "transferring"}

    tools = build_default_tools(kb, DATA_DIR / "orders.json", transfer=fake_transfer)
    CURRENT_CALL_SID.set("CA" + "a" * 32)
    await tools.call("transfer_to_human", '{"reason": "wants a human"}')
    assert calls == [("CA" + "a" * 32, "wants a human")]


async def test_real_transfer_refuses_test_calls_without_calling_twilio():
    transfer = build_transfer(make_settings(
        human_agent_number="+919876543210", twilio_account_sid="AC1", twilio_auth_token="t",
        twilio_phone_number="+15550000000",
    ))
    assert transfer is not None
    assert (await transfer("CAbrowser123", "test"))["status"] == "unavailable"
    assert build_transfer(make_settings(human_agent_number="")) is None


# ---------------------------------------------------------------- history trimming


def test_recent_keeps_whole_turns():
    history = []
    for i in range(10):
        history += [
            {"role": "user", "content": f"q{i}"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}"}]},
            {"role": "tool", "tool_call_id": f"c{i}", "content": "{}"},
            {"role": "assistant", "content": f"a{i}"},
        ]
    trimmed = recent(history, 6)
    assert trimmed[0]["role"] == "user"  # never starts on an orphaned tool result
    assert trimmed[-1] == history[-1]
    assert len(trimmed) <= 6
    assert recent(history, 100) is history


# ---------------------------------------------------------------- PII spoken as words


@pytest.mark.parametrize(
    "text,label",
    [
        ("call me on nine eight seven six five four three two one zero", "PHONE"),
        ("my number is double nine eight seven six five four three two one", "PHONE"),
        ("aadhaar two three four five six seven eight nine zero one two three", "AADHAAR"),
        ("card four one one one one one one one one one one one one one one one", "CARD"),
    ],
)
def test_redacts_numbers_spoken_as_words(text, label):
    out, found = redact(text)
    assert found == [label] and f"[{label}]" in out


@pytest.mark.parametrize("text", ["order K E one zero zero two", "one two three four", "I want two, one for me"])
def test_spoken_numbers_that_are_not_pii_are_untouched(text):
    assert redact(text) == (text, [])


# ---------------------------------------------------------------- session


def make_session(agent, tts=None, latency_logger=None):
    transport = FakeTransport()
    deps = SessionDeps(
        settings=make_settings(greeting=""),
        stt_factory=FakeSTT,
        agent=agent,
        tts=tts or FakeTTS(chunk_delay=0.05),
        vad_factory=lambda: EnergyVAD(-35),
        latency_logger=latency_logger,
    )
    return transport, CallSession(transport, deps)


async def test_simultaneous_barge_ins_send_one_clear():
    t, s = make_session(ScriptedAgent(["One. Two. Three."]))
    await s.handle_event(START)
    await s.on_transcript("count", True, True)
    for _ in range(100):
        if s.bot_speaking:
            break
        await asyncio.sleep(0.01)
    await asyncio.gather(s.barge_in("vad"), s.barge_in("stt"))  # media loop + STT task at once
    assert len(t.events("clear")) == 1


class FillerAgent:
    filler = "One moment, let me check that. "

    async def stream_reply(self, history):
        yield self.filler
        await asyncio.sleep(0.2)  # the tool call + second LLM round
        yield "Your order has shipped."


async def test_latency_does_not_count_the_filler_as_first_token(tmp_path):
    logger = LatencyLogger(str(tmp_path))
    t, s = make_session(FillerAgent(), tts=FakeTTS(), latency_logger=logger)
    await s.handle_event(START)
    await s.on_transcript("where is my order", True, True)
    await s.response_task
    for name in ("1:0", "1:1", "1:end"):
        await s.handle_event({"event": "mark", "mark": {"name": name}})
    row = json.loads(logger.path.read_text().splitlines()[-1])
    assert row["filler_ms"] < 50
    assert row["llm_ttft_ms"] >= 190  # the real answer, after the "tool" delay
    assert row["first_sentence_ms"] is not None and row["first_sentence_ms"] >= 0
