"""Text-only chat with the agent: test the prompt, RAG and tools without any audio.
Needs only LLM_API_KEY. Shows time-to-first-token and how the reply would be
chunked for TTS.

  python scripts/chat_cli.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import Settings  # noqa: E402
from app.factory import build_agent  # noqa: E402
from app.pii import redact  # noqa: E402
from app.text import SentenceChunker  # noqa: E402


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="   [%(name)s] %(message)s")
    settings = Settings.from_env()
    agent = await build_agent(settings)
    history: list[dict] = []
    print(f"model={settings.llm_model}. Try: 'where is my order KE1002', 'can I return earbuds', 'my aadhaar is 2345 6789 0123'. Ctrl+C to quit.\n")
    while True:
        try:
            user = await asyncio.to_thread(input, "you> ")
        except (EOFError, KeyboardInterrupt):
            break
        if not user.strip():
            continue
        clean, kinds = redact(user)
        if kinds:
            print(f"   [redacted {kinds}: {clean}]")
        history.append({"role": "user", "content": clean if settings.redact_before_llm else user})

        chunker, chunks, reply = SentenceChunker(), [], ""
        t0, ttft = time.perf_counter(), None
        print("bot> ", end="", flush=True)
        async for delta in agent.stream_reply(history):
            ttft = ttft or (time.perf_counter() - t0) * 1000
            reply += delta
            print(delta, end="", flush=True)
            chunks += chunker.feed(delta)
        chunks += chunker.flush()
        total = (time.perf_counter() - t0) * 1000
        print(f"\n   [ttft {ttft or 0:.0f} ms | total {total:.0f} ms | TTS chunks: {chunks}]\n")
        history.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    asyncio.run(main())
