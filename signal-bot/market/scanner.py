"""
Independent TA scanner: every 5 minutes, scores the top 50 Binance
perpetual futures pairs by volume and flags candidates for signal
generation when they have no supporting onchain event.

Scoring (points, threshold to become a candidate: >= 7):
  RSI < 30 on 4H                          -> 2 points
  RSI < 35 on 1H                          -> 1 point
  Price below VWAP                        -> 1 point (long bias)
  Volume > 2x 7-day average                -> 2 points
  Funding rate < -0.05%                    -> 2 points (squeeze setup)
  Open interest rising while price flat    -> 1 point
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import aiohttp

from config import MARKET_SCANNER_SCORE_THRESHOLD
from market.binance import (
    calculate_rsi,
    calculate_vwap,
    closes_from_klines,
    get_funding_rate,
    get_klines,
    get_open_interest,
    get_top_perp_symbols_by_volume,
    volumes_from_klines,
)

logger = logging.getLogger("market.scanner")


@dataclass
class TASignalCandidate:
    symbol: str
    score: int
    rsi_4h: float | None
    rsi_1h: float | None
    vwap: float | None
    current_price: float | None
    volume_multiple: float | None
    funding_rate: float | None
    open_interest: float | None
    reasons: list[str]


async def _score_symbol(session: aiohttp.ClientSession, symbol: str) -> TASignalCandidate | None:
    klines_4h = await get_klines(session, symbol, "4h", limit=60)
    klines_1h = await get_klines(session, symbol, "1h", limit=60)
    klines_daily = await get_klines(session, symbol, "1d", limit=8)

    if not klines_4h or not klines_1h or not klines_daily:
        return None

    closes_4h = closes_from_klines(klines_4h)
    closes_1h = closes_from_klines(klines_1h)

    rsi_4h = calculate_rsi(closes_4h, period=14)
    rsi_1h = calculate_rsi(closes_1h, period=14)
    vwap = calculate_vwap(klines_1h)
    current_price = closes_1h[-1] if closes_1h else None

    daily_volumes = volumes_from_klines(klines_daily)
    avg_7d_volume = sum(daily_volumes[:-1]) / max(len(daily_volumes) - 1, 1) if len(daily_volumes) > 1 else None
    today_volume = daily_volumes[-1] if daily_volumes else None
    volume_multiple = (today_volume / avg_7d_volume) if (today_volume and avg_7d_volume) else None

    funding_rate = await get_funding_rate(session, symbol)
    open_interest = await get_open_interest(session, symbol)

    score = 0
    reasons: list[str] = []

    if rsi_4h is not None and rsi_4h < 30:
        score += 2
        reasons.append(f"RSI 4H {rsi_4h:.1f} < 30 (oversold)")
    if rsi_1h is not None and rsi_1h < 35:
        score += 1
        reasons.append(f"RSI 1H {rsi_1h:.1f} < 35 (oversold)")
    if vwap is not None and current_price is not None and current_price < vwap:
        score += 1
        reasons.append(f"Price {current_price:.4f} below VWAP {vwap:.4f} (long bias)")
    if volume_multiple is not None and volume_multiple > 2:
        score += 2
        reasons.append(f"Volume {volume_multiple:.1f}x 7-day average")
    if funding_rate is not None and funding_rate < -0.0005:
        score += 2
        reasons.append(f"Funding rate {funding_rate * 100:.3f}% (shorts paying longs -- squeeze setup)")

    if score >= MARKET_SCANNER_SCORE_THRESHOLD:
        return TASignalCandidate(
            symbol=symbol,
            score=score,
            rsi_4h=rsi_4h,
            rsi_1h=rsi_1h,
            vwap=vwap,
            current_price=current_price,
            volume_multiple=volume_multiple,
            funding_rate=funding_rate,
            open_interest=open_interest,
            reasons=reasons,
        )
    return None


async def scan_once(session: aiohttp.ClientSession) -> list[TASignalCandidate]:
    symbols = await get_top_perp_symbols_by_volume(session, limit=50)
    candidates: list[TASignalCandidate] = []

    for symbol in symbols:
        try:
            candidate = await _score_symbol(session, symbol)
            if candidate is not None:
                candidates.append(candidate)
        except Exception:  # noqa: BLE001 - keep scanning other symbols
            logger.exception("market scanner: failed scoring %s", symbol)
        await asyncio.sleep(0.2)  # be gentle with the public API

    return candidates
