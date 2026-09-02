"""
In-memory state tracking:
  - Per-symbol signal cooldown (3 hours) to prevent re-posting the same
    token too frequently.
  - Content hash dedup set to prevent posting the exact same content twice.
  - Last Main Channel post timestamp, for the market-pulse idle timer.
  - Daily counter for TA-only signals (max 4/day, no onchain support).
  - Last Main Channel news-post timestamp, for the 30-minute news throttle.

All state is process-local (in memory). If the bot restarts, cooldowns
reset -- acceptable for this use case since the underlying goal is just to
avoid spamming within a single continuous run.
"""

from __future__ import annotations

import hashlib
import time

from config import (
    MAX_TA_ONLY_SIGNALS_PER_DAY,
    NEWS_POST_MIN_INTERVAL_SECONDS,
    SIGNAL_COOLDOWN_SECONDS,
)

_last_signal_time: dict[str, float] = {}
_content_hashes: set[str] = set()
_last_main_channel_post_time: float = 0.0
_last_news_post_time: float = 0.0
_ta_only_signal_dates: dict[str, int] = {}  # "YYYY-MM-DD" -> count


def hash_content(text: str) -> str:
    return hashlib.md5(text.strip().encode("utf-8")).hexdigest()


def is_duplicate_content(text: str) -> bool:
    return hash_content(text) in _content_hashes


def remember_content(text: str) -> None:
    _content_hashes.add(hash_content(text))
    if len(_content_hashes) > 5000:
        _content_hashes.clear()


def is_symbol_on_cooldown(symbol: str) -> bool:
    last_time = _last_signal_time.get(symbol)
    if last_time is None:
        return False
    return (time.time() - last_time) < SIGNAL_COOLDOWN_SECONDS


def mark_signal_posted(symbol: str) -> None:
    _last_signal_time[symbol] = time.time()


def mark_main_channel_post() -> None:
    global _last_main_channel_post_time
    _last_main_channel_post_time = time.time()


def seconds_since_last_main_channel_post() -> float:
    if _last_main_channel_post_time == 0:
        return float("inf")
    return time.time() - _last_main_channel_post_time


def can_post_news_now() -> bool:
    if _last_news_post_time == 0:
        return True
    return (time.time() - _last_news_post_time) >= NEWS_POST_MIN_INTERVAL_SECONDS


def mark_news_posted() -> None:
    global _last_news_post_time
    _last_news_post_time = time.time()
    mark_main_channel_post()


def _today_key() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def can_post_ta_only_signal() -> bool:
    today = _today_key()
    # Clear stale days so the dict doesn't grow forever.
    for key in list(_ta_only_signal_dates.keys()):
        if key != today:
            del _ta_only_signal_dates[key]
    return _ta_only_signal_dates.get(today, 0) < MAX_TA_ONLY_SIGNALS_PER_DAY


def mark_ta_only_signal_posted() -> None:
    today = _today_key()
    _ta_only_signal_dates[today] = _ta_only_signal_dates.get(today, 0) + 1
