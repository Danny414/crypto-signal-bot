"""
Entry point. Runs every loop concurrently via asyncio.gather:
  - ETH ChainScanner
  - BSC ChainScanner
  - Market (TA) scanner
  - News crawler (RSS feeds)
  - Market pulse idle timer
  - Source bot reader (existing ETH/BSC/BASE onchain scanner bot alert ingestion)
"""

from __future__ import annotations

import asyncio
import logging
import re

import aiohttp

import config
from market.binance import get_24h_stats
from market.scanner import scan_once
from news.filter import filter_headlines
from news.rss import poll_rss_feeds
from onchain.classifier import ClusterTracker, classify_transfer
from onchain.moralis import enrich_transfer
from onchain.scanner import ChainScanner, RawTransfer
from onchain.filters import OperationalWalletTracker, is_blocked_asset
from pipeline import generate_and_post_signal
from state.cooldowns import (
    can_post_news_now,
    mark_news_posted,
    seconds_since_last_main_channel_post,
)
from telegram.reader import start_reader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

_cluster_tracker = ClusterTracker()
_operational_wallet_tracker = OperationalWalletTracker()


def _raw_amount_to_float(raw_value: int, decimals: int) -> float:
    return raw_value / (10**decimals)


async def _handle_raw_transfer(transfer: RawTransfer) -> None:
    """Step 1-3 of the onchain flow: enrich, filter, classify, cluster-check."""
    token_info = await enrich_transfer(transfer.contract_address, transfer.chain.lower())
    if token_info is None:
        return

    blocked, block_reason = is_blocked_asset(token_info.symbol)
    if blocked:
        logger.info("Skipping %s transfer: %s", token_info.symbol, block_reason)
        return

    token_amount = _raw_amount_to_float(transfer.raw_value, token_info.decimals)
    usd_value = token_amount * token_info.usd_price if token_info.usd_price else 0.0

    if usd_value < config.MIN_TRANSFER_USD:
        return
    if token_info.market_cap_usd is None:
        logger.info("Skipping %s: market cap unavailable; low-cap rule requires verified cap", token_info.symbol)
        return
    if token_info.market_cap_usd > config.MAX_WHALE_MARKET_CAP_USD:
        logger.info(
            "Skipping %s: market cap $%.0f exceeds $%.0f cap",
            token_info.symbol,
            token_info.market_cap_usd,
            config.MAX_WHALE_MARKET_CAP_USD,
        )
        return
    if _operational_wallet_tracker.is_recurring_operational_pattern(
        token_info.symbol, transfer.from_address, transfer.to_address
    ):
        logger.info(
            "Skipping %s: recurring operational wallet pattern %s -> %s",
            token_info.symbol,
            transfer.from_address[:10],
            transfer.to_address[:10],
        )
        return
    _operational_wallet_tracker.record(token_info.symbol, transfer.from_address, transfer.to_address)

    classified = classify_transfer(
        chain=transfer.chain,
        symbol=token_info.symbol,
        contract_address=transfer.contract_address,
        from_address=transfer.from_address,
        to_address=transfer.to_address,
        usd_value=usd_value,
        token_amount=token_amount,
        tx_hash=transfer.tx_hash,
    )

    logger.info(
        "%s transfer: %s %s ($%.0f) %s -> %s [%s]",
        transfer.chain,
        token_amount,
        token_info.symbol,
        usd_value,
        classified.from_address[:10],
        classified.to_address[:10],
        classified.flow_type.value,
    )

    cluster_event = _cluster_tracker.add(classified)

    if cluster_event is not None:
        description = (
            f"Onchain CLUSTER detected on {cluster_event.chain}: "
            f"${cluster_event.total_usd:,.0f} total {cluster_event.flow_type.value} across "
            f"{cluster_event.transfer_count} transfers in the last {cluster_event.window_seconds // 60} minutes "
            f"for token {cluster_event.symbol}."
        )
        await generate_and_post_signal(cluster_event.symbol, cluster_event.chain, description)
        return

    # Individual whale moves and single accumulation/distribution events above
    # threshold are still candidates -- just smaller-magnitude than a cluster.
    if classified.flow_type.value in ("WHALE_MOVE", "ACCUMULATION", "DISTRIBUTION"):
        description = (
            f"Onchain {classified.flow_type.value} on {classified.chain}: "
            f"${classified.usd_value:,.0f} ({classified.token_amount:,.4f} {classified.symbol}) "
            f"from {classified.from_address} to {classified.to_address}. "
            f"Verified market cap: ${token_info.market_cap_usd:,.0f}."
        )
        await generate_and_post_signal(classified.symbol, classified.chain, description)


async def _run_market_scanner_loop() -> None:
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                candidates = await scan_once(session)
            for candidate in candidates:
                description = (
                    f"Pure technical setup on {candidate.symbol} (no onchain trigger). Score {candidate.score}/10. "
                    f"Reasons: {'; '.join(candidate.reasons)}."
                )
                token = candidate.symbol.replace("USDT", "")
                await generate_and_post_signal(token, None, description, is_ta_only=True)
        except Exception:  # noqa: BLE001
            logger.exception("Market scanner loop error")

        await asyncio.sleep(config.MARKET_SCANNER_INTERVAL_SECONDS)


async def _run_news_crawler_loop() -> None:
    """
    Polls RSS feeds every 3 minutes, respects the 30-minute Main Channel
    throttle, and combines simultaneous high-scoring items into one post.
    """
    last_rss_poll = 0.0
    loop = asyncio.get_event_loop()

    while True:
        try:
            now = loop.time()
            pending_items = []

            if now - last_rss_poll >= config.RSS_POLL_INTERVAL_SECONDS:
                pending_items.extend(await poll_rss_feeds())
                last_rss_poll = now

            if pending_items:
                scored = await filter_headlines(pending_items)
                if scored and can_post_news_now():
                    from ai.engine import chat_completion_text
                    from ai.news_prompt import NEWS_UPDATE_SYSTEM_PROMPT

                    combined_input = "\n".join(
                        f"- [{s.category}] {s.item.title} (score {s.score}, {s.sentiment}): {s.summary}"
                        for s in scored
                    )
                    post_text = await chat_completion_text(NEWS_UPDATE_SYSTEM_PROMPT, combined_input)
                    if post_text:
                        from telegram.poster import post_to_main_channel

                        posted = await post_to_main_channel(post_text)
                        if posted:
                            mark_news_posted()
                elif scored:
                    logger.info("News throttled -- %s high-score items queued but skipped this cycle", len(scored))
        except Exception:  # noqa: BLE001
            logger.exception("News crawler loop error")

        await asyncio.sleep(config.MARKET_PULSE_CHECK_INTERVAL_SECONDS)


async def _run_market_pulse_loop() -> None:
    """If nothing has been posted to Main Channel for 90 minutes, post a brief pulse."""
    from ai.engine import chat_completion_text
    from telegram.poster import post_to_main_channel

    pulse_system_prompt = (
        "You are a crypto market commentator. Write a brief 2 to 3 sentence market pulse "
        "covering BTC, ETH, SOL, and BNB price action and overall sentiment based on the 24h "
        "stats given. Conversational tone, no jargon, no bullet points. End with a timestamp "
        "line: \"⏰ HH:MM UTC\". Output only the post text."
    )

    while True:
        try:
            if seconds_since_last_main_channel_post() >= config.MARKET_PULSE_IDLE_SECONDS:
                async with aiohttp.ClientSession() as session:
                    stats = {}
                    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"):
                        data = await get_24h_stats(session, symbol)
                        if data:
                            stats[symbol] = {
                                "price": data.get("lastPrice"),
                                "change_pct": data.get("priceChangePercent"),
                            }

                if stats:
                    stats_text = "\n".join(
                        f"{sym}: price {v['price']}, 24h change {v['change_pct']}%" for sym, v in stats.items()
                    )
                    pulse = await chat_completion_text(pulse_system_prompt, stats_text)
                    if pulse:
                        await post_to_main_channel(pulse, skip_dedup=True)
        except Exception:  # noqa: BLE001
            logger.exception("Market pulse loop error")

        await asyncio.sleep(config.MARKET_PULSE_CHECK_INTERVAL_SECONDS)


_SYMBOL_PATTERN = re.compile(r"\$([A-Z0-9]{2,10})\b")
_USD_PATTERN = re.compile(r"\$([\d,.]+)\s*([KMB])?", re.IGNORECASE)
_MARKET_CAP_PATTERN = re.compile(
    r"(?:market\s*cap|market\s*capitalization|mcap|mc)\D*\$([\d,.]+)\s*([KMB])?",
    re.IGNORECASE,
)


def _parse_alert_usd(text: str) -> float | None:
    match = _USD_PATTERN.search(text)
    if not match:
        return None
    try:
        amount = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(
        (match.group(2) or "").lower(), 1
    )
    return amount * multiplier


def _parse_alert_market_cap(text: str) -> float | None:
    match = _MARKET_CAP_PATTERN.search(text)
    if not match:
        return None
    try:
        amount = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(
        (match.group(2) or "").lower(), 1
    )
    return amount * multiplier


async def _handle_source_bot_message(source_label: str, text: str) -> None:
    """
    Ingests alerts from the user's existing ETH/BSC onchain scanner bots.
    Uses the AI decision gate to decide whether this is worth turning into
    a signal candidate; if so, runs the same generation pipeline.
    """
    logger.info("Source bot message from %s: %s", source_label, text[:200])

    match = _SYMBOL_PATTERN.search(text)
    token_symbol = match.group(1) if match else None
    chain = source_label  # source_label is already "ETH", "BSC", or "BASE"

    # Source-bot alerts are subject to the same hard rules as direct RPC
    # events. Do not let an older source bot configuration bypass them.
    if not token_symbol:
        logger.info("Skipping source alert without a token symbol")
        return
    blocked, block_reason = is_blocked_asset(token_symbol)
    if blocked:
        logger.info("Skipping source alert for %s: %s", token_symbol, block_reason)
        return
    alert_usd = _parse_alert_usd(text)
    if alert_usd is None or alert_usd < config.MIN_TRANSFER_USD:
        logger.info("Skipping source alert for %s: value is below $%s or unavailable", token_symbol, config.MIN_TRANSFER_USD)
        return
    alert_market_cap = _parse_alert_market_cap(text)
    if alert_market_cap is None or alert_market_cap > config.MAX_WHALE_MARKET_CAP_USD:
        logger.info(
            "Skipping source alert for %s: market cap is unavailable or exceeds $%.0f",
            token_symbol,
            config.MAX_WHALE_MARKET_CAP_USD,
        )
        return

    description = f"Alert from existing {source_label} onchain scanner bot: {text}"
    await generate_and_post_signal(token_symbol, chain, description)


async def main() -> None:
    logger.info("Starting signal bot -- monitoring ETH, BSC, BASE, markets, and news.")

    eth_scanner = ChainScanner([config.ETH_RPC_URL, *config.ETH_RPC_FALLBACKS], "ETH")
    bsc_scanner = ChainScanner([config.BSC_RPC_URL, *config.BSC_RPC_FALLBACKS], "BSC")
    base_scanner = ChainScanner([config.BASE_RPC_URL, *config.BASE_RPC_FALLBACKS], "BASE")

    await asyncio.gather(
        eth_scanner.run(_handle_raw_transfer),
        bsc_scanner.run(_handle_raw_transfer),
        base_scanner.run(_handle_raw_transfer),
        _run_market_scanner_loop(),
        _run_news_crawler_loop(),
        _run_market_pulse_loop(),
        start_reader(_handle_source_bot_message),
    )


if __name__ == "__main__":
    asyncio.run(main())
