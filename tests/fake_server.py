"""Runs the real FastAPI app and CallSession, with offline fakes for STT/LLM/TTS.
Used by the end-to-end test together with scripts/simulate_call.py."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.latency import LatencyLogger  # noqa: E402
from app.main import create_app  # noqa: E402
from app.session import SessionDeps  # noqa: E402
from app.vad import EnergyVAD  # noqa: E402
from tests.fakes import EnergySTT, FakeTTS, ScriptedAgent, make_settings  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--log-dir", required=True)
    args = p.parse_args()

    settings = make_settings(
        greeting="Hello, Kiran Electronics support.", log_dir=args.log_dir, media_stream_token="test-token"
    )
    deps = SessionDeps(
        settings=settings,
        stt_factory=lambda: EnergySTT(["where is my order", "never mind, can I return earbuds"]),
        agent=ScriptedAgent(
            [
                "Your order K E one zero zero two has shipped. It should reach you in two days. Anything else?",
                "Opened earbuds can't be returned, sorry. Can I help with something else?",
            ],
            first_token_delay=0.3,
            token_delay=0.01,
        ),
        tts=FakeTTS(chunk_delay=0.05, ms_per_char=60),
        vad_factory=lambda: EnergyVAD(-35),
        latency_logger=LatencyLogger(args.log_dir),
        transcript_dir=str(Path(args.log_dir) / "transcripts"),
    )
    uvicorn.run(create_app(deps), host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
