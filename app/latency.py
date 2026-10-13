"""Per-turn latency instrumentation.

Each caller turn gets a TurnTimer. Pipeline stages call `mark(name)` and
only the first call per name counts. At the end of the turn we log one JSON
line with the stage-by-stage breakdown, so p50/p95 can be computed later
(scripts/latency_report.py).

Note: `server_total_ms` starts when Deepgram tells us the caller finished
(speech_final), so it excludes the endpointing silence itself and phone
network time. The call simulator measures true voice-to-voice from the
caller's side.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path


def now_ms() -> float:
    return time.perf_counter() * 1000


@dataclass
class TurnTimer:
    call_sid: str
    turn: int
    marks: dict[str, float] = field(default_factory=dict)
    interrupted: bool = False

    def mark(self, name: str) -> None:
        self.marks.setdefault(name, now_ms())

    def _delta(self, start: str, end: str) -> float | None:
        if start in self.marks and end in self.marks:
            return round(self.marks[end] - self.marks[start], 1)
        return None

    def report(self) -> dict:
        return {
            "call_sid": self.call_sid,
            "turn": self.turn,
            "interrupted": self.interrupted,
            "filler_ms": self._delta("llm_request", "filler"),
            "llm_ttft_ms": self._delta("llm_request", "llm_first_token"),
            "first_sentence_ms": self._delta("llm_first_token", "first_sentence"),
            "tts_ttfb_ms": self._delta("tts_request", "tts_first_byte"),
            "server_total_ms": self._delta("user_turn_end", "first_audio_sent"),
        }


class LatencyLogger:
    def __init__(self, log_dir: str) -> None:
        self.path = Path(log_dir) / "latency.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, timer: TurnTimer) -> dict:
        row = timer.report()
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        return row
