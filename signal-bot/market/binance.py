"""
Binance public REST API helpers: klines, RSI, VWAP, 24h stats, funding
rate, and open interest. No API key required. RSI and VWAP are computed
with pure Python math -- no TA library dependency.
"""

from __future__ import annotations

import aiohttp

from config import BINANCE_FUTURES_BASE, BINANCE_SPOT_BASE, HTTP_TIMEOUT_SECONDS

_TIMEOUT = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)


async def _get_json(session: aiohttp.ClientSession, url: str, params: dict | None = None):
    async with session.get(url, params=params) as resp:
        if resp.status != 200:
            return None
        return await resp.json()


async def get_klines(
    session: aiohttp.ClientSession, symbol: str, interval: str, limit: int = 100
) -> list[list] | None:
    """
    Fetch klines from Binance spot API. Each kline is:
    [open_time, open, high, low, close, volume, close_time, ...]
    """
    url = f"{BINANCE_SPOT_BASE}/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    return await _get_json(session, url, params)


async def get_24h_stats(session: aiohttp.ClientSession, symbol: str) -> dict | None:
    url = f"{BINANCE_SPOT_BASE}/api/v3/ticker/24hr"
    return await _get_json(session, url, {"symbol": symbol})


async def get_funding_rate(session: aiohttp.ClientSession, symbol: str) -> float | None:
    """Returns the most recent funding rate as a decimal (e.g. -0.0005 = -0.05%)."""
    url = f"{BINANCE_FUTURES_BASE}/fapi/v1/fundingRate"
    data = await _get_json(session, url, {"symbol": symbol, "limit": 1})
    if not data:
        return None
    try:
        return float(data[-1]["fundingRate"])
    except (KeyError, ValueError, IndexError):
        return None


async def get_open_interest(session: aiohttp.ClientSession, symbol: str) -> float | None:
    url = f"{BINANCE_FUTURES_BASE}/fapi/v1/openInterest"
    data = await _get_json(session, url, {"symbol": symbol})
    if not data:
        return None
    try:
        return float(data["openInterest"])
    except (KeyError, ValueError):
        return None


async def get_top_perp_symbols_by_volume(session: aiohttp.ClientSession, limit: int = 50) -> list[str]:
    """Return the top USDT-margined perpetual futures symbols by 24h quote volume."""
    url = f"{BINANCE_FUTURES_BASE}/fapi/v1/ticker/24hr"
    data = await _get_json(session, url)
    if not data:
        return []
    usdt_pairs = [d for d in data if str(d.get("symbol", "")).endswith("USDT")]
    usdt_pairs.sort(key=lambda d: float(d.get("quoteVolume", 0) or 0), reverse=True)
    return [d["symbol"] for d in usdt_pairs[:limit]]


def calculate_rsi(closes: list[float], period: int = 14) -> float | None:
    """Standard Wilder's RSI from a list of closing prices (oldest first)."""
    if len(closes) < period + 1:
        return None

    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_vwap(klines: list[list]) -> float | None:
    """
    VWAP from raw Binance kline rows: index 2=high, 3=low, 4=close, 5=volume.
    Uses typical price (H+L+C)/3 weighted by volume.
    """
    if not klines:
        return None

    total_pv = 0.0
    total_volume = 0.0
    for k in klines:
        try:
            high, low, close, volume = float(k[2]), float(k[3]), float(k[4]), float(k[5])
        except (IndexError, ValueError):
            continue
        typical_price = (high + low + close) / 3
        total_pv += typical_price * volume
        total_volume += volume

    if total_volume == 0:
        return None
    return total_pv / total_volume


def closes_from_klines(klines: list[list]) -> list[float]:
    return [float(k[4]) for k in klines]


def volumes_from_klines(klines: list[list]) -> list[float]:
    return [float(k[5]) for k in klines]
