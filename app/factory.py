"""Builds the LLM agent (with its knowledge base and tools) from settings.
Shared by the server and the text-only chat CLI."""

from __future__ import annotations

import logging
from pathlib import Path

from .agent import Agent
from .config import Settings
from .rag import EmbeddingIndex, KnowledgeBase
from .telephony import CALL_SID, TwilioClient
from .tools import TransferFn, build_default_tools

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def build_transfer(settings: Settings) -> TransferFn | None:
    """A real Twilio transfer, or None when no human number is configured."""
    twilio = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token, settings.twilio_phone_number)
    if not (settings.human_agent_number and twilio.configured):
        return None

    async def transfer(call_sid: str, reason: str) -> dict:
        if not CALL_SID.match(call_sid):
            return {
                "status": "unavailable",
                "message": "This is a test call, not a phone call, so it can't be transferred. "
                "Tell the caller that on a real call a colleague would take over now.",
            }
        resp = await twilio.transfer(call_sid, settings.human_agent_number, "Connecting you to a colleague now. Please hold.")
        if resp.status_code >= 300:
            log.error("transfer failed %s: %s", resp.status_code, resp.text[:300])
            return {"status": "failed", "message": "The transfer didn't go through. Apologise and keep helping."}
        return {"status": "transferring", "message": "The call is being handed to a human now. Say nothing more."}

    return transfer


async def build_agent(settings: Settings) -> Agent:
    from openai import AsyncOpenAI

    if not settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY is not set (see .env.example)")
    # One retry only: on a live call a fast fallback beats a long silence,
    # and errors like insufficient_quota never succeed on retry anyway.
    llm = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout_s,
        max_retries=1,
    )
    embeddings = EmbeddingIndex(llm, settings.embedding_model) if settings.embeddings_enabled else None
    kb = KnowledgeBase.from_directory(DATA_DIR / "kb", embeddings)
    await kb.build_embeddings()
    tools = build_default_tools(kb, DATA_DIR / "orders.json", transfer=build_transfer(settings))
    return Agent(
        llm,
        settings.llm_model,
        tools,
        temperature=settings.llm_temperature,
        max_history_messages=settings.max_history_messages,
    )
