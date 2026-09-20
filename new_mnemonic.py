from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bip_utils import Bip39MnemonicGenerator, Bip39WordsNum

from topup.wallets import HDWalletEngine, _COIN_SETUP


def main() -> None:
    mnemonic = Bip39MnemonicGenerator().FromWordsNumber(Bip39WordsNum.WORDS_NUM_24)
    print("Write this mnemonic down offline. Anyone with it can spend store deposits.\n")
    print(mnemonic)
    print("\nFirst address for each coin (index 0):\n")
    engine = HDWalletEngine(str(mnemonic))
    for asset in _COIN_SETUP:
        derived = engine.derive(asset, 0)
        print(f"{asset:12} {derived.path:22} {derived.address}")
    print("\nFee wallets (fund these with ETH / TRX so token sweeps can pay gas):\n")
    for asset in ("ETH", "USDT_ERC20", "USDT_TRC20"):
        fee = engine.fee_wallet(asset)
        print(f"{asset:12} {fee.path:22} {fee.address}")
    print("\nPut the mnemonic in .env as MASTER_MNEMONIC=word1 word2 ... word24")


if __name__ == "__main__":
    main()
