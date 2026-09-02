"""
Moralis integration: enrich a raw ERC-20 transfer (contract address + chain)
    with token symbol, decimals, current USD price, and market cap.

Only used to enrich transfers already found via direct RPC log polling --
never used for block polling itself (that would burn the free compute-unit
budget far too fast).

Results are cached in memory per (chain, contract_address) so repeat
transfers of an already-seen token do not cost additional Moralis calls.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import aiohttp

from config import HTTP_TIMEOUT_SECONDS, MORALIS_API_KEY

MORALIS_BASE_URL = "https://deep-index.moralis.io/api/v2.2"

# Cache entries expire after this long so prices don't go permanently stale.
_CACHE_TTL_SECONDS = 10 * 60


@dataclass
class TokenInfo:
    symbol: str
    decimals: int
    usd_price: float | None
    name: str | None = None
    market_cap_usd: float | None = None


_cache: dict[tuple[str, str], tuple[float, TokenInfo]] = {}


def _cache_key(chain: str, contract_address: str) -> tuple[str, str]:
    return (chain.lower(), contract_address.lower())


def _get_cached(chain: str, contract_address: str) -> TokenInfo | None:
    key = _cache_key(chain, contract_address)
    entry = _cache.get(key)
    if entry is None:
        return None
    fetched_at, info = entry
    if time.time() - fetched_at > _CACHE_TTL_SECONDS:
        del _cache[key]
        return None
    return info


def _set_cached(chain: str, contract_address: str, info: TokenInfo) -> None:
    _cache[_cache_key(chain, contract_address)] = (time.time(), info)


async def enrich_transfer(contract_address: str, chain: str) -> TokenInfo | None:
    """
    Look up token symbol/decimals/USD price for `contract_address` on `chain`
    ("eth", "bsc", or "base"). Returns None if the lookup fails.
    """
    cached = _get_cached(chain, contract_address)
    if cached is not None:
        return cached

    headers = {"X-API-Key": MORALIS_API_KEY, "accept": "application/json"}
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Token metadata (symbol, decimals, name)
            meta_url = f"{MORALIS_BASE_URL}/erc20/metadata"
            meta_params = {"chain": chain, "addresses": contract_address}
            async with session.get(meta_url, headers=headers, params=meta_params) as resp:
                if resp.status != 200:
                    return None
                meta_list = await resp.json()
                if not meta_list:
                    return None
                meta = meta_list[0]

            symbol = meta.get("symbol") or "UNKNOWN"
            decimals = int(meta.get("decimals") or 18)
            name = meta.get("name")

            # Current USD price
            usd_price: float | None = None
            price_url = f"{MORALIS_BASE_URL}/erc20/{contract_address}/price"
            async with session.get(price_url, headers=headers, params={"chain": chain}) as resp:
                if resp.status == 200:
                    price_data = await resp.json()
                    usd_price = price_data.get("usdPrice")

            # CoinGecko is used only for the low-cap gate. A missing market cap
            # is intentionally retained as None and rejected by the caller;
            # posting an unknown-cap token would violate the low-cap rule.
            platform = {
                "eth": "ethereum",
                "bsc": "binance-smart-chain",
                "base": "base",
            }.get(chain.lower())
            market_cap_usd: float | None = None
            if platform:
                gecko_url = (
                    f"https://api.coingecko.com/api/v3/coins/{platform}/contract/"
                    f"{contract_address.lower()}"
                )
                async with session.get(
                    gecko_url,
                    params={
                        "localization": "false",
                        "tickers": "false",
                        "market_data": "true",
                        "community_data": "false",
                        "developer_data": "false",
                    },
                    headers={"accept": "application/json", "user-agent": "alpha-signal/1.0"},
                ) as resp:
                    if resp.status == 200:
                        gecko_data = await resp.json()
                        market_cap_usd = (gecko_data.get("market_data") or {}).get("market_cap", {}).get("usd")

            if market_cap_usd is None:
                # DexScreener covers many newer low-cap tokens that have no
                # CoinGecko listing. Use the matching chain's largest pair
                # market cap, falling back to FDV as a conservative proxy.
                dex_chain = {"eth": "ethereum", "bsc": "bsc", "base": "base"}.get(chain.lower())
                if dex_chain:
                    dex_url = f"https://api.dexscreener.com/latest/dex/tokens/{contract_address.lower()}"
                    async with session.get(
                        dex_url,
                        headers={"accept": "application/json", "user-agent": "alpha-signal/1.0"},
                    ) as resp:
                        if resp.status == 200:
                            dex_data = await resp.json()
                            caps = []
                            for pair in dex_data.get("pairs") or []:
                                if pair.get("chainId") != dex_chain:
                                    continue
                                cap = pair.get("marketCap") or pair.get("fdv")
                                if isinstance(cap, (int, float)) and cap > 0:
                                    caps.append(float(cap))
                            if caps:
                                market_cap_usd = max(caps)

            info = TokenInfo(
                symbol=symbol,
                decimals=decimals,
                usd_price=usd_price,
                name=name,
                market_cap_usd=market_cap_usd,
            )
            _set_cached(chain, contract_address, info)
            return info
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return None
