"""Twilio specifics: webhook signature checks, TwiML, and the REST calls we make.

Kept apart from main.py so the security-sensitive parts can be unit tested
without a server, and so the transfer tool can reuse the REST client.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
from xml.sax.saxutils import escape, quoteattr

import httpx

log = logging.getLogger(__name__)

API = "https://api.twilio.com/2010-04-01/Accounts/{sid}"
CALL_SID = re.compile(r"^CA[0-9a-fA-F]{32}$")
E164 = re.compile(r"^\+[1-9]\d{6,14}$")


def twilio_signature(auth_token: str, url: str, params: list[tuple[str, str]]) -> str:
    """Twilio's X-Twilio-Signature: HMAC-SHA1 over the full URL followed by
    every POST parameter as name+value, sorted by name (then value)."""
    payload = url + "".join(k + v for k, v in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def signature_valid(auth_token: str, url: str, params: list[tuple[str, str]], signature: str) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(twilio_signature(auth_token, url, params), signature)


def tokens_match(expected: str, given: str | None) -> bool:
    """Constant-time comparison; an unset expected token never matches."""
    return bool(expected) and hmac.compare_digest(expected.encode(), (given or "").encode())


def stream_twiml(host: str, token: str) -> str:
    """TwiML that connects the call to our Media Stream WebSocket. The token
    comes back to us in the stream's `start` message (customParameters)."""
    url = quoteattr(f"wss://{host}/media-stream")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Connect><Stream url={url}><Parameter name="token" value={quoteattr(token)} />'
        "</Stream></Connect></Response>"
    )


def stream_token_ok(start_msg: dict, expected: str) -> bool:
    params = start_msg.get("start", {}).get("customParameters") or {}
    return tokens_match(expected, params.get("token"))


class TwilioClient:
    def __init__(self, account_sid: str, auth_token: str, from_number: str, timeout: float = 15.0) -> None:
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.account_sid and self.auth_token and self.from_number)

    async def _post(self, path: str, data: dict) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await client.post(
                API.format(sid=self.account_sid) + path, auth=(self.account_sid, self.auth_token), data=data
            )

    async def create_call(self, to: str, twiml: str) -> httpx.Response:
        return await self._post("/Calls.json", {"To": to, "From": self.from_number, "Twiml": twiml})

    async def transfer(self, call_sid: str, number: str, say: str) -> httpx.Response:
        """Replace the live call's instructions: Twilio ends our Media Stream,
        speaks `say`, and dials the human agent."""
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<Response><Say>{escape(say)}</Say><Dial>{escape(number)}</Dial></Response>"
        )
        return await self._post(f"/Calls/{call_sid}.json", {"Twiml": twiml})
