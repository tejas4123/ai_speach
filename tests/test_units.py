import json
from types import SimpleNamespace as NS

import numpy as np
import pytest

from app.agent import Agent
from app.audio import pcm16_to_ulaw, resample, rms_dbfs, ulaw_to_pcm16
from app.factory import DATA_DIR
from app.pii import redact
from app.rag import KnowledgeBase, SearchResult, reciprocal_rank_fusion
from app.text import SentenceChunker, clean_for_tts
from app.tools import build_default_tools, normalize_order_id
from app.vad import EnergyVAD, SpeechOnsetDetector

# ---------------------------------------------------------------- audio


def test_ulaw_known_values():
    assert pcm16_to_ulaw(np.array([0], dtype=np.int16)) == b"\xff"
    assert ulaw_to_pcm16(b"\xff")[0] == 0
    assert ulaw_to_pcm16(b"\x80")[0] == 32124  # loudest positive code
    assert ulaw_to_pcm16(b"\x00")[0] == -32124


def test_ulaw_round_trip_is_close():
    t = np.arange(8000) / 8000
    pcm = (10000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    back = ulaw_to_pcm16(pcm16_to_ulaw(pcm))
    # mu-law error grows with amplitude; relative error stays a few percent
    assert np.max(np.abs(back.astype(int) - pcm)) < 400
    for byte in range(256):  # decode -> encode is lossless for every code...
        encoded = pcm16_to_ulaw(ulaw_to_pcm16(bytes([byte])))
        # ...except mu-law's "negative zero" (0x7F), which comes back as +0 (0xFF)
        assert encoded == bytes([byte]) or (byte == 0x7F and encoded == b"\xff")


def test_resample_lengths_and_loudness():
    pcm = (8000 * np.sin(2 * np.pi * 300 * np.arange(800) / 8000)).astype(np.int16)
    up = resample(pcm, 8000, 16000)
    assert len(up) == 1600
    assert abs(rms_dbfs(up) - rms_dbfs(pcm)) < 0.5


def test_energy_vad_onset_needs_sustained_speech():
    loud = (8000 * np.sin(2 * np.pi * 300 * np.arange(160) / 8000)).astype(np.int16)
    quiet = np.zeros(160, dtype=np.int16)
    det = SpeechOnsetDetector(EnergyVAD(-35), min_speech_ms=100)
    assert [det.feed(loud) for _ in range(4)] == [False, False, False, False]
    assert det.feed(loud) is True       # 100 ms reached
    assert det.feed(loud) is False      # fires once
    for _ in range(10):
        det.feed(quiet)
    assert det.speech_ms == 0


# ---------------------------------------------------------------- text chunking


def feed_all(text, chunker=None, step=3):
    chunker = chunker or SentenceChunker()
    out = []
    for i in range(0, len(text), step):
        out += chunker.feed(text[i : i + step])
    return out + chunker.flush()


def test_chunker_splits_sentences():
    assert feed_all("Sure. Your order has shipped. It arrives in 2 days!") == [
        "Sure.",
        "Your order has shipped.",
        "It arrives in 2 days!",
    ]


def test_chunker_keeps_decimals_and_abbreviations():
    out = feed_all("Dr. Rao said the price is Rs. 4.5 lakh. Okay?")
    assert out == ["Dr. Rao said the price is Rs. 4.5 lakh.", "Okay?"]


def test_chunker_first_chunk_can_split_at_comma():
    out = feed_all("Your order from last Tuesday, the Redmi phone, has been shipped and will arrive soon")
    assert out[0].endswith(",") and len(out) == 2


def test_chunker_handles_hindi_danda():
    assert feed_all("आपका ऑर्डर भेज दिया गया है। कल आएगा।") == ["आपका ऑर्डर भेज दिया गया है।", "कल आएगा।"]


def test_clean_for_tts_strips_markdown():
    assert clean_for_tts("**Refunds** take [5 days](http://x.y)") == "Refunds take 5 days"


# ---------------------------------------------------------------- PII


@pytest.mark.parametrize(
    "text,label",
    [
        ("my aadhaar is 2345 6789 0123", "AADHAAR"),
        ("PAN is ABCDE1234F", "PAN"),
        ("pan abcde1234f", "PAN"),
        ("call me on +91 98765 43210", "PHONE"),
        ("number 9876543210", "PHONE"),
        ("card 4111 1111 1111 1111", "CARD"),
        ("mail me at tejas@example.com", "EMAIL"),
    ],
)
def test_redacts_pii(text, label):
    out, found = redact(text)
    assert label in found
    assert f"[{label}]" in out


def test_does_not_redact_order_ids_or_amounts():
    text = "order KE1002 cost 18999 rupees, delivered in 2 days"
    assert redact(text) == (text, [])


def test_card_requires_luhn():
    _, found = redact("4111 1111 1111 1112")
    assert "CARD" not in found


# ---------------------------------------------------------------- RAG + tools


@pytest.fixture(scope="module")
def kb():
    return KnowledgeBase.from_directory(DATA_DIR / "kb")


async def test_bm25_finds_the_right_section(kb):
    assert "Items that cannot be returned" in (await kb.search("can I return opened earbuds"))[0].chunk.text
    assert "No-cost EMI" in (await kb.search("is there no cost EMI"))[0].chunk.text
    assert "Refund timelines" in (await kb.search("how long for refund to card"))[0].chunk.text


def test_rrf_rewards_agreement(kb):
    a, b, c = kb.chunks[:3]
    fused = reciprocal_rank_fusion([[SearchResult(a, 9), SearchResult(b, 5)], [SearchResult(b, 0.9), SearchResult(c, 0.8)]])
    assert fused[0].chunk.id == b.id  # ranked by both lists


@pytest.mark.parametrize("spoken", ["KE1002", "ke 1002", "K E one zero zero two", "ke-1002"])
def test_normalize_order_id(spoken):
    assert normalize_order_id(spoken) == "KE1002"


async def test_tools(kb):
    tools = build_default_tools(kb, DATA_DIR / "orders.json")
    order = json.loads(await tools.call("get_order_status", '{"order_id": "k e one zero zero two"}'))
    assert order["found"] and order["status"] == "shipped"
    assert json.loads(await tools.call("get_order_status", '{"order_id": "KE9999"}'))["found"] is False
    assert "error" in json.loads(await tools.call("nope", "{}"))
    assert "error" in json.loads(await tools.call("get_order_status", "{not json"))
    assert "error" in json.loads(await tools.call("get_order_status", '{"wrong": 1}'))


# ---------------------------------------------------------------- agent tool-call loop


def text_chunk(s):
    return NS(choices=[NS(delta=NS(content=s, tool_calls=None))])


def tool_chunk(index, id=None, name=None, args=None):
    fn = NS(name=name, arguments=args)
    return NS(choices=[NS(delta=NS(content=None, tool_calls=[NS(index=index, id=id, function=fn)]))])


class FakeCompletions:
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        chunks = self.scripted.pop(0)

        async def gen():
            for c in chunks:
                yield c

        return gen()


async def test_agent_runs_tools_then_answers(kb):
    completions = FakeCompletions(
        [
            [  # round 1: model streams a tool call, arguments split across chunks
                tool_chunk(0, id="call_1", name="get_order_status", args='{"order_'),
                tool_chunk(0, args='id": "ke 1002"}'),
                NS(choices=[]),  # usage-only chunk, must be ignored
            ],
            [text_chunk("Your order "), text_chunk("has shipped.")],
        ]
    )
    agent = Agent(NS(chat=NS(completions=completions)), "m", build_default_tools(kb, DATA_DIR / "orders.json"))
    history = [{"role": "user", "content": "where is my order KE 1002"}]
    out = [d async for d in agent.stream_reply(history)]

    assert out[0] == agent.filler  # filler spoken while the tool runs
    assert "".join(out[1:]) == "Your order has shipped."
    assert history[1]["tool_calls"][0]["function"] == {"name": "get_order_status", "arguments": '{"order_id": "ke 1002"}'}
    assert history[2]["role"] == "tool" and json.loads(history[2]["content"])["status"] == "shipped"
    assert "tools" in completions.calls[0]
    assert completions.calls[1]["messages"][-1]["role"] == "tool"
