"""
Shared orchestration glue that ties together onchain events, TA candidates,
the AI decision gate, supplementary market data, signal generation, and
posting to the two Telegram destinations.

This is the "step 4 onward" flow described for both onchain-triggered and
pure-TA-triggered signals: decision gate -> gather market data -> generate
signal card via deepseek-reasoner -> post to Alpha AI -> generate and
post a narrative version to Main Channel.
"""

from __future__ import annotations

import logging

import aiohttp

from ai.decision import is_significant
from ai.engine import chat_completion_text, reasoning_completion
from ai.news_prompt import NEWS_NARRATIVE_SYSTEM_PROMPT
from ai.signal_prompt import SIGNAL_SYSTEM_PROMPT
from market.binance import (
    calculate_rsi,
    calculate_vwap,
    closes_from_klines,
    get_funding_rate,
    get_klines,
    get_open_interest,
    volumes_from_klines,
)
from state.cooldowns import (
    can_post_ta_only_signal,
    is_symbol_on_cooldown,
    mark_signal_posted,
    mark_ta_only_signal_posted,
)
from telegram.poster import post_to_inner_circle, post_to_main_channel

logger = logging.getLogger("pipeline")


def to_binance_symbol(token_symbol: str) -> str:
    token_symbol = token_symbol.upper().strip()
    if token_symbol.endswith("USDT"):
        return token_symbol
    return f"{token_symbol}USDT"


async def gather_market_data(session: aiohttp.ClientSession, token_symbol: str) -> dict:
    """
    Best-effort supplementary TA data for the signal card. Any field that
    can't be fetched (e.g. token isn't listed on Binance) comes back as
    None -- the signal prompt is instructed to handle missing data
    gracefully rather than inventing numbers.
    """
    binance_symbol = to_binance_symbol(token_symbol)
    data: dict = {"binance_symbol": binance_symbol}

    klines_4h = await get_klines(session, binance_symbol, "4h", limit=60)
    klines_1h = await get_klines(session, binance_symbol, "1h", limit=60)
    klines_daily = await get_klines(session, binance_symbol, "1d", limit=8)

    data["rsi_4h"] = calculate_rsi(closes_from_klines(klines_4h), 14) if klines_4h else None
    data["rsi_1h"] = calculate_rsi(closes_from_klines(klines_1h), 14) if klines_1h else None
    data["vwap"] = calculate_vwap(klines_1h) if klines_1h else None
    data["current_price"] = closes_from_klines(klines_1h)[-1] if klines_1h else None

    if klines_daily and len(klines_daily) > 1:
        volumes = volumes_from_klines(klines_daily)
        avg_7d = sum(volumes[:-1]) / max(len(volumes) - 1, 1)
        data["volume_multiple"] = (volumes[-1] / avg_7d) if avg_7d else None
    else:
        data["volume_multiple"] = None

    data["funding_rate"] = await get_funding_rate(session, binance_symbol)
    data["open_interest"] = await get_open_interest(session, binance_symbol)
    # Liquidation data is intentionally disabled until a new presentation
    # format is approved.
    data["liquidity_clusters"] = []

    return data


def _format_market_data(data: dict) -> str:
    lines = [f"Binance symbol: {data['binance_symbol']}"]
    lines.append(f"Current price: {data['current_price']}" if data["current_price"] else "Current price: unavailable")
    lines.append(f"RSI 4H: {data['rsi_4h']:.1f}" if data["rsi_4h"] is not None else "RSI 4H: unavailable")
    lines.append(f"RSI 1H: {data['rsi_1h']:.1f}" if data["rsi_1h"] is not None else "RSI 1H: unavailable")
    lines.append(f"VWAP: {data['vwap']:.6f}" if data["vwap"] is not None else "VWAP: unavailable")
    lines.append(
        f"Volume vs 7d avg: {data['volume_multiple']:.2f}x" if data["volume_multiple"] else "Volume: unavailable"
    )
    lines.append(
        f"Funding rate: {data['funding_rate'] * 100:.4f}%" if data["funding_rate"] is not None else "Funding rate: unavailable"
    )
    lines.append(
        f"Open interest: {data['open_interest']}" if data["open_interest"] is not None else "Open interest: unavailable"
    )
    if data["liquidity_clusters"]:
        lines.append("Liquidity clusters:")
        for cluster in data["liquidity_clusters"]:
            low, high = cluster["range"]
            lines.append(f"  {low}-{high} — {cluster['type']} — ~${cluster['usd_estimate']:,.0f}")
    else:
        lines.append("Liquidity clusters: unavailable")
    return "\n".join(lines)


async def generate_and_post_signal(
    token_symbol: str,
    chain: str | None,
    onchain_description: str,
    *,
    is_ta_only: bool = False,
) -> None:
    """
    Full step-4-onward flow: decision gate -> gather market data ->
    generate signal card -> post to Alpha AI -> generate + post
    narrative to Main Channel.
    """
    token_symbol = token_symbol.upper().strip()

    if is_symbol_on_cooldown(token_symbol):
        logger.info("Skipping %s -- symbol on cooldown", token_symbol)
        return

    if is_ta_only and not can_post_ta_only_signal():
        logger.info("Skipping TA-only signal for %s -- daily TA signal cap reached", token_symbol)
        return

    should_proceed, score, reason = await is_significant(onchain_description)
    if not should_proceed:
        logger.info("Decision gate dropped %s (score=%s): %s", token_symbol, score, reason)
        return

    async with aiohttp.ClientSession() as session:
        market_data = await gather_market_data(session, token_symbol)

    market_data_text = _format_market_data(market_data)

    signal_user_prompt = (
        f"Token: {token_symbol}\n"
        f"Chain: {chain or 'N/A'}\n\n"
        f"Event / setup description:\n{onchain_description}\n\n"
        f"Market data:\n{market_data_text}"
    )

    signal_card = await reasoning_completion(SIGNAL_SYSTEM_PROMPT, signal_user_prompt)
    if not signal_card:
        logger.warning("Signal generation failed for %s -- no card produced", token_symbol)
        return

    posted = await post_to_inner_circle(signal_card)
    if not posted:
        logger.warning("Failed to post signal for %s to Alpha AI", token_symbol)
        return

    mark_signal_posted(token_symbol)
    if is_ta_only:
        mark_ta_only_signal_posted()

    narrative_user_prompt = (
        f"Token: {token_symbol}\n"
        f"Chain: {chain or 'N/A'}\n"
        f"Event description: {onchain_description}\n\n"
        "A full trading signal for this token was just posted to Alpha AI. "
        "Write the Main Channel narrative version."
    )
    narrative = await chat_completion_text(NEWS_NARRATIVE_SYSTEM_PROMPT, narrative_user_prompt)
    if narrative:
        await post_to_main_channel(narrative)
    else:
        logger.warning("Narrative generation failed for %s", token_symbol)
