"""
DeepSeek client setup (OpenAI-SDK-compatible) plus a retry wrapper with
exponential backoff for rate limits and 5xx errors.

Two models are used across the codebase:
  deepseek-chat     -- fast/cheap: news scoring, significance gating.
  deepseek-reasoner -- expensive/capable: full signal card generation.
"""

from __future__ import annotations

import asyncio
import logging

from openai import APIStatusError, AsyncOpenAI, RateLimitError

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_FAST_MODEL, DEEPSEEK_REASONING_MODEL

logger = logging.getLogger("ai.engine")

client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

_MAX_RETRIES = 3
_BASE_BACKOFF_SECONDS = 2.0


async def _call_with_retry(model: str, messages: list[dict], **kwargs) -> str | None:
    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = await client.chat.completions.create(model=model, messages=messages, **kwargs)
            return response.choices[0].message.content
        except RateLimitError as exc:
            last_error = exc
            wait = _BASE_BACKOFF_SECONDS * (2**attempt)
            logger.warning("DeepSeek rate limited (attempt %s/%s), waiting %.1fs", attempt + 1, _MAX_RETRIES, wait)
            await asyncio.sleep(wait)
        except APIStatusError as exc:
            last_error = exc
            if 500 <= exc.status_code < 600:
                wait = _BASE_BACKOFF_SECONDS * (2**attempt)
                logger.warning(
                    "DeepSeek server error %s (attempt %s/%s), waiting %.1fs",
                    exc.status_code,
                    attempt + 1,
                    _MAX_RETRIES,
                    wait,
                )
                await asyncio.sleep(wait)
            else:
                logger.error("DeepSeek API error %s: %s", exc.status_code, exc)
                return None
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected DeepSeek call failure: %s", exc)
            return None

    logger.error("DeepSeek call failed after %s retries: %s", _MAX_RETRIES, last_error)
    return None


async def chat_completion_json(system_prompt: str, user_prompt: str) -> str | None:
    """Fast/cheap call using deepseek-chat, expecting a JSON-shaped response."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    return await _call_with_retry(DEEPSEEK_FAST_MODEL, messages, temperature=0.2)


async def chat_completion_text(system_prompt: str, user_prompt: str) -> str | None:
    """Fast/cheap call using deepseek-chat, expecting free-form text (e.g. narrative posts)."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    return await _call_with_retry(DEEPSEEK_FAST_MODEL, messages, temperature=0.6)


async def reasoning_completion(system_prompt: str, user_prompt: str) -> str | None:
    """Expensive/capable call using deepseek-reasoner, for full signal card generation."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    return await _call_with_retry(DEEPSEEK_REASONING_MODEL, messages, temperature=0.4)
