"""
Central configuration for the signal bot.

Reads every environment variable the bot needs at startup and raises a
clear, specific error if anything required is missing. Also holds shared
constants (thresholds, cooldowns, stablecoin list) used across modules.
"""

from __future__ import annotations

import os
import sys


class ConfigError(RuntimeError):
    """Raised when a required environment variable is missing or invalid."""


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"Missing required environment variable: {name}. "
            f"Set it in Replit Secrets/Environment variables and restart the bot."
        )
    return value


def _optional(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value if value else default


def _require_int(name: str) -> int:
    raw = _require(name)
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name} must be an integer, got: {raw!r}") from exc


# ---------------------------------------------------------------------------
# Required secrets / credentials
# ---------------------------------------------------------------------------

try:
    DEEPSEEK_API_KEY = _require("DEEPSEEK_API_KEY")
    TELEGRAM_BOT_TOKEN = _require("TELEGRAM_BOT_TOKEN")
    MAIN_CHANNEL_ID = _require("MAIN_CHANNEL_ID")
    INNER_CIRCLE_ID = _require("INNER_CIRCLE_ID")
    # Source bots: the user's own existing ETH/BSC onchain scanner bots.
    # Telegram only allows one getUpdates consumer per bot token, and these
    # bots are already polled by their own running process elsewhere -- so
    # we can't long-poll their tokens directly. Instead the user redirects
    # both bots to post into a shared group, our own TELEGRAM_BOT_TOKEN bot
    # is an admin of that group, and we identify which source bot sent a
    # given group message by its Telegram user ID (resolved via getMe on
    # each source bot's token at startup).
    SOURCE_BOTS = {
        "ETH": _require("ETH_SOURCE_BOT_TOKEN"),
        "BSC": _require("BSC_SOURCE_BOT_TOKEN"),
        "BASE": _require("BASE_SOURCE_BOT_TOKEN"),
    }
    SOURCE_GROUP_CHAT_ID = _require("SOURCE_GROUP_CHAT_ID")
    MORALIS_API_KEY = _require("MORALIS_API_KEY")
except ConfigError as exc:
    print(f"\n[CONFIG ERROR] {exc}\n", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# RPC endpoints (free public defaults, overridable via env)
# ---------------------------------------------------------------------------

ETH_RPC_URL = _optional("ETH_RPC_URL", "https://rpc.mevblocker.io")
BSC_RPC_URL = _optional("BSC_RPC_URL", "https://bsc-dataseed.binance.org")
BASE_RPC_URL = _optional("BASE_RPC_URL", "https://mainnet.base.org")

# Public fallbacks prevent one provider quota outage from making the service
# appear healthy while it is no longer scanning.
ETH_RPC_FALLBACKS = (
    "https://eth.api.onfinality.io/public",
    "https://eth-mainnet.public.blastapi.io",
)
BSC_RPC_FALLBACKS = (
    "https://bsc.publicnode.com",
    "https://bsc-rpc.publicnode.com",
)
BASE_RPC_FALLBACKS = (
    "https://base-mainnet.public.blastapi.io",
    "https://base.drpc.org",
)

# ---------------------------------------------------------------------------
# DeepSeek models
# ---------------------------------------------------------------------------

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_FAST_MODEL = "deepseek-chat"
DEEPSEEK_REASONING_MODEL = "deepseek-reasoner"

# ---------------------------------------------------------------------------
# Onchain thresholds
# ---------------------------------------------------------------------------

MIN_TRANSFER_USD = 100_000
WHALE_THRESHOLD_USD = 100_000
CLUSTER_THRESHOLD_USD = 200_000
CLUSTER_WINDOW_SECONDS = 5 * 60
MAX_WHALE_MARKET_CAP_USD = 2_000_000_000

STABLECOINS = {
    "USDT", "USDC", "DAI", "USD1", "USDE", "USDG", "PYUSD", "RLUSD",
    "USDD", "XDC", "TUSD", "EURC", "FDUSD", "BUSD", "FRAX", "USDP",
    "GUSD", "LUSD", "EURT", "USTC",
}

# Explicitly excluded operational/major assets. These are not actionable
# low-cap whale movements for this service.
OPERATIONAL_WHALE_SYMBOLS = {"VVV", "AERO"}
MAJOR_ASSET_ROOTS = {"BTC", "ETH"}

# ---------------------------------------------------------------------------
# Anti-spam / cooldown rules
# ---------------------------------------------------------------------------

SIGNAL_COOLDOWN_SECONDS = 30 * 60              # 30 minutes per symbol
NEWS_POST_MIN_INTERVAL_SECONDS = 30 * 60        # 30 minutes between Main Channel news posts
MARKET_PULSE_IDLE_SECONDS = 90 * 60             # post a pulse if channel silent this long
MAX_TA_ONLY_SIGNALS_PER_DAY = 4                 # TA-only signals per day to Alpha AI

# ---------------------------------------------------------------------------
# Poll intervals
# ---------------------------------------------------------------------------

CHAIN_POLL_INTERVAL_SECONDS = 15
MARKET_SCANNER_INTERVAL_SECONDS = 5 * 60
RSS_POLL_INTERVAL_SECONDS = 3 * 60
MARKET_PULSE_CHECK_INTERVAL_SECONDS = 60

# ---------------------------------------------------------------------------
# ERC-20 Transfer event topic
# ---------------------------------------------------------------------------

ERC20_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# ---------------------------------------------------------------------------
# News sources
# ---------------------------------------------------------------------------

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss",
    "https://www.theblock.co/rss.xml",
]
AI_INCIDENT_RSS_FEEDS = [
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.technologyreview.com/feed/",
]

NEWS_IMPACT_SCORE_THRESHOLD = 7
NEWS_SPECIAL_SCORE_THRESHOLD = 5
SIGNAL_DECISION_SCORE_THRESHOLD = 5
MARKET_SCANNER_SCORE_THRESHOLD = 7

# ---------------------------------------------------------------------------
# Binance endpoints
# ---------------------------------------------------------------------------

BINANCE_SPOT_BASE = "https://api.binance.com"
BINANCE_FUTURES_BASE = "https://fapi.binance.com"
OKX_BASE = "https://www.okx.com"

HTTP_TIMEOUT_SECONDS = 15
