"""
Posts to the Main Channel and the Alpha AI signals group via the plain
Telegram Bot API (HTTP, not a client library) using aiohttp. Applies dedup
and cooldown checks via state/cooldowns.py before sending.
"""

from __future__ import annotations

import logging

import aiohttp

from config import HTTP_TIMEOUT_SECONDS, INNER_CIRCLE_ID, MAIN_CHANNEL_ID, TELEGRAM_BOT_TOKEN
from state.cooldowns import is_duplicate_content, mark_main_channel_post, remember_content

logger = logging.getLogger("telegram.poster")
logger.info("Poster targets — signals: %s  main channel: %s", INNER_CIRCLE_ID, MAIN_CHANNEL_ID)

_API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


_MAX_TELEGRAM_CHARS = 4096


def _truncate(text: str) -> str:
    """Telegram enforces a 4096-character hard limit per message."""
    if len(text) <= _MAX_TELEGRAM_CHARS:
        return text
    logger.warning("Message truncated from %d to %d chars", len(text), _MAX_TELEGRAM_CHARS)
    return text[: _MAX_TELEGRAM_CHARS - 4] + " ..."


async def _send_message(chat_id: str, text: str) -> bool:
    url = f"{_API_BASE}/sendMessage"
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
    text = _truncate(text)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Try with Markdown first; fall back to plain text if Telegram
            # rejects the formatting (unbalanced * or _ from AI output).
            for parse_mode in ("Markdown", None):
                payload: dict = {"chat_id": chat_id, "text": text}
                if parse_mode:
                    payload["parse_mode"] = parse_mode
                async with session.post(url, json=payload) as resp:
                    body = await resp.text()
                    if resp.status == 200:
                        logger.info(
                            "Message sent to %s (parse_mode=%s, chars=%d)",
                            chat_id, parse_mode or "plain", len(text),
                        )
                        return True
                    if resp.status == 400 and "parse entities" in body and parse_mode:
                        logger.warning("Markdown parse error — retrying as plain text")
                        continue
                    logger.error(
                        "Telegram sendMessage failed chat=%s status=%s: %s",
                        chat_id, resp.status, body[:500],
                    )
                    return False
    except (aiohttp.ClientError, TimeoutError) as exc:
        logger.error("Telegram sendMessage request failed: %s", exc)
        return False
    return False


async def post_to_main_channel(text: str, *, skip_dedup: bool = False) -> bool:
    """Post a narrative update or news post to the public Main Channel."""
    if not skip_dedup and is_duplicate_content(text):
        logger.info("Skipping duplicate Main Channel post")
        return False

    sent = await _send_message(MAIN_CHANNEL_ID, text)
    if sent:
        remember_content(text)
        mark_main_channel_post()
    return sent


async def post_to_inner_circle(text: str, *, skip_dedup: bool = False) -> bool:
    """Post a full trading signal card to the Alpha AI signals group."""
    if not skip_dedup and is_duplicate_content(text):
        logger.info("Skipping duplicate Alpha AI post")
        return False

    sent = await _send_message(INNER_CIRCLE_ID, text)
    if sent:
        remember_content(text)
    return sent
