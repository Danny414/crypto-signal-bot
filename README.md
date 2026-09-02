# Crypto Signal Bot

An autonomous Python crypto signal bot that monitors ETH, BSC, and BASE on-chain activity, scans Binance market setups, filters crypto news, and uses DeepSeek to generate Telegram content.

## Components

- On-chain ERC-20 transfer scanning with JSON-RPC and Moralis enrichment
- Binance technical-analysis scanning
- RSS news polling with persistent deduplication and AI/news-impact filtering
- Telegram Bot API posting and shared source-bot ingestion
- DeepSeek decision and signal generation pipeline

## Running locally

cd signal-bot && python3 main.py

The bot reads credentials and destination settings from environment variables. Configure secrets through your environment or secret manager; never commit real tokens, API keys, passwords, or chat credentials.

Required environment variable names include DEEPSEEK_API_KEY, TELEGRAM_BOT_TOKEN, MAIN_CHANNEL_ID, INNER_CIRCLE_ID, ETH_SOURCE_BOT_TOKEN, BSC_SOURCE_BOT_TOKEN, BASE_SOURCE_BOT_TOKEN, SOURCE_GROUP_CHAT_ID, and MORALIS_API_KEY.

## Public-repository safety

This repository is a sanitized source snapshot. It intentionally excludes local Git history, Replit runtime configuration, screenshots and attachments, internal agent files, dependency caches, Python bytecode, and persistent RSS state.
