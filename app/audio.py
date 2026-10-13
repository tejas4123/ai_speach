"""Audio helpers: G.711 mu-law codec, resampling and loudness.

Phone audio (PSTN) and Twilio Media Streams use 8 kHz, 8-bit mu-law, mono.
Twilio sends it base64-encoded in 20 ms frames (160 samples = 160 bytes).

mu-law is a logarithmic companding scheme: it spends more of its 8 bits on
quiet sounds (where the ear is sensitive) and fewer on loud ones, giving
roughly 14-bit dynamic range in 8 bits. Most ML models (Whisper, Silero VAD)
want linear PCM instead, so we decode to 16-bit PCM and resample as needed.

Python 3.13 removed the stdlib `audioop` module, so the codec is written
here with numpy (vectorised, no per-sample Python loop).
"""

from __future__ import annotations

import numpy as np

TELEPHONY_RATE = 8000
FRAME_MS = 20
FRAME_BYTES = TELEPHONY_RATE * FRAME_MS // 1000  # 160

_BIAS = 0x84  # 132
_CLIP = 32635


def _build_decode_table() -> np.ndarray:
    table = np.zeros(256, dtype=np.int16)
    for byte in range(256):
        u = ~byte & 0xFF
        sign = u & 0x80
        exponent = (u >> 4) & 0x07
        mantissa = u & 0x0F
        sample = (((mantissa << 3) + _BIAS) << exponent) - _BIAS
        table[byte] = -sample if sign else sample
    return table


_DECODE_TABLE = _build_decode_table()


def ulaw_to_pcm16(data: bytes) -> np.ndarray:
    """Decode mu-law bytes to int16 PCM samples (table lookup)."""
    return _DECODE_TABLE[np.frombuffer(data, dtype=np.uint8)]


def pcm16_to_ulaw(pcm: np.ndarray) -> bytes:
    """Encode int16 PCM samples to mu-law bytes."""
    x = np.asarray(pcm, dtype=np.int32)
    sign = (x < 0).astype(np.int32) << 7
    magnitude = np.minimum(np.abs(x), _CLIP) + _BIAS
    exponent = np.clip(np.floor(np.log2(magnitude)).astype(np.int32) - 7, 0, 7)
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    ulaw = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return ulaw.astype(np.uint8).tobytes()


def resample(pcm: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear-interpolation resampler.

    Good enough for a demo and for upsampling 8k -> 16k. For production
    downsampling use a proper polyphase filter (soxr, scipy resample_poly)
    so high frequencies don't alias into the speech band.
    """
    pcm = np.asarray(pcm)
    if src_rate == dst_rate or pcm.size == 0:
        return pcm.astype(np.int16, copy=True)
    n_out = int(round(pcm.size * dst_rate / src_rate))
    t_src = np.arange(pcm.size) / src_rate
    t_dst = np.arange(n_out) / dst_rate
    out = np.interp(t_dst, t_src, pcm.astype(np.float32))
    return np.clip(out, -32768, 32767).astype(np.int16)


def rms_dbfs(pcm: np.ndarray) -> float:
    """Loudness of a frame in dB relative to full scale (0 dBFS = max)."""
    if pcm.size == 0:
        return -120.0
    rms = np.sqrt(np.mean(np.square(pcm.astype(np.float64))))
    if rms <= 0:
        return -120.0
    return float(20 * np.log10(rms / 32768.0))

