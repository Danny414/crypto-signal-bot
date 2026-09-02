"""
Classifies individual ERC-20 transfers and detects rolling clusters of
same-direction flow within a 5-minute window.

Transfer classification:
  ACCUMULATION -- source is a known exchange wallet, destination is a
                  regular wallet (funds leaving an exchange).
  DISTRIBUTION -- source is a regular wallet, destination is a known
                  exchange wallet (funds moving to an exchange).
  WHALE_MOVE   -- wallet-to-wallet transfer above the whale threshold.
  TRANSFER     -- anything else that clears the minimum USD threshold.

Cluster detection:
  Tracks a rolling 5-minute window per symbol+direction. When the total
  USD value of same-direction transfers exceeds CLUSTER_THRESHOLD_USD,
  emits a CLUSTER event that should be treated as an immediate signal
  candidate.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from config import CLUSTER_THRESHOLD_USD, CLUSTER_WINDOW_SECONDS, WHALE_THRESHOLD_USD
from onchain.cex_wallets import is_exchange_wallet


class FlowType(str, Enum):
    ACCUMULATION = "ACCUMULATION"
    DISTRIBUTION = "DISTRIBUTION"
    WHALE_MOVE = "WHALE_MOVE"
    TRANSFER = "TRANSFER"


@dataclass
class ClassifiedTransfer:
    chain: str
    symbol: str
    contract_address: str
    from_address: str
    to_address: str
    usd_value: float
    token_amount: float
    tx_hash: str
    flow_type: FlowType
    timestamp: float = field(default_factory=time.time)


@dataclass
class ClusterEvent:
    chain: str
    symbol: str
    flow_type: FlowType
    total_usd: float
    transfer_count: int
    window_seconds: int
    transfers: list[ClassifiedTransfer]


def classify_transfer(
    chain: str,
    symbol: str,
    contract_address: str,
    from_address: str,
    to_address: str,
    usd_value: float,
    token_amount: float,
    tx_hash: str,
) -> ClassifiedTransfer:
    from_is_exchange = is_exchange_wallet(from_address)
    to_is_exchange = is_exchange_wallet(to_address)

    if from_is_exchange and not to_is_exchange:
        flow_type = FlowType.ACCUMULATION
    elif to_is_exchange and not from_is_exchange:
        flow_type = FlowType.DISTRIBUTION
    elif not from_is_exchange and not to_is_exchange and usd_value >= WHALE_THRESHOLD_USD:
        flow_type = FlowType.WHALE_MOVE
    else:
        flow_type = FlowType.TRANSFER

    return ClassifiedTransfer(
        chain=chain,
        symbol=symbol,
        contract_address=contract_address,
        from_address=from_address,
        to_address=to_address,
        usd_value=usd_value,
        token_amount=token_amount,
        tx_hash=tx_hash,
        flow_type=flow_type,
    )


class ClusterTracker:
    """Maintains a rolling window of transfers per (symbol, flow_type)."""

    def __init__(self, window_seconds: int = CLUSTER_WINDOW_SECONDS) -> None:
        self._window_seconds = window_seconds
        self._buckets: dict[tuple[str, FlowType], list[ClassifiedTransfer]] = {}

    def _prune(self, key: tuple[str, FlowType]) -> None:
        cutoff = time.time() - self._window_seconds
        bucket = self._buckets.get(key, [])
        self._buckets[key] = [t for t in bucket if t.timestamp >= cutoff]

    def add(self, transfer: ClassifiedTransfer) -> ClusterEvent | None:
        """
        Add a transfer to the rolling window. Returns a ClusterEvent if the
        window's total for this symbol+direction now exceeds the threshold,
        else None.
        """
        # Only accumulation/distribution direction matters for clustering --
        # whale moves and generic transfers don't have a clear "direction".
        if transfer.flow_type not in (FlowType.ACCUMULATION, FlowType.DISTRIBUTION):
            return None

        key = (transfer.symbol, transfer.flow_type)
        self._prune(key)
        self._buckets.setdefault(key, []).append(transfer)

        bucket = self._buckets[key]
        total_usd = sum(t.usd_value for t in bucket)

        if total_usd >= CLUSTER_THRESHOLD_USD and len(bucket) > 1:
            event = ClusterEvent(
                chain=transfer.chain,
                symbol=transfer.symbol,
                flow_type=transfer.flow_type,
                total_usd=total_usd,
                transfer_count=len(bucket),
                window_seconds=self._window_seconds,
                transfers=list(bucket),
            )
            # Reset the bucket so we don't immediately re-fire on the next transfer.
            self._buckets[key] = []
            return event

        return None
