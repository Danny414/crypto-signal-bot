"""
Reads alerts from the user's own existing ETH/BSC/BASE onchain scanner bots.

Those bots are already running elsewhere and continuously polling their own
getUpdates endpoint, so we can't also long-poll their tokens (Telegram only
allows one getUpdates/webhook consumer per bot token -- a second consumer
gets HTTP 409 Conflict forever). Instead:

  1. The user redirects all source bots to post their alerts into a shared
     Telegram group (SOURCE_GROUP_CHAT_ID) — the "Alpha AI" group.
  2. Our own posting bot (TELEGRAM_BOT_TOKEN / Alpha Signal) is added as
     admin of that group, so its own getUpdates feed receives every message
     posted there, including messages sent by other bots.
  3. We resolve each source bot's Telegram user ID once at startup (via
     getMe on its own token) and use that to tell ETH/BSC/BASE alerts apart
     within the shared group.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import aiohttp

from config import HTTP_TIMEOUT_SECONDS, SOURCE_BOTS, SOURCE_GROUP_CHAT_ID, TELEGRAM_BOT_TOKEN

logger = logging.getLogger("telegram.reader")

MessageHandler = Callable[[str, str], Awaitable[None]]  # (source_label, message_text) -> None

_LONG_POLL_TIMEOUT_SECONDS = 30


async def _get_bot_id(session: aiohttp.ClientSession, token: str) -> int:
    async with session.get(f"https://api.telegram.org/bot{token}/getMe") as resp:
        payload = await resp.json()
    if not payload.get("ok"):
        raise RuntimeError(f"getMe failed for a source bot token: {payload}")
    return int(payload["result"]["id"])


async def _resolve_source_bot_ids() -> dict[int, str]:
    """Maps each source bot's Telegram user ID to its label (e.g. 'ETH')."""
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        ids_by_label = {label: await _get_bot_id(session, token) for label, token in SOURCE_BOTS.items()}
    logger.info("Resolved source bot IDs: %s", ids_by_label)
    return {bot_id: label for label, bot_id in ids_by_label.items()}


def _sender_id(message: dict) -> int | None:
    sender = message.get("from") or message.get("sender_chat")
    if not sender:
        return None
    return sender.get("id")


async def start_reader(on_message: MessageHandler) -> None:
    """
    Long-polls our own bot's getUpdates for messages in the shared source
    group, routing each one to on_message(source_label, text) based on which
    source bot sent it.
    """
    label_by_id = await _resolve_source_bot_ids()
    logger.info(
        "Listening for alerts in shared group %s from source bots: %s",
        SOURCE_GROUP_CHAT_ID,
        ", ".join(SOURCE_BOTS.keys()),
    )

    api_base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    offset: int | None = None
    target_chat_id = str(SOURCE_GROUP_CHAT_ID)

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                params = {"timeout": _LONG_POLL_TIMEOUT_SECONDS, "allowed_updates": '["message","channel_post"]'}
                if offset is not None:
                    params["offset"] = offset

                timeout = aiohttp.ClientTimeout(total=_LONG_POLL_TIMEOUT_SECONDS + HTTP_TIMEOUT_SECONDS)
                async with session.get(f"{api_base}/getUpdates", params=params, timeout=timeout) as resp:
                    payload = await resp.json()

                if not payload.get("ok"):
                    logger.warning("getUpdates error: %s", payload)
                    await asyncio.sleep(5)
                    continue

                for update in payload.get("result", []):
                    offset = update["update_id"] + 1
                    message = update.get("channel_post") or update.get("message")
                    if not message:
                        continue
                    if str(message.get("chat", {}).get("id")) != target_chat_id:
                        continue

                    sender_id = _sender_id(message)
                    label = label_by_id.get(sender_id) if sender_id is not None else None
                    if label is None:
                        continue  # message in the group from someone other than our source bots

                    text = (message.get("text") or message.get("caption") or "").strip()
                    if not text:
                        continue
                    await on_message(label, text)

            except asyncio.TimeoutError:
                continue
            except Exception:  # noqa: BLE001 - keep polling even if one cycle errors
                logger.exception("Error polling shared source group")
                await asyncio.sleep(5)
