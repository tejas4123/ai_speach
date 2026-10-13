"""End to end over real WebSockets: the actual FastAPI server and CallSession
(with offline STT/LLM/TTS fakes) talking to scripts/simulate_call.py, which
behaves like Twilio. Covers the greeting, two caller turns and one barge-in."""

import socket
import subprocess
import sys
import time
import wave
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def write_speech_like_wav(path: Path, seconds: float) -> None:
    """Amplitude-modulated noise at a speech-like level, 16 kHz."""
    rate = 16000
    n = int(seconds * rate)
    rng = np.random.default_rng(0)
    envelope = 0.6 + 0.4 * np.sin(2 * np.pi * 4 * np.arange(n) / rate)
    pcm = (rng.normal(0, 4000, n) * envelope).clip(-32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def test_simulated_call_end_to_end(tmp_path):
    port = free_port()
    log_dir = tmp_path / "logs"
    server = subprocess.Popen(
        [sys.executable, str(ROOT / "tests" / "fake_server.py"), "--port", str(port), "--log-dir", str(log_dir)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        for _ in range(100):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/health").status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.1)
        else:
            raise AssertionError("server did not start")

        q1, stop, q2 = tmp_path / "q1.wav", tmp_path / "stop.wav", tmp_path / "q2.wav"
        write_speech_like_wav(q1, 1.2)
        write_speech_like_wav(stop, 0.8)
        write_speech_like_wav(q2, 1.0)

        result = subprocess.run(
            [
                sys.executable, str(ROOT / "scripts" / "simulate_call.py"),
                "--url", f"ws://127.0.0.1:{port}/media-stream",
                "--wav", str(q1), "--wav", str(q2),
                "--interrupt-wav", str(stop),
                "--token", "test-token",
                "--out", str(tmp_path / "bot.wav"),
            ],
            cwd=ROOT, capture_output=True, text=True, timeout=90,
        )
        print(result.stdout, result.stderr)
        assert result.returncode == 0
        assert "barge-ins (clear events): 1" in result.stdout
        assert result.stdout.count("caller-side voice-to-voice") == 3

        transcript_files = list((log_dir / "transcripts").glob("*.json"))
        assert len(transcript_files) == 1
        latency_rows = (log_dir / "latency.jsonl").read_text().splitlines()
        assert len(latency_rows) >= 4  # greeting + 3 caller turns
        assert any('"interrupted": true' in row for row in latency_rows)
    finally:
        server.terminate()
        out, _ = server.communicate(timeout=10)
        print(out[-4000:])
