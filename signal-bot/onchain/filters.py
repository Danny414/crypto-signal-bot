"""
Conservative filters for actionable low-cap whale movements.

The direct ERC-20 scanner sees protocol accounting, market-maker inventory
rebalancing, validator activity, and real whale transfers alike. This module
keeps the first three out before they reach the AI gate or Telegram.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque

from config import MAJOR_ASSET_ROOTS, OPERATIONAL_WHALE_SYMBOLS, STABLECOINS

_SYMBOL_CLEANER = re.compile(r"[^A-Z0-9]")
_STABLE_SUFFIXES = (".E", "E", "0", "B", "BRIDGED", "C")


def _clean_symbol(symbol: str) -> str:
    return _SYMBOL_CLEANER.sub("", symbol.upper())


def is_blocked_asset(symbol: str) -> tuple[bool, str]:
    """
    Return whether a token is excluded from whale updates.

    Stablecoin wrappers commonly appear as USDC.e, USDC.e, USDT0, or
    bridged variants. Major-asset derivatives are matched by common wrapper
    and staking prefixes/suffixes without blocking unrelated symbols such as
    ETHOS.
    """
    raw = symbol.upper().strip()
    clean = _clean_symbol(raw)

    if raw in OPERATIONAL_WHALE_SYMBOLS or clean in OPERATIONAL_WHALE_SYMBOLS:
        return True, "known operational token"
    if raw in STABLECOINS or clean in STABLECOINS:
        return True, "stablecoin"

    for stable in STABLECOINS:
        if clean.startswith(stable) and (
            len(clean) == len(stable)
            or clean[len(stable):] in _STABLE_SUFFIXES
            or clean[len(stable):].startswith(("BRIDGED", "PEGGED"))
        ):
            return True, "stablecoin variant"

    major_variants = {
        "WBTC", "BTCB", "CBTC", "CBBTC", "TBTC", "SBTC", "HBTC",
        "OBTC", "IBTC", "BBTC", "WBTC", "WETH", "STETH", "WSTETH",
        "CBETH", "RETH", "ANKRETH", "FRXETH", "SFRXETH", "OETH",
        "WEETH", "EETH", "METH", "SWETH", "ETHX", "BETH",
    }
    if clean in major_variants:
        return True, "BTC/ETH derivative"
    if clean.endswith("BTC") or clean.endswith("ETH"):
        return True, "BTC/ETH derivative"

    return False, ""


class OperationalWalletTracker:
    """
    Detects recurring token/address patterns typical of inventory routers,
    market makers, and validator/treasury operations.

    This is deliberately conservative: a wallet is suppressed only after
    repeated behavior within a short window, not on a single transfer.
    """

    def __init__(self, window_seconds: int = 6 * 60 * 60) -> None:
        self._window_seconds = window_seconds
        self._events: dict[str, deque[tuple[float, str, str]]] = defaultdict(deque)

    def _prune(self, symbol: str, now: float) -> deque[tuple[float, str, str]]:
        events = self._events[symbol]
        cutoff = now - self._window_seconds
        while events and events[0][0] < cutoff:
            events.popleft()
        return events

    def is_recurring_operational_pattern(self, symbol: str, from_address: str, to_address: str) -> bool:
        now = time.time()
        events = self._prune(symbol.upper(), now)
        from_address = from_address.lower()
        to_address = to_address.lower()
        pair_count = sum(1 for _, source, target in events if source == from_address and target == to_address)
        address_count = sum(
            1 for _, source, target in events
            if source in (from_address, to_address) or target in (from_address, to_address)
        )
        return pair_count >= 2 or address_count >= 8

    def record(self, symbol: str, from_address: str, to_address: str) -> None:
        now = time.time()
        events = self._prune(symbol.upper(), now)
        events.append((now, from_address.lower(), to_address.lower()))