"""Settings loaded from environment variables (and a local .env file)."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    # Public hostname Twilio should connect back to, e.g. "abcd-1234.ngrok-free.app".
    public_host: str

    # Speech-to-text (Deepgram streaming)
    deepgram_api_key: str
    stt_model: str
    stt_language: str
    endpointing_ms: int
    utterance_end_ms: int

    # LLM (any OpenAI-compatible API: OpenAI, Groq, Together, a local vLLM ...)
    llm_api_key: str
    llm_base_url: str | None
    llm_model: str
    llm_temperature: float
    llm_timeout_s: float
    max_history_messages: int

    # Text-to-speech
    tts_provider: str  # "deepgram" | "elevenlabs"
    deepgram_tts_model: str
    elevenlabs_api_key: str
    elevenlabs_voice_id: str
    elevenlabs_model: str

    # Turn-taking / barge-in
    vad_provider: str  # "energy" | "silero"
    vad_energy_threshold_dbfs: float
    barge_in_min_speech_ms: int
    barge_in_min_words: int

    # RAG
    embeddings_enabled: bool
    embedding_model: str

    # Compliance
    redact_before_llm: bool

    # Twilio (only needed for the outbound /call endpoint)
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_phone_number: str
    demo_api_token: str
    # Number to <Dial> when the agent transfers to a human. Empty = no transfer.
    human_agent_number: str
    # Shared secret Twilio echoes back in the Media Stream `start` message.
    # Random per process unless set (set it when running several instances).
    media_stream_token: str

    greeting: str
    log_dir: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            public_host=_get("PUBLIC_HOST"),
            deepgram_api_key=_get("DEEPGRAM_API_KEY"),
            stt_model=_get("STT_MODEL", "nova-2"),
            stt_language=_get("STT_LANGUAGE", "en-IN"),
            endpointing_ms=_int("ENDPOINTING_MS", 300),
            utterance_end_ms=_int("UTTERANCE_END_MS", 1000),
            llm_api_key=_get("LLM_API_KEY"),
            llm_base_url=_get("LLM_BASE_URL") or None,
            llm_model=_get("LLM_MODEL", "gpt-4o-mini"),
            llm_temperature=_float("LLM_TEMPERATURE", 0.3),
            llm_timeout_s=_float("LLM_TIMEOUT_S", 15.0),
            max_history_messages=_int("MAX_HISTORY_MESSAGES", 40),
            tts_provider=_get("TTS_PROVIDER", "deepgram").lower(),
            deepgram_tts_model=_get("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en"),
            elevenlabs_api_key=_get("ELEVENLABS_API_KEY"),
            elevenlabs_voice_id=_get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"),
            elevenlabs_model=_get("ELEVENLABS_MODEL", "eleven_flash_v2_5"),
            vad_provider=_get("VAD_PROVIDER", "energy").lower(),
            vad_energy_threshold_dbfs=_float("VAD_ENERGY_THRESHOLD_DBFS", -35.0),
            barge_in_min_speech_ms=_int("BARGE_IN_MIN_SPEECH_MS", 250),
            barge_in_min_words=_int("BARGE_IN_MIN_WORDS", 2),
            embeddings_enabled=_bool("EMBEDDINGS_ENABLED", False),
            embedding_model=_get("EMBEDDING_MODEL", "text-embedding-3-small"),
            redact_before_llm=_bool("REDACT_BEFORE_LLM", True),
            twilio_account_sid=_get("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=_get("TWILIO_AUTH_TOKEN"),
            twilio_phone_number=_get("TWILIO_PHONE_NUMBER"),
            demo_api_token=_get("DEMO_API_TOKEN"),
            human_agent_number=_get("HUMAN_AGENT_NUMBER"),
            media_stream_token=_get("MEDIA_STREAM_TOKEN") or secrets.token_urlsafe(24),
            greeting=_get(
                "GREETING",
                "Hi, thanks for calling Kiran Electronics support. How can I help you today?",
            ),
            log_dir=_get("LOG_DIR", "logs"),
        )
