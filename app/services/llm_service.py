"""Thin provider-agnostic LLM client (Anthropic + Gemini over HTTPS via httpx)."""
import json
import re
from dataclasses import dataclass
from typing import Optional
import asyncio


import httpx

from app.config import Settings


class LLMError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


def extract_json(text: str) -> dict:
    """Parse JSON from model output, tolerating ```json fences and stray prose."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


async def _call_anthropic(system: str, user: str, model: str, s: Settings) -> LLMResult:
    if not s.anthropic_api_key:
        raise LLMError("ANTHROPIC_API_KEY is not configured.")
    async with httpx.AsyncClient(timeout=s.llm_timeout_seconds) as c:
        r = await c.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": s.anthropic_api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": model, "max_tokens": s.llm_max_tokens, "system": system,
                  "messages": [{"role": "user", "content": user}]},
        )
    if r.status_code != 200:
        raise LLMError(f"Anthropic API error {r.status_code}")
    d = r.json()
    text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    u = d.get("usage", {})
    return LLMResult(text, model, u.get("input_tokens"), u.get("output_tokens"))


async def _call_gemini(system: str, user: str, model: str, s: Settings) -> LLMResult:
    if not s.gemini_api_key:
            raise LLMError(f"Gemini API error {r.status_code}", r.status_code)
    async with httpx.AsyncClient(timeout=s.llm_timeout_seconds) as c:
        r = await c.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": s.gemini_api_key},
            json={"systemInstruction": {"parts": [{"text": system}]},
                  "contents": [{"role": "user", "parts": [{"text": user}]}],
                  "generationConfig": {"responseMimeType": "application/json",
                                       "maxOutputTokens": s.llm_max_tokens}},
        )
    if r.status_code != 200:
        raise LLMError(f"Gemini API error {r.status_code}")
    d = r.json()
    try:
        text = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        raise LLMError("Gemini returned no content.")
    u = d.get("usageMetadata", {})
    return LLMResult(text, model, u.get("promptTokenCount"), u.get("candidatesTokenCount"))


_PROVIDERS = {"anthropic": _call_anthropic, "gemini": _call_gemini}


async def complete(system: str, user: str, provider: str, model: str, s: Settings) -> LLMResult:
    fn = _PROVIDERS.get(provider)
    if not fn:
        raise LLMError(f"Unknown provider '{provider}'.")
    last: Exception = LLMError("LLM call failed.")
    for delay in (0, 2, 5, 10):          # retry transient errors with backoff
        if delay:
            await asyncio.sleep(delay)
        try:
            return await fn(system, user, model, s)
        except LLMError as e:
            if e.status not in (429, 500, 502, 503, 504):
                raise                     # key/model problems: don't retry
            last = e
        except httpx.HTTPError as e:
            last = LLMError(f"{provider} request failed: {type(e).__name__}")
    raise last
