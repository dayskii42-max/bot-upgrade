from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from topup.config import ASSETS, MASTER_MNEMONIC
from topup.wallets import HDWalletEngine


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a deposit private key for sweeping.")
    parser.add_argument("asset", choices=sorted(ASSETS))
    parser.add_argument("index", type=int)
    args = parser.parse_args()
    if not MASTER_MNEMONIC:
        raise SystemExit("MASTER_MNEMONIC is missing from .env")

    engine = HDWalletEngine(MASTER_MNEMONIC)
    derived = engine.derive(args.asset, args.index)
    print(f"asset:   {args.asset}")
    print(f"index:   {derived.index}")
    print(f"path:    {derived.path}")
    print(f"address: {derived.address}")
    print(f"hex key: {engine.private_key_hex(args.asset, args.index)}")
    wif = engine.private_key_wif(args.asset, args.index)
    if wif:
        print(f"wif key: {wif}")
    print("\nKeep this private. Import it into your own wallet to sweep the funds.")


if __name__ == "__main__":
    main()
