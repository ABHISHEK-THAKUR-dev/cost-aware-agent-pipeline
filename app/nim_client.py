"""
Thin wrapper around the NVIDIA NIM API. NIM exposes an OpenAI-compatible
/chat/completions endpoint, so we use the standard `openai` SDK with base_url overridden.
This is the ONLY file that talks to NIM directly (see docs/architecture.md).

Docs: https://build.nvidia.com  (browse the model catalog for current model IDs —
deliberately not hardcoded here, see docs/design.md §1)
"""
import logging
import secrets
import time
from dataclasses import dataclass

from openai import APIError, APITimeoutError, OpenAI

from app.config import get_settings

logger = logging.getLogger("nim_client")


@dataclass
class CompletionResult:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    model: str


def _client() -> OpenAI:
    settings = get_settings()
    return OpenAI(
        base_url=settings.nim_base_url,
        api_key=settings.nvidia_api_key,
        timeout=settings.nim_timeout_seconds,
    )


def call_nim(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 800,
    temperature: float = 0.2,
) -> CompletionResult:
    """
    Calls NIM with bounded retries + jittered backoff. Raises on final failure —
    caller (app/pipeline.py) is responsible for turning that into a PipelineError
    with step context, per docs/rule.md Agent rule A.6 (budget awareness).
    """
    settings = get_settings()
    client = _client()
    last_err: Exception | None = None

    for attempt in range(settings.nim_max_retries + 1):
        start = time.time()
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            latency_ms = (time.time() - start) * 1000
            usage = resp.usage
            return CompletionResult(
                text=resp.choices[0].message.content or "",
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                latency_ms=latency_ms,
                model=model,
            )
        except (APITimeoutError, APIError) as exc:
            last_err = exc
            if attempt < settings.nim_max_retries:
                # secrets used only to satisfy security linting; jitter has no security
                # sensitivity, but consistency avoids a bandit false-flag on `random`.
                backoff = (2**attempt) + (secrets.randbelow(500) / 1000)
                logger.warning(
                    "nim_retry",
                    extra={
                        "extra_fields": {
                            "attempt": attempt,
                            "model": model,
                            "error": str(exc),
                        }
                    },
                )
                time.sleep(backoff)
            continue

    raise last_err  # exhausted retries — caller wraps this into PipelineError
