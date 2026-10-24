"""The one place the backend talks to an external AI model (Ask ANAVA assistant).

Provider is chosen by settings.ai_provider:
  rules   no external call at all (the assistant answers from safety rules,
          patient-data templates and the stored FAQ only)
  nvidia  NVIDIA API Catalog (build.nvidia.com, free credits for the demo),
          OpenAI-compatible chat completions over httpx
  claude  reserved for the paid upgrade after leadership approval; until it is
          wired it behaves exactly like "rules"

Callers never see provider errors: every failure comes back as an AIResult with
text=None and an outcome the caller logs to ops.ai_usage_log, then the caller
shows its fallback reply. Only text we built (redacted question, approved FAQ
text) is ever sent; patient records are never passed in here.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import httpx

from app.config import get_settings

NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
# Reasoning models (Nemotron, DeepSeek R1, Qwen3) may put their thinking inline in the reply.
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


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
    return s.ai_provider == "nvidia" and bool(s.nvidia_api_key)


async def generate(system: str, turns: list[tuple[str, str]], *, max_output_tokens: int = 300) -> AIResult:
    """turns = [(role, text)], role 'patient' or 'assistant', oldest first, the
    last one being the question to answer."""
    s = get_settings()
    if not provider_enabled():
        return AIResult(None, "error", s.ai_provider, "", error_code="provider_disabled")

    body: dict = {
        "model": s.nvidia_model,
        "messages": [{"role": "system", "content": system}]
        + [{"role": "user" if role == "patient" else "assistant", "content": text} for role, text in turns],
        "max_tokens": max_output_tokens,
        "temperature": 0.2,
        "stream": False,
    }
    if s.nvidia_disable_thinking and "nemotron" in s.nvidia_model:
        # Nemotron 3.x models think by default; /no_think in the prompt is ignored.
        body["chat_template_kwargs"] = {"enable_thinking": False}
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=s.ai_timeout_seconds) as client:
            resp = await client.post(
                NVIDIA_URL,
                headers={"authorization": f"Bearer {s.nvidia_api_key or ''}", "accept": "application/json"},
                json=body,
            )
    except httpx.TimeoutException:
        return AIResult(None, "timeout", "nvidia", s.nvidia_model, latency_ms=_ms(started))
    except httpx.HTTPError as exc:
        return AIResult(None, "error", "nvidia", s.nvidia_model, latency_ms=_ms(started), error_code=type(exc).__name__)

    latency = _ms(started)
    if resp.status_code == 429:
        return AIResult(None, "rate_limited", "nvidia", s.nvidia_model, latency_ms=latency, error_code="429")
    if resp.status_code == 402:
        # Free trial credits used up.
        return AIResult(None, "quota_exhausted", "nvidia", s.nvidia_model, latency_ms=latency, error_code="402")
    if resp.status_code >= 400:
        return AIResult(None, "error", "nvidia", s.nvidia_model, latency_ms=latency, error_code=str(resp.status_code))

    data = resp.json()
    usage = data.get("usage") or {}
    message = ((data.get("choices") or [{}])[0].get("message")) or {}
    text = _THINK.sub("", message.get("content") or "").strip() or None
    return AIResult(
        text,
        "ok" if text else "error",
        "nvidia",
        s.nvidia_model,
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
        latency_ms=latency,
        error_code=None if text else "empty_reply",
    )


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
