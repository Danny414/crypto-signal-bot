"""
Gatekeeper: before spending money on deepseek-reasoner, ask deepseek-chat a
cheap yes/no-style significance question first. Only proceed to full signal
generation when the score is >= SIGNAL_DECISION_SCORE_THRESHOLD.
"""

from __future__ import annotations

import json
import logging

from config import SIGNAL_DECISION_SCORE_THRESHOLD
from ai.engine import chat_completion_json

logger = logging.getLogger("ai.decision")

_SYSTEM_PROMPT = """You are a crypto trading analyst screening onchain events and TA setups \
for a signal alert service. Rate the event from 1 to 10 on whether it is worth alerting \
subscribers about. Use this rubric strictly:

1-2 — Ignore: stablecoin mints/burns, protocol-internal moves (Aave deposits, null-address \
mints), routine wrapped-token operations, amounts under $50k.
3-4 — Weak: large transfers ($100k-$500k) between two completely unknown wallets with no \
exchange or protocol context; likely internal but uncertain.
5-6 — Notable: whale move $500k-$5M between unknown addresses OR any move involving a known \
exchange, DeFi protocol, or labelled wallet regardless of size; worth a brief alert.
7-8 — Strong: whale move $5M+ to/from an exchange OR cluster of coordinated transfers OR \
known entity (fund, exchange, project) moving significant capital; clear signal value.
9-10 — Exceptional: $20M+ exchange inflow/outflow, massive cluster, or move by a \
publicly known entity that is almost certainly market-moving.

Key factors that raise score: high USD value, exchange involvement, known entity, cluster \
pattern, unusual timing or velocity.
Key factors that lower score: transfers from/to null address (mints), stablecoins as the \
token, purely internal DeFi protocol accounting, very small USD value.

Respond ONLY with JSON in this exact shape, no other text:
{"score": 7, "reason": "one sentence explanation"}"""


async def is_significant(description: str) -> tuple[bool, int, str]:
    """
    Returns (should_proceed, score, reason). should_proceed is True only when
    score >= SIGNAL_DECISION_SCORE_THRESHOLD.
    """
    raw = await chat_completion_json(system_prompt=_SYSTEM_PROMPT, user_prompt=description)
    if raw is None:
        return False, 0, "DeepSeek call failed"

    try:
        parsed = json.loads(raw)
        score = int(parsed.get("score", 0))
        reason = str(parsed.get("reason", ""))
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("decision gate: failed to parse response: %s", raw[:300])
        return False, 0, "Failed to parse DeepSeek response"

    return score >= SIGNAL_DECISION_SCORE_THRESHOLD, score, reason
