"""Voice Activity Detection, used for fast barge-in.

Two VADs are available:

* EnergyVAD: loudness threshold. Zero dependencies and microseconds per
  frame, but it can't tell speech from a door slam or a TV in the background.
* SileroVAD: a small neural model (~2 MB) that classifies speech vs non-speech.
  Far more robust to noise. Needs `pip install silero-vad` (pulls in torch).

The SpeechOnsetDetector sits on top of either one and only fires after
speech has lasted `min_speech_ms`, so a cough or a click doesn't cut the
bot off mid-sentence.
"""

from __future__ import annotations

import logging
from typing import Protocol

import numpy as np

from .audio import FRAME_MS, TELEPHONY_RATE, rms_dbfs

log = logging.getLogger(__name__)


class VAD(Protocol):
    def is_speech(self, pcm16: np.ndarray) -> bool: ...

    def reset(self) -> None: ...


class EnergyVAD:
    def __init__(self, threshold_dbfs: float = -35.0) -> None:
        self.threshold_dbfs = threshold_dbfs

    def is_speech(self, pcm16: np.ndarray) -> bool:
        return rms_dbfs(pcm16) > self.threshold_dbfs

    def reset(self) -> None:
        pass


class SileroVAD:
    """Silero VAD at 8 kHz. The model consumes 256-sample windows (32 ms)."""

    WINDOW = 256  # samples at 8 kHz (512 at 16 kHz)

    def __init__(self, threshold: float = 0.5) -> None:
        try:
            import torch
            from silero_vad import load_silero_vad
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "VAD_PROVIDER=silero needs `pip install silero-vad`"
            ) from exc
        self._torch = torch
        self.model = load_silero_vad()
        self.threshold = threshold
        self._buffer = np.zeros(0, dtype=np.float32)
        self._last = False

    def is_speech(self, pcm16: np.ndarray) -> bool:
        samples = pcm16.astype(np.float32) / 32768.0
        self._buffer = np.concatenate([self._buffer, samples])
        while self._buffer.size >= self.WINDOW:
            window, self._buffer = self._buffer[: self.WINDOW], self._buffer[self.WINDOW :]
            prob = self.model(self._torch.from_numpy(window.copy()), TELEPHONY_RATE).item()
            self._last = prob >= self.threshold
        return self._last

    def reset(self) -> None:
        self._buffer = np.zeros(0, dtype=np.float32)
        self._last = False
        self.model.reset_states()


class SpeechOnsetDetector:
    """Fires once when speech has been continuous for `min_speech_ms`.

    Short gaps (up to `max_gap_ms`) don't reset the counter, because syllables
    have tiny pauses between them.
    """

    def __init__(self, vad: VAD, min_speech_ms: int = 250, max_gap_ms: int = 100) -> None:
        self.vad = vad
        self.min_speech_ms = min_speech_ms
        self.max_gap_ms = max_gap_ms
        self.speech_ms = 0
        self.gap_ms = 0
        self.fired = False

    def feed(self, pcm16: np.ndarray) -> bool:
        frame_ms = int(1000 * pcm16.size / TELEPHONY_RATE) or FRAME_MS
        if self.vad.is_speech(pcm16):
            self.speech_ms += frame_ms
            self.gap_ms = 0
        else:
            self.gap_ms += frame_ms
            if self.gap_ms > self.max_gap_ms:
                self.speech_ms = 0
                self.fired = False
        if not self.fired and self.speech_ms >= self.min_speech_ms:
            self.fired = True
            return True
        return False

    def reset(self) -> None:
        self.speech_ms = 0
        self.gap_ms = 0
        self.fired = False
        self.vad.reset()


def build_vad(provider: str, energy_threshold_dbfs: float) -> VAD:
    if provider == "silero":
        return SileroVAD()
    return EnergyVAD(energy_threshold_dbfs)
