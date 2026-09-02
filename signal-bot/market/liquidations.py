"""
Builds a liquidity cluster table (price ranges + type + estimated dollar
size) by combining Binance futures open-interest/premium-index data with
OKX public liquidation order data. Free, no-auth endpoints only.

This is a best-effort estimate, not the paid Coinglass-grade heatmap --
it's meant to populate the "Liquidity Clusters" section of the signal card
with directionally useful levels.
"""

from __future__ import annotations

import aiohttp

from config import BINANCE_FUTURES_BASE, HTTP_TIMEOUT_SECONDS, OKX_BASE

_TIMEOUT = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)


def _binance_to_okx_inst_id(symbol: str) -> str:
    # e.g. "BTCUSDT" -> "BTC-USDT-SWAP"
    if symbol.endswith("USDT"):
        base = symbol[: -len("USDT")]
        return f"{base}-USDT-SWAP"
    return symbol


async def _get_json(session: aiohttp.ClientSession, url: str, params: dict | None = None):
    async with session.get(url, params=params) as resp:
        if resp.status != 200:
            return None
        return await resp.json()


async def get_binance_oi_and_mark_price(session: aiohttp.ClientSession, symbol: str) -> dict | None:
    oi_data = await _get_json(session, f"{BINANCE_FUTURES_BASE}/fapi/v1/openInterest", {"symbol": symbol})
    premium_data = await _get_json(session, f"{BINANCE_FUTURES_BASE}/fapi/v1/premiumIndex", {"symbol": symbol})
    if not oi_data or not premium_data:
        return None
    try:
        return {
            "open_interest": float(oi_data["openInterest"]),
            "mark_price": float(premium_data["markPrice"]),
        }
    except (KeyError, ValueError):
        return None


async def get_okx_recent_liquidations(session: aiohttp.ClientSession, symbol: str) -> list[dict]:
    inst_id = _binance_to_okx_inst_id(symbol)
    # OKX requires the underlying instrument family even when instId is
    # supplied. For USDT-margined swaps, the family is e.g. BTC-USDT.
    inst_family = inst_id.removesuffix("-SWAP")
    liquidation_url = f"{OKX_BASE}/api/v5/public/liquidation-orders"
    liquidation_params = {
        "instType": "SWAP",
        "instFamily": inst_family,
        "instId": inst_id,
        "state": "filled",
    }
    data = await _get_json(session, liquidation_url, liquidation_params)
    if not data or data.get("code") != "0":
        return []

    # OKX's `sz` is a number of contracts, so retrieve the instrument's
    # contract value before consumers convert the order into USD. Without it,
    # BTC and ETH liquidations would be overstated by 100x and 10x.
    instruments_url = f"{OKX_BASE}/api/v5/public/instruments"
    instrument_data = await _get_json(
        session,
        instruments_url,
        {"instType": "SWAP", "instId": inst_id},
    )
    try:
        contract_value = float((instrument_data.get("data") or [])[0]["ctVal"])
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return []
    if contract_value <= 0:
        return []

    details = data.get("data", [])
    orders: list[dict] = []
    for entry in details:
        for detail in entry.get("details", []):
            normalized = dict(detail)
            normalized["contract_value"] = contract_value
            orders.append(normalized)
    return orders


def _bucket_price(price: float, bucket_pct: float = 0.005) -> tuple[float, float]:
    """Round a price into a bucket range of +/- bucket_pct around it."""
    lower = price * (1 - bucket_pct)
    upper = price * (1 + bucket_pct)
    return round(lower, 6), round(upper, 6)


async def get_liquidity_clusters(session: aiohttp.ClientSession, symbol: str) -> list[dict]:
    """
    Returns a list of {"range": (low, high), "type": "Short Liq"|"Long Liq",
    "usd_estimate": float} entries, sorted by usd_estimate descending.
    """
    clusters: dict[tuple[float, float, str], float] = {}

    binance_data = await get_binance_oi_and_mark_price(session, symbol)
    if binance_data:
        mark_price = binance_data["mark_price"]
        open_interest = binance_data["open_interest"]
        notional = mark_price * open_interest
        # Rough heuristic: OI clustered near current price on both sides.
        short_range = _bucket_price(mark_price * 1.02)
        long_range = _bucket_price(mark_price * 0.98)
        clusters[(*short_range, "Short Liq")] = clusters.get((*short_range, "Short Liq"), 0) + notional * 0.15
        clusters[(*long_range, "Long Liq")] = clusters.get((*long_range, "Long Liq"), 0) + notional * 0.15

    okx_orders = await get_okx_recent_liquidations(session, symbol)
    for order in okx_orders:
        try:
            price = float(order.get("bkPx") or order.get("px") or 0)
            contracts = float(order.get("sz") or 0)
            contract_value = float(order.get("contract_value") or 0)
            side = order.get("side", "")
        except (TypeError, ValueError):
            continue
        if price <= 0 or contracts <= 0 or contract_value <= 0:
            continue
        usd_value = price * contracts * contract_value
        liq_type = "Long Liq" if side == "sell" else "Short Liq"
        price_range = _bucket_price(price)
        key = (*price_range, liq_type)
        clusters[key] = clusters.get(key, 0) + usd_value

    result = [
        {"range": (low, high), "type": liq_type, "usd_estimate": usd}
        for (low, high, liq_type), usd in clusters.items()
    ]
    result.sort(key=lambda c: c["usd_estimate"], reverse=True)
    return result[:6]
