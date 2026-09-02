"""
ChainScanner polls an EVM-compatible chain (ETH, BSC, or BASE -- identical
architecture, different RPC URL) for new blocks every 15 seconds and
extracts ERC-20 Transfer events via eth_getLogs.

Both chains are handled by the exact same class; main.py instantiates it
twice, once per chain, with the appropriate RPC URL and chain label.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import aiohttp

from config import CHAIN_POLL_INTERVAL_SECONDS, ERC20_TRANSFER_TOPIC, HTTP_TIMEOUT_SECONDS

logger = logging.getLogger("onchain.scanner")

# Many free RPC providers cap eth_getLogs to a small block range (commonly
# 50). If our poll loop ever falls behind (slow downstream processing,
# provider hiccup), we cap each query to this many blocks and catch up over
# several iterations rather than requesting a too-large range and erroring.
_MAX_BLOCK_RANGE = 50


@dataclass
class RawTransfer:
    chain: str
    contract_address: str
    from_address: str
    to_address: str
    raw_value: int
    tx_hash: str
    block_number: int


def _decode_address(topic: str) -> str:
    # Topics are 32-byte hex strings; an address is the last 20 bytes.
    return "0x" + topic[-40:]


def _decode_uint(data: str) -> int:
    if not data or data == "0x":
        return 0
    return int(data, 16)


class ChainScanner:
    """Polls a single EVM chain for new ERC-20 Transfer log events."""

    def __init__(self, rpc_url: str | list[str], chain_label: str) -> None:
        self.rpc_urls = [rpc_url] if isinstance(rpc_url, str) else list(rpc_url)
        self.rpc_urls = list(dict.fromkeys(self.rpc_urls))
        self.chain_label = chain_label  # "ETH", "BSC", or "BASE"
        self._request_id = 0
        self._last_block: int | None = None
        self._rpc_index = 0

    async def _rpc_call(self, session: aiohttp.ClientSession, method: str, params: list) -> object | None:
        payload = {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}
        for attempt in range(len(self.rpc_urls)):
            self._request_id += 1
            payload["id"] = self._request_id
            url = self.rpc_urls[(self._rpc_index + attempt) % len(self.rpc_urls)]
            try:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        logger.warning("%s RPC HTTP %s for %s via %s", self.chain_label, resp.status, method, url)
                        continue
                    body = await resp.json()
                    if "error" in body:
                        logger.warning(
                            "%s RPC error for %s via %s: %s",
                            self.chain_label,
                            method,
                            url,
                            body["error"],
                        )
                        continue
                    self._rpc_index = (self._rpc_index + attempt) % len(self.rpc_urls)
                    return body.get("result")
            except (aiohttp.ClientError, TimeoutError) as exc:
                logger.warning("%s RPC call %s via %s failed: %s", self.chain_label, method, url, exc)

        self._rpc_index = (self._rpc_index + 1) % len(self.rpc_urls)
        return None

    async def _get_latest_block_number(self, session: aiohttp.ClientSession) -> int | None:
        result = await self._rpc_call(session, "eth_blockNumber", [])
        if result is None:
            return None
        return int(result, 16)

    async def _get_transfer_logs(
        self, session: aiohttp.ClientSession, from_block: int, to_block: int
    ) -> list[dict] | None:
        params = [
            {
                "fromBlock": hex(from_block),
                "toBlock": hex(to_block),
                "topics": [ERC20_TRANSFER_TOPIC],
            }
        ]
        result = await self._rpc_call(session, "eth_getLogs", params)
        if result is None:
            return None
        return result

    def _log_to_transfer(self, log: dict) -> RawTransfer | None:
        topics = log.get("topics", [])
        if len(topics) < 3:
            return None
        try:
            return RawTransfer(
                chain=self.chain_label,
                contract_address=log["address"],
                from_address=_decode_address(topics[1]),
                to_address=_decode_address(topics[2]),
                raw_value=_decode_uint(log.get("data", "0x")),
                tx_hash=log.get("transactionHash", ""),
                block_number=int(log.get("blockNumber", "0x0"), 16),
            )
        except (KeyError, ValueError):
            return None

    async def run(self, on_transfer: Callable[[RawTransfer], Awaitable[None]]) -> None:
        """
        Poll forever, calling `on_transfer` for every decoded ERC-20 transfer
        found in newly finalized blocks.
        """
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while True:
                try:
                    latest = await self._get_latest_block_number(session)
                    if latest is not None:
                        if self._last_block is None:
                            # First run: start from the current block, don't backfill history.
                            self._last_block = latest
                        elif latest > self._last_block:
                            to_block = min(latest, self._last_block + _MAX_BLOCK_RANGE)
                            logs = await self._get_transfer_logs(session, self._last_block + 1, to_block)
                            if logs is None:
                                # Do not advance over an unscanned range when
                                # every current provider is throttled/down.
                                await asyncio.sleep(2)
                                continue
                            for log in logs:
                                transfer = self._log_to_transfer(log)
                                if transfer is not None:
                                    try:
                                        await on_transfer(transfer)
                                    except Exception:  # noqa: BLE001 - keep the scanner alive
                                        logger.exception(
                                            "%s: error handling transfer %s", self.chain_label, transfer.tx_hash
                                        )
                            self._last_block = to_block
                except Exception:  # noqa: BLE001 - never let the poll loop die
                    logger.exception("%s: unexpected scanner error", self.chain_label)

                await asyncio.sleep(CHAIN_POLL_INTERVAL_SECONDS)
