"""
Polls CoinTelegraph, CoinDesk, and TheBlock RSS feeds every 3 minutes and
returns new items since the last check (deduplicated by entry link/guid).

feedparser itself is synchronous, so the actual HTTP fetch is done via
aiohttp and the raw XML bytes are handed to feedparser for parsing --
this keeps the network call non-blocking.
"""

from __future__ import annotations

import logging
import json
import re
from dataclasses import dataclass
from pathlib import Path

import aiohttp
import feedparser

from config import AI_INCIDENT_RSS_FEEDS, HTTP_TIMEOUT_SECONDS, RSS_FEEDS

logger = logging.getLogger("news.rss")


@dataclass
class NewsItem:
    source: str
    item_id: str
    title: str
    url: str
    published_at: str | None = None


_SEEN_STATE_PATH = Path(__file__).resolve().parents[1] / "state" / "news_seen.txt"
_seen_links: set[str] = set()
_seen_titles: list[str] = []
_UPDATE_MARKERS = {
    "update", "updated", "breaking", "approved", "approval", "decision",
    "ruling", "settlement", "exploited", "exploit", "hack", "hacked",
    "launches", "launched", "acquires", "acquired", "files", "filed",
}


def _load_seen_state() -> None:
    try:
        raw = _SEEN_STATE_PATH.read_text(encoding="utf-8").strip()
        if not raw:
            return
        saved = json.loads(raw)
        _seen_links.update(saved.get("links", []))
        _seen_titles.extend(saved.get("titles", []))
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, AttributeError, TypeError):
        logger.warning("Ignoring invalid persisted RSS dedup state")


def _save_seen_state() -> None:
    _SEEN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    saved = {
        "links": list(_seen_links)[-4000:],
        "titles": _seen_titles[-4000:],
    }
    _SEEN_STATE_PATH.write_text(json.dumps(saved), encoding="utf-8")


_load_seen_state()


def _title_words(title: str) -> set[str]:
    return {
        word for word in re.findall(r"[a-z0-9]+", title.lower())
        if len(word) > 2
    }


def _is_duplicate_story(title: str) -> bool:
    words = _title_words(title)
    if not words:
        return False
    has_update_marker = bool(words & _UPDATE_MARKERS)
    for previous in _seen_titles:
        previous_words = _title_words(previous)
        union = words | previous_words
        overlap = len(words & previous_words) / len(union) if union else 0
        if overlap >= 0.72 and not has_update_marker:
            return True
    return False


async def _fetch_feed_bytes(session: aiohttp.ClientSession, url: str) -> bytes | None:
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.warning("RSS feed %s returned HTTP %s", url, resp.status)
                return None
            return await resp.read()
    except (aiohttp.ClientError, TimeoutError) as exc:
        logger.warning("RSS feed %s fetch failed: %s", url, exc)
        return None


async def poll_rss_feeds() -> list[NewsItem]:
    """Returns new RSS entries across all configured feeds since the last poll."""
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
    new_items: list[NewsItem] = []

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for feed_url in (*RSS_FEEDS, *AI_INCIDENT_RSS_FEEDS):
            raw = await _fetch_feed_bytes(session, feed_url)
            if raw is None:
                continue

            parsed = feedparser.parse(raw)
            for entry in parsed.entries:
                link = entry.get("link") or entry.get("id") or ""
                title = (entry.get("title") or "").strip()
                if not link or link in _seen_links or _is_duplicate_story(title):
                    continue
                _seen_links.add(link)
                _seen_titles.append(title)
                new_items.append(
                    NewsItem(
                        source=feed_url,
                        item_id=link,
                        title=title,
                        url=link,
                        published_at=entry.get("published"),
                    )
                )

    if len(_seen_links) > 5000:
        for link in list(_seen_links)[:1000]:
            _seen_links.remove(link)
    if len(_seen_titles) > 5000:
        del _seen_titles[:1000]
    if new_items:
        _save_seen_state()

    return new_items
