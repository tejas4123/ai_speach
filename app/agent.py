"""The LLM side: a streaming chat completion loop with function calling.

Works with any OpenAI-compatible API. Groq (free tier, very fast
time-to-first-token) and OpenAI both work: set LLM_BASE_URL / LLM_MODEL.

Flow for one caller turn:
1. Stream a completion with tools attached.
2. Text deltas are yielded immediately (they go to the sentence chunker -> TTS).
3. If the model calls tools instead, we (a) yield a short filler phrase so the
   line isn't silent, (b) run the tools concurrently, (c) append the tool
   call + results to history, and (d) stream again so the model can phrase
   the answer. Capped at `max_tool_rounds`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from .tools import ToolRegistry

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Asha, a phone support agent for Kiran Electronics, an Indian online electronics store.

You are speaking on a phone call, so:
- Keep replies short: one to three sentences. One question at a time.
- Plain spoken language only. No lists, markdown, emojis, or URLs.
- Say amounts naturally, like "four hundred and ninety nine rupees".
- Read order IDs back character by character, like "K E one zero zero one".
- Reply in the caller's language: English, Hindi or Hinglish, matching how they speak.

Facts:
- For any policy question (returns, refunds, delivery, warranty, payments), call search_knowledge_base first and answer only from what it returns.
- For anything about a specific order, call get_order_status. Never invent order details.
- If the tools don't have the answer, say so honestly and offer a human agent.
- If the caller asks for a human, is angry, or you can't help after two tries, call transfer_to_human.
- Parts of the transcript like [AADHAAR] or [CARD] were redacted for privacy. Never ask the caller to repeat sensitive numbers; payment issues go to a human agent.
"""


def recent(history: list[dict], limit: int) -> list[dict]:
    """The last `limit` messages, starting at a user message so a tool result
    is never sent without the assistant tool call it answers. Keeps long calls
    from growing the prompt (and its cost and latency) without bound."""
    if limit <= 0 or len(history) <= limit:
        return history
    tail = history[-limit:]
    for i, msg in enumerate(tail):
        if msg["role"] == "user":
            return tail[i:]
    # No user message in the window: fall back to the current turn, whole.
    last_user = max((i for i, m in enumerate(history) if m["role"] == "user"), default=0)
    return history[last_user:]


class Agent:
    def __init__(
        self,
        client,
        model: str,
        tools: ToolRegistry,
        temperature: float = 0.3,
        max_tool_rounds: int = 3,
        max_history_messages: int = 40,
        filler: str = "One moment, let me check that. ",
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self.client = client
        self.model = model
        self.tools = tools
        self.temperature = temperature
        self.max_tool_rounds = max_tool_rounds
        self.max_history_messages = max_history_messages
        self.filler = filler
        self.system_prompt = system_prompt

    async def stream_reply(self, history: list[dict]) -> AsyncIterator[str]:
        """Yield text deltas for the next assistant turn.

        Appends tool-call and tool-result messages to `history` (only after
        the tools finish, so a cancelled turn never leaves a dangling tool
        call). The final spoken text is appended by the caller, because only
        it knows how much of the reply the caller actually heard.
        """
        spoke_anything = False
        for round_no in range(self.max_tool_rounds + 1):
            allow_tools = round_no < self.max_tool_rounds and len(self.tools) > 0
            kwargs = dict(
                model=self.model,
                messages=[{"role": "system", "content": self.system_prompt}, *recent(history, self.max_history_messages)],
                temperature=self.temperature,
                stream=True,
            )
            if allow_tools:
                kwargs["tools"] = self.tools.schemas()
            stream = await self.client.chat.completions.create(**kwargs)

            round_text = ""
            calls: dict[int, dict] = {}
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if getattr(delta, "content", None):
                    round_text += delta.content
                    spoke_anything = True
                    yield delta.content
                for tc in getattr(delta, "tool_calls", None) or []:
                    if not spoke_anything and self.filler:
                        spoke_anything = True
                        yield self.filler
                    slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function is not None:
                        if tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function.arguments:
                            slot["arguments"] += tc.function.arguments

            if not calls:
                return

            ordered = [calls[i] for i in sorted(calls)]
            log.info("tool calls: %s", [(c["name"], c["arguments"]) for c in ordered])
            results = await asyncio.gather(*(self.tools.call(c["name"], c["arguments"]) for c in ordered))
            history.append(
                {
                    "role": "assistant",
                    "content": round_text or None,
                    "tool_calls": [
                        {
                            "id": c["id"],
                            "type": "function",
                            "function": {"name": c["name"], "arguments": c["arguments"] or "{}"},
                        }
                        for c in ordered
                    ],
                }
            )
            history.extend(
                {"role": "tool", "tool_call_id": c["id"], "content": result}
                for c, result in zip(ordered, results)
            )
