from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from topup.config import DATABASE_PATH, MASTER_MNEMONIC
from topup.database import Database
from topup.sweep import Sweeper
from topup.wallets import HDWalletEngine


async def _run(limit: int) -> None:
    if not MASTER_MNEMONIC:
        raise SystemExit("MASTER_MNEMONIC is missing from .env")
    db = Database(DATABASE_PATH)
    await db.connect()
    http = httpx.AsyncClient(headers={"User-Agent": "sebby-shoes-topup/1.0"})
    sweeper = Sweeper(db, HDWalletEngine(MASTER_MNEMONIC), http)
    try:
        results = await sweeper.sweep_unswept(limit=limit)
        if not results:
            print("Nothing to sweep.")
            return
        for item in results:
            print(f"{item.asset} #{item.deposit_id}: {'ok' if item.ok else 'failed'} {item.detail} {item.txids}")
    finally:
        await sweeper.close()
        await http.aclose()
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sweep credited deposits to your treasury addresses.")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    asyncio.run(_run(args.limit))


if __name__ == "__main__":
    main()
