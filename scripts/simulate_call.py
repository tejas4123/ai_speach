"""Simulate a Twilio phone call against your local server. No phone or Twilio needed.

It speaks to the server exactly like Twilio Media Streams does:
* sends `connected` and `start`, then a continuous 20 ms mu-law frame every
  20 ms (your WAV when "speaking", silence otherwise),
* "plays" the bot's audio on a virtual clock and echoes `mark` events back
  only once the audio before them would have finished playing,
* honours `clear` (barge-in) by dropping queued audio.

It measures voice-to-voice latency from the caller's side: from the moment
your last audio frame is sent to the first bot audio that comes back. This
includes endpointing, unlike the server-side numbers in logs/latency.jsonl.

The server only accepts the stream if --token matches its MEDIA_STREAM_TOKEN
(set one in .env; without it the server picks a random token per run).

Examples:
  python scripts/simulate_call.py --wav samples/q1.wav --wav samples/q2.wav
  python scripts/simulate_call.py --wav samples/q1.wav --interrupt-wav samples/stop.wav
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np
import websockets
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.audio import FRAME_BYTES, pcm16_to_ulaw, resample, ulaw_to_pcm16  # noqa: E402

load_dotenv()

SILENCE_FRAME = b"\xff" * FRAME_BYTES


def load_wav_as_ulaw(path: str) -> bytes:
    with wave.open(path, "rb") as w:
        channels, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        frames = w.readframes(n)
    if width != 2:
        raise SystemExit(f"{path}: need a 16-bit PCM WAV (got {8 * width}-bit)")
    pcm = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return pcm16_to_ulaw(resample(pcm, rate, 8000))


class FakeTwilio:
    def __init__(self, ws) -> None:
        self.ws = ws
        self.sid = "MZsimulated0001"
        self.mic: asyncio.Queue[bytes] = asyncio.Queue()
        self.playback_end = 0.0
        self.last_media_at = 0.0
        self.bot_audio = bytearray()
        self.pending_marks: dict[str, asyncio.Task] = {}
        self.clears = 0
        self.turn_started_at: float | None = None
        self.latencies: list[float] = []

    async def send(self, obj: dict) -> None:
        await self.ws.send(json.dumps(obj))

    async def mic_loop(self) -> None:
        """Send one 20 ms frame every 20 ms on an absolute clock, like a real phone."""
        seq, t0 = 0, time.perf_counter()
        while True:
            frame = self.mic.get_nowait() if not self.mic.empty() else SILENCE_FRAME
            seq += 1
            await self.send(
                {
                    "event": "media",
                    "streamSid": self.sid,
                    "sequenceNumber": str(seq),
                    "media": {"track": "inbound", "chunk": str(seq), "timestamp": str(seq * 20), "payload": base64.b64encode(frame).decode()},
                }
            )
            await asyncio.sleep(max(0.0, t0 + seq * 0.02 - time.perf_counter()))

    async def receive_loop(self) -> None:
        async for raw in self.ws:
            msg = json.loads(raw)
            now = time.perf_counter()
            event = msg.get("event")
            if event == "media":
                chunk = base64.b64decode(msg["media"]["payload"])
                if self.turn_started_at is not None:
                    latency = (now - self.turn_started_at) * 1000
                    self.latencies.append(latency)
                    print(f"   <- first bot audio after {latency:.0f} ms (caller-side voice-to-voice)")
                    self.turn_started_at = None
                self.playback_end = max(now, self.playback_end) + len(chunk) / 8000
                self.last_media_at = now
                self.bot_audio.extend(chunk)
            elif event == "mark":
                name = msg["mark"]["name"]
                self.pending_marks[name] = asyncio.create_task(self._echo_mark(name, self.playback_end))
            elif event == "clear":
                self.clears += 1
                self.playback_end = now
                print("   !! server sent `clear`: bot audio flushed (barge-in)")
                for name, task in list(self.pending_marks.items()):
                    task.cancel()
                    await self._send_mark(name)  # Twilio returns cleared marks immediately

    async def _echo_mark(self, name: str, at: float) -> None:
        await asyncio.sleep(max(0.0, at - time.perf_counter()))
        await self._send_mark(name)

    async def _send_mark(self, name: str) -> None:
        self.pending_marks.pop(name, None)
        await self.send({"event": "mark", "streamSid": self.sid, "mark": {"name": name}})

    def bot_idle(self, quiet_s: float = 0.8) -> bool:
        now = time.perf_counter()
        return now > self.playback_end + quiet_s and now > self.last_media_at + quiet_s

    async def wait_for_reply_then_idle(self, timeout: float) -> None:
        """Wait until the bot has started answering (if we're expecting an
        answer) and then finished playing it."""
        deadline = time.perf_counter() + timeout
        while (self.turn_started_at is not None or self.last_media_at == 0) and time.perf_counter() < deadline:
            await asyncio.sleep(0.05)
        while not self.bot_idle() and time.perf_counter() < deadline:
            await asyncio.sleep(0.05)

    async def say(self, ulaw: bytes) -> None:
        frames = [ulaw[i : i + FRAME_BYTES].ljust(FRAME_BYTES, b"\xff") for i in range(0, len(ulaw), FRAME_BYTES)]
        for f in frames:
            self.mic.put_nowait(f)
        while not self.mic.empty():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.02)  # last frame on the wire
        self.turn_started_at = time.perf_counter()


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="ws://localhost:8000/media-stream")
    p.add_argument("--wav", action="append", required=True, help="caller audio (16-bit WAV); repeat for more turns")
    p.add_argument("--interrupt-wav", help="spoken over the bot's first reply to test barge-in")
    p.add_argument("--interrupt-after", type=float, default=1.2, help="seconds into the reply to interrupt")
    p.add_argument("--out", default="bot_reply.wav", help="where to save everything the bot said")
    p.add_argument("--token", default=os.getenv("MEDIA_STREAM_TOKEN", ""),
                   help="stream token the server expects (MEDIA_STREAM_TOKEN; default from env)")
    args = p.parse_args()

    turns = [load_wav_as_ulaw(w) for w in args.wav]
    interrupt = load_wav_as_ulaw(args.interrupt_wav) if args.interrupt_wav else None

    async with websockets.connect(args.url, max_size=None) as ws:
        tw = FakeTwilio(ws)
        await tw.send({"event": "connected", "protocol": "Call", "version": "1.0.0"})
        await tw.send(
            {
                "event": "start",
                "streamSid": tw.sid,
                "start": {
                    "streamSid": tw.sid,
                    "callSid": f"CAsim{int(time.time())}",
                    "tracks": ["inbound"],
                    "customParameters": {"token": args.token},
                    "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
                },
            }
        )
        receiver = asyncio.create_task(tw.receive_loop())
        mic = asyncio.create_task(tw.mic_loop())

        print("== waiting for greeting")
        await tw.wait_for_reply_then_idle(timeout=15)

        for i, audio in enumerate(turns, 1):
            print(f"== caller turn {i}: speaking {len(audio) / 8000:.1f}s of audio")
            await tw.say(audio)
            if interrupt is not None and i == 1:
                while tw.turn_started_at is not None:
                    await asyncio.sleep(0.02)
                await asyncio.sleep(args.interrupt_after)
                print("== caller interrupts the bot")
                await tw.say(interrupt)
            await tw.wait_for_reply_then_idle(timeout=20)

        await tw.send({"event": "stop", "streamSid": tw.sid})
        await asyncio.sleep(0.3)
        mic.cancel()
        receiver.cancel()

    pcm = ulaw_to_pcm16(bytes(tw.bot_audio))
    with wave.open(args.out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(pcm.tobytes())

    print("\n== summary")
    for i, ms in enumerate(tw.latencies, 1):
        print(f"   turn {i}: {ms:.0f} ms voice-to-voice")
    if tw.latencies:
        print(f"   mean: {sum(tw.latencies) / len(tw.latencies):.0f} ms")
    print(f"   barge-ins (clear events): {tw.clears}")
    print(f"   bot audio saved to {args.out} ({len(pcm) / 8000:.1f}s)")


if __name__ == "__main__":
    asyncio.run(main())
