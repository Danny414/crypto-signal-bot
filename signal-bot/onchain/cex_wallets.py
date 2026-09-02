"""
Known exchange hot wallet addresses (EVM, lowercase) mapped to exchange
names. These work across both ETH and BSC since both are EVM chains and
major exchanges tend to reuse the same hot wallet addresses on both.

This list is not exhaustive -- it covers well-known, publicly documented
hot wallets used for classification (ACCUMULATION vs DISTRIBUTION). Extend
as needed.
"""

from __future__ import annotations

CEX_WALLETS: dict[str, str] = {
    # Binance
    "0x28c6c06298d514db089934071355e5743bf21d60": "Binance",
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": "Binance",
    "0xdfd5293d8e347dd8b2eb28a1f3fb26e8de0fe37e": "Binance",
    "0x56eddb7aa87536c09ccc2793473599fd21a8b17f": "Binance",
    "0x9696f59e4d72e237be84ffd425dcad154bf96f5": "Binance",
    "0x4976a4a02f38326660d17bf34b431dc6e2eb2327": "Binance",
    "0xf977814e90da44bfa03b6295a0616a897441acec": "Binance",
    "0x001866ae5b3de6caa5a51543fd9fb64f524f5478": "Binance",
    "0x5a52e96bacdabb82fd05763e25335261b270efcb": "Binance",
    "0x8894e0a0c962cb723c1976a4421c95949be2d4e3": "Binance (Cold)",
    "0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be": "Binance",
    # Coinbase
    "0x71660c4005ba85c37ccec55d0c4493e66fe775d3": "Coinbase",
    "0x503828976d22510aad0201ac7ec88293211d23da": "Coinbase",
    "0xddfabcdc4d8ffc6d5beaf154f18b778f892a0740": "Coinbase",
    "0x3cd751e6b0078be393132286c442345e5dc49699": "Coinbase",
    "0xb5d85cbf7cb3ee0d56b3bb207d5fc4b82f43f511": "Coinbase",
    "0xeb2629a2734e272bcc07bda959863f316f4bd4cf": "Coinbase",
    "0x02466e547bfdab679fc49e96bbfc62b9747d997c": "Coinbase Prime",
    # Kraken
    "0x2910543af39aba0cd09dbb2d50200b3e800a63d2": "Kraken",
    "0x0a869d79a7052c7f1b55a8ebabbea3420f0d1e13": "Kraken",
    "0xe853c56864a2ebe4576a807d26fdc4a0ada51919": "Kraken",
    "0xfa52274dd61e1643d2205169732f29114bc240b3": "Kraken",
    # OKX
    "0x236f9f97e0e62388479bf9e5ba4889e46b0273c3": "OKX",
    "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "OKX",
    "0xa7efae728d2936e78bda97dc267687568dd593f3": "OKX",
    "0x461c92a4b6f96b7d7b9c56cb3a3f4baacecd2d7b": "OKX",
    # Bybit
    "0xf89d7b9c864f589bbf53a82105107622b35eaa40": "Bybit",
    "0xee5b5b923ffce93a870b3104b7ca09c3db80047a": "Bybit",
    "0x8c4c4a4c0a6c1e7e6b9c3f5c2f0e2b9d5c6f7a8b": "Bybit",
    # KuCoin
    "0x2b5634c42055806a59e9107ed44d43c426e58258": "KuCoin",
    "0x689c56aef474df92d44a1b70850f808488f9769c": "KuCoin",
    "0xd6216fc19db775df9774a6e33526131da7d19a2c": "KuCoin",
    # Gate.io
    "0x0d0707963952f2fba59dd06f2b425ace40b492fe": "Gate.io",
    "0x7793cd85c11a924478d358d49b05b37e91b5810": "Gate.io",
    "0x1c4b70a3968436b9a0a9cf5205c787eb81bb558": "Gate.io",
    # HTX (Huobi)
    "0xdc76cd25977e0a5ae17155770273ad58648900d3": "HTX",
    "0xab5c66752a9e8167967685f1450532fb1e194811": "HTX",
    "0xe93381fb4c4f14bda253907b18fad305d799241a": "HTX",
    # MEXC
    "0x0211f3cedbef3143223d3acf0e589747933e8527": "MEXC",
    "0x9642b23ed1e01df1092b92641051881a322f5d4e": "MEXC",
    # Bitget
    "0x0639556f03714a74a5feeaf5736a4a64ff70d206": "Bitget",
    "0x5bdf85216ec1e38d6458c870992a69e38e03f7ef": "Bitget",
}


def label_wallet(address: str) -> str | None:
    """Return the exchange name for a known hot wallet address, or None."""
    if not address:
        return None
    return CEX_WALLETS.get(address.lower())


def is_exchange_wallet(address: str) -> bool:
    return label_wallet(address) is not None
