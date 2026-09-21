"""
WALLET ENGINE.

Derives a unique address + private key per top-up from MASTER_MNEMONIC.
Customers only ever see the address. Copy this file with the rest of topup/.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from bip_utils import (
    Bip39MnemonicValidator,
    Bip39SeedGenerator,
    Bip44,
    Bip44Changes,
    Bip44Coins,
    Bip84,
    Bip84Coins,
)

from topup.config import ASSETS

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DerivedWallet:
    asset: str
    index: int
    path: str
    address: str


_COIN_SETUP = {
    "BTC": ("bip84", Bip84Coins.BITCOIN, 0, "m/84'/0'/0'/0/{index}"),
    "LTC": ("bip84", Bip84Coins.LITECOIN, 0, "m/84'/2'/0'/0/{index}"),
    "ETH": ("bip44", Bip44Coins.ETHEREUM, 0, "m/44'/60'/0'/0/{index}"),
    "USDT_ERC20": ("bip44", Bip44Coins.ETHEREUM, 1, "m/44'/60'/1'/0/{index}"),
    "USDT_TRC20": ("bip44", Bip44Coins.TRON, 0, "m/44'/195'/0'/0/{index}"),
}


class HDWalletEngine:
    """Derives a unique deposit address from one master mnemonic."""

    def __init__(self, mnemonic: str) -> None:
        # Normalize: strip, collapse whitespace
        mnemonic = " ".join(mnemonic.split())
        
        # Validate
        is_valid = Bip39MnemonicValidator().IsValid(mnemonic)
        if not is_valid:
            log.error(f"BIP39 validation failed for mnemonic: {repr(mnemonic)}")
            log.error(f"Mnemonic length: {len(mnemonic)} chars, {len(mnemonic.split())} words")
            raise SystemExit("MASTER_MNEMONIC is not a valid BIP39 mnemonic.")
        
        self._seed = Bip39SeedGenerator(mnemonic).Generate()
        self._accounts: dict[str, object] = {}

    def _account(self, asset: str):
        if asset not in ASSETS:
            raise ValueError(f"Unsupported asset: {asset}")
        cached = self._accounts.get(asset)
        if cached is not None:
            return cached

        kind, coin, account_index, _path = _COIN_SETUP[asset]
        if kind == "bip84":
            master = Bip84.FromSeed(self._seed, coin)
        else:
            master = Bip44.FromSeed(self._seed, coin)
        account = master.Purpose().Coin().Account(account_index)
        self._accounts[asset] = account
        return account

    def _ctx(self, asset: str, index: int, internal: bool = False):
        change = Bip44Changes.CHAIN_INT if internal else Bip44Changes.CHAIN_EXT
        return self._account(asset).Change(change).AddressIndex(index)

    def derive(self, asset: str, index: int) -> DerivedWallet:
        ctx = self._ctx(asset, index)
        _kind, _coin, _account, path = _COIN_SETUP[asset]
        return DerivedWallet(
            asset=asset,
            index=index,
            path=path.format(index=index),
            address=ctx.PublicKey().ToAddress(),
        )

    def fee_wallet(self, asset: str) -> DerivedWallet:
        ctx = self._ctx(asset, 0, internal=True)
        _kind, _coin, _account, path = _COIN_SETUP[asset]
        fee_path = path.replace("/0/{index}", "/1/0")
        return DerivedWallet(
            asset=asset,
            index=0,
            path=fee_path,
            address=ctx.PublicKey().ToAddress(),
        )

    def private_key_hex(self, asset: str, index: int, internal: bool = False) -> str:
        return self._ctx(asset, index, internal=internal).PrivateKey().Raw().ToHex()

    def fee_private_key_hex(self, asset: str) -> str:
        return self.private_key_hex(asset, 0, internal=True)

    def private_key_wif(self, asset: str, index: int) -> str | None:
        try:
            return self._ctx(asset, index).PrivateKey().ToWif()
        except Exception:
            return None

