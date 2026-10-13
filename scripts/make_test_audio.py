"""Generate caller test audio with Deepgram TTS, so you can test without recording.

  python scripts/make_test_audio.py "Hi, where is my order K E one zero zero two?" samples/q1.wav

Recording yourself is even better for testing STT (real accent, real
background noise). Any 16-bit WAV works with simulate_call.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    text, out = sys.argv[1], Path(sys.argv[2])
    key = os.getenv("DEEPGRAM_API_KEY")
    if not key:
        raise SystemExit("DEEPGRAM_API_KEY is not set")
    model = os.getenv("CALLER_TTS_MODEL") or os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en")
    resp = httpx.post(
        "https://api.deepgram.com/v1/speak",
        params={"model": model, "encoding": "linear16", "sample_rate": 16000, "container": "wav"},
        headers={"Authorization": f"Token {key}"},
        json={"text": text},
        timeout=30,
    )
    resp.raise_for_status()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(resp.content)
    print(f"wrote {out} ({len(resp.content) / 32000:.1f}s)")


if __name__ == "__main__":
    main()
