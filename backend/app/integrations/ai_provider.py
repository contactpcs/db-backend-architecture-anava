"""The one place the backend talks to an external AI model (Ask ANAVA assistant).

Provider is chosen by settings.ai_provider:
  rules   no external call at all (the assistant answers from safety rules,
          patient-data templates and the stored FAQ only)
  gemini  Google AI Studio (free tier for the demo), REST over httpx
  claude  reserved for the paid upgrade after leadership approval; until it is
          wired it behaves exactly like "rules"

Callers never see provider errors: every failure comes back as an AIResult with
text=None and an outcome the caller logs to ops.ai_usage_log, then the caller
shows its fallback reply. Only text we built (redacted question, approved FAQ
text) is ever sent; patient records are never passed in here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from app.config import get_settings

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


@dataclass(frozen=True)
class AIResult:
    text: str | None
    outcome: str  # ok | rate_limited | quota_exhausted | error | timeout
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    error_code: str | None = None


def provider_enabled() -> bool:
    s = get_settings()
    return s.ai_provider == "gemini" and bool(s.gemini_api_key)


async def generate(system: str, turns: list[tuple[str, str]], *, max_output_tokens: int = 300) -> AIResult:
    """turns = [(role, text)], role 'patient' or 'assistant', oldest first, the
    last one being the question to answer."""
    s = get_settings()
    if not provider_enabled():
        return AIResult(None, "error", s.ai_provider, "", error_code="provider_disabled")

    generation: dict = {"maxOutputTokens": max_output_tokens, "temperature": 0.2}
    if s.gemini_disable_thinking:
        generation["thinkingConfig"] = {"thinkingBudget": 0}
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user" if role == "patient" else "model", "parts": [{"text": text}]} for role, text in turns],
        "generationConfig": generation,
    }
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=s.ai_timeout_seconds) as client:
            resp = await client.post(
                GEMINI_URL.format(model=s.gemini_model),
                headers={"x-goog-api-key": s.gemini_api_key or "", "content-type": "application/json"},
                json=body,
            )
    except httpx.TimeoutException:
        return AIResult(None, "timeout", "gemini", s.gemini_model, latency_ms=_ms(started))
    except httpx.HTTPError as exc:
        return AIResult(None, "error", "gemini", s.gemini_model, latency_ms=_ms(started), error_code=type(exc).__name__)

    latency = _ms(started)
    if resp.status_code == 429:
        # Google uses 429 RESOURCE_EXHAUSTED for both per-minute and per-day limits.
        return AIResult(None, "rate_limited", "gemini", s.gemini_model, latency_ms=latency, error_code="429")
    if resp.status_code >= 400:
        return AIResult(None, "error", "gemini", s.gemini_model, latency_ms=latency, error_code=str(resp.status_code))

    data = resp.json()
    usage = data.get("usageMetadata") or {}
    parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip() or None
    return AIResult(
        text,
        "ok" if text else "error",
        "gemini",
        s.gemini_model,
        input_tokens=usage.get("promptTokenCount"),
        output_tokens=usage.get("candidatesTokenCount"),
        latency_ms=latency,
        error_code=None if text else "empty_reply",
    )


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
