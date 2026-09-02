"""
Batches headlines from RSS and sends them to deepseek-chat
for market-impact scoring. Only items scoring >= NEWS_IMPACT_SCORE_THRESHOLD
are considered actionable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from config import NEWS_IMPACT_SCORE_THRESHOLD, NEWS_SPECIAL_SCORE_THRESHOLD
from ai.engine import chat_completion_json
from news.rss import NewsItem

logger = logging.getLogger("news.filter")

_SYSTEM_PROMPT = """You are a crypto news editor screening real published headlines. You will \
be given a numbered list of headlines. For EACH headline, rate its relevance from 1 to 10, give \
a one-sentence factual summary, a sentiment ("bullish", "bearish", or "neutral"), and exactly \
one category:
- "ai_incident": a real-world report of an AI agent/model hacking, exploiting, bypassing \
security, taking unauthorized actions, accessing data, or causing an actual incident. Do not \
use this for hypothetical research, product launches, demos, or ordinary AI business news.
- "liquidation": a real report of traders/positions being liquidated, forced closures, major \
trading losses, or a liquidation cascade.
- "market", "regulation", "institutional", "risk", or "other" for everything else.

Score 7-10 for major market-moving stories. Score 5-10 for real AI incidents or liquidation \
stories that are genuinely notable even if their direct price impact is smaller. Never invent \
facts not present in the headline.

Respond ONLY with a JSON array, one object per headline, in the same order as given, in \
this exact shape:
[{"index": 0, "score": 8, "summary": "...", "sentiment": "bearish", "category": "liquidation"}, ...]

Do not include any text before or after the JSON array."""


@dataclass
class ScoredNewsItem:
    item: NewsItem
    score: int
    summary: str
    sentiment: str
    category: str


async def filter_headlines(items: list[NewsItem]) -> list[ScoredNewsItem]:
    """Score a batch of headlines via deepseek-chat; return only score >= threshold."""
    if not items:
        return []

    numbered = "\n".join(f"{i}. {item.title}" for i, item in enumerate(items))
    user_prompt = f"Headlines:\n{numbered}"

    raw = await chat_completion_json(system_prompt=_SYSTEM_PROMPT, user_prompt=user_prompt)
    if raw is None:
        logger.warning("news filter: DeepSeek returned no response, dropping batch")
        return []

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("news filter: failed to parse DeepSeek JSON response: %s", raw[:500])
        return []

    scored: list[ScoredNewsItem] = []
    for entry in parsed:
        try:
            index = int(entry["index"])
            score = int(entry["score"])
        except (KeyError, TypeError, ValueError):
            continue
        if index < 0 or index >= len(items):
            continue
        category = str(entry.get("category", "other")).lower()
        threshold = (
            NEWS_SPECIAL_SCORE_THRESHOLD
            if category in {"ai_incident", "liquidation"}
            else NEWS_IMPACT_SCORE_THRESHOLD
        )
        if score < threshold or category == "other":
            continue
        scored.append(
            ScoredNewsItem(
                item=items[index],
                score=score,
                summary=str(entry.get("summary", "")),
                sentiment=str(entry.get("sentiment", "neutral")),
                category=category,
            )
        )

    return scored
