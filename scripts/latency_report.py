"""Summarise logs/latency.jsonl into a p50/p95 table (paste it into the README).

  python scripts/latency_report.py [logs/latency.jsonl]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

METRICS = [
    ("filler_ms", "Time to tool filler (tool turns only)"),
    ("llm_ttft_ms", "LLM time to first token"),
    ("first_sentence_ms", "First token -> first full sentence"),
    ("tts_ttfb_ms", "TTS time to first audio byte"),
    ("server_total_ms", "End of caller speech -> first audio sent"),
]


def main() -> None:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "logs/latency.jsonl")
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    # Turn 1 is the static greeting (no LLM), so leave it out.
    rows = [r for r in rows if r.get("turn", 0) > 1]
    if not rows:
        raise SystemExit(f"no caller turns in {path} yet")
    print(f"{len(rows)} turns, {sum(r['interrupted'] for r in rows)} interrupted\n")
    print("| Stage | p50 (ms) | p95 (ms) |")
    print("|---|---|---|")
    for key, label in METRICS:
        values = [r[key] for r in rows if r.get(key) is not None]
        if values:
            print(f"| {label} | {np.percentile(values, 50):.0f} | {np.percentile(values, 95):.0f} |")


if __name__ == "__main__":
    main()
