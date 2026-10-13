"""Tools the LLM can call (OpenAI function-calling format).

Tool results are returned as JSON strings and fed back to the model, which
then phrases the answer for the caller. Rule of thumb: facts come from
tools, wording comes from the model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .rag import KnowledgeBase

log = logging.getLogger(__name__)

ToolFn = Callable[..., Awaitable[Any]]
TransferFn = Callable[[str, str], Awaitable[dict]]  # (call_sid, reason) -> result

# Set by CallSession for each turn, so tools know which live call they serve.
CURRENT_CALL_SID: ContextVar[str | None] = ContextVar("current_call_sid", default=None)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: ToolFn

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self, timeout_s: float = 8.0) -> None:
        self._tools: dict[str, Tool] = {}
        self.timeout_s = timeout_s

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)

    async def call(self, name: str, arguments: str) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return json.dumps({"error": f"unknown tool {name}"})
        try:
            kwargs = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return json.dumps({"error": "arguments were not valid JSON"})
        if not isinstance(kwargs, dict):
            return json.dumps({"error": "arguments must be a JSON object"})
        try:
            async with asyncio.timeout(self.timeout_s):
                result = await tool.fn(**kwargs)
        except TimeoutError:
            log.warning("tool %s timed out after %.0f s", name, self.timeout_s)
            return json.dumps({"error": "tool timed out; apologise and offer a human agent"})
        except TypeError as exc:
            return json.dumps({"error": f"bad arguments: {exc}"})
        except Exception as exc:  # never let a tool crash the call
            log.exception("tool %s failed", name)
            return json.dumps({"error": f"tool failed: {exc}"})
        log.info("tool %s(%s) -> %s", name, arguments, str(result)[:200])
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)


def normalize_order_id(spoken: str) -> str:
    """Callers say "K E one zero zero one" or "ke-1001"; STT may write either.
    Map number words to digits and drop spaces/punctuation."""
    words = {
        "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
        "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    }
    tokens = re.findall(r"[a-zA-Z]+|\d+", spoken)
    return "".join(words.get(t.lower(), t) for t in tokens).upper()


def build_default_tools(
    kb: KnowledgeBase, orders_path: str | Path, transfer: TransferFn | None = None
) -> ToolRegistry:
    orders: dict[str, dict] = json.loads(Path(orders_path).read_text(encoding="utf-8"))
    registry = ToolRegistry()

    async def search_knowledge_base(query: str) -> dict:
        results = await kb.search(query, k=3)
        if not results:
            return {"results": [], "note": "Nothing relevant found. Do not guess; offer a human agent."}
        return {"results": [r.chunk.text for r in results]}

    async def get_order_status(order_id: str) -> dict:
        oid = normalize_order_id(order_id)
        order = orders.get(oid)
        if order is None:
            return {"found": False, "order_id": oid, "note": "Ask the caller to repeat the order ID."}
        return {"found": True, "order_id": oid, **order}

    async def transfer_to_human(reason: str) -> dict:
        call_sid = CURRENT_CALL_SID.get()
        log.info("TRANSFER requested call_sid=%s reason=%s", call_sid, reason)
        if transfer is None or not call_sid:
            return {
                "status": "unavailable",
                "message": "No human agent can be reached on this line right now. Apologise, keep helping "
                "if you can, and suggest calling back later. Don't claim you are transferring.",
            }
        return await transfer(call_sid, reason)

    registry.register(
        Tool(
            name="search_knowledge_base",
            description=(
                "Search Kiran Electronics' help articles: returns, refunds, delivery, "
                "warranty, payments, cancellations. Use for any policy question."
            ),
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "The caller's question, rephrased as a search query"}},
                "required": ["query"],
            },
            fn=search_knowledge_base,
        )
    )
    registry.register(
        Tool(
            name="get_order_status",
            description="Look up an order's status, items and delivery date by order ID (format like KE1001).",
            parameters={
                "type": "object",
                "properties": {"order_id": {"type": "string", "description": "Order ID as the caller said it"}},
                "required": ["order_id"],
            },
            fn=get_order_status,
        )
    )
    registry.register(
        Tool(
            name="transfer_to_human",
            description="Transfer the caller to a human agent. Use when asked, when the caller is upset, or when you can't help.",
            parameters={
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
            fn=transfer_to_human,
        )
    )
    return registry
