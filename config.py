"""
PAYMENT CONFIG ONLY.

Do not copy this into your existing bot file.
Do not put TELEGRAM_BOT_TOKEN here. Your current bot already has that.

Railway: add these as extra variables. Do not delete your current ones.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Asset:
    code: str
    name: str
    coingecko_id: str
    decimals: int
    confirmations: int
    kind: str
    contract: str | None = None


ASSETS: dict[str, Asset] = {
    "BTC": Asset("BTC", "Bitcoin", "bitcoin", 8, 1, "btc_like"),
    "LTC": Asset("LTC", "Litecoin", "litecoin", 8, 1, "ltc_like"),
    "ETH": Asset("ETH", "Ethereum", "ethereum", 18, 6, "eth_native"),
    "USDT_TRC20": Asset(
        "USDT_TRC20",
        "USDT (TRC20)",
        "tether",
        6,
        1,
        "trc20",
        "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
    ),
    "USDT_ERC20": Asset(
        "USDT_ERC20",
        "USDT (ERC20)",
        "tether",
        6,
        6,
        "erc20",
        "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    ),
}

ROOT = Path(__file__).resolve().parent.parent


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# Your existing bot token is NOT used by this payment module.
MASTER_MNEMONIC = " ".join(_env("MASTER_MNEMONIC").split())
ADMIN_TELEGRAM_IDS = {int(x) for x in _split_csv(_env("ADMIN_TELEGRAM_IDS")) if x.isdigit()}

enabled = _split_csv(_env("ENABLED_ASSETS", "USDT_TRC20,BTC,LTC,ETH,USDT_ERC20"))
ENABLED_ASSETS = [code for code in enabled if code in ASSETS]
if not ENABLED_ASSETS:
    ENABLED_ASSETS = ["USDT_TRC20"]

POLL_INTERVAL_SECONDS = int(_env("POLL_INTERVAL_SECONDS", "20"))
WATCH_WINDOW_DAYS = int(_env("WATCH_WINDOW_DAYS", "30"))

# Separate file so this never overwrites your current bot database.
DATABASE_PATH = Path(_env("PAYMENTS_DB_PATH", _env("DATABASE_PATH", str(ROOT / "data" / "payments.db"))))

TRONGRID_API_KEY = _env("TRONGRID_API_KEY")
ETHERSCAN_API_KEY = _env("ETHERSCAN_API_KEY")
ETH_API_URL = _env("ETH_API_URL", "https://eth.blockscout.com/api")
ETH_RPC_URL = _env("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")

AUTO_SWEEP = _env("AUTO_SWEEP", "true").lower() in {"1", "true", "yes", "on"}
SWEEP_BTC_ADDRESS = _env("SWEEP_BTC_ADDRESS")
SWEEP_LTC_ADDRESS = _env("SWEEP_LTC_ADDRESS")
SWEEP_ETH_ADDRESS = _env("SWEEP_ETH_ADDRESS")
SWEEP_TRON_ADDRESS = _env("SWEEP_TRON_ADDRESS")

USD_QUANT = Decimal("0.01")

_SWEEP_DEST = {
    "BTC": lambda: SWEEP_BTC_ADDRESS,
    "LTC": lambda: SWEEP_LTC_ADDRESS,
    "ETH": lambda: SWEEP_ETH_ADDRESS,
    "USDT_ERC20": lambda: SWEEP_ETH_ADDRESS,
    "USDT_TRC20": lambda: SWEEP_TRON_ADDRESS,
}


def sweep_destination(asset_code: str) -> str:
    getter = _SWEEP_DEST.get(asset_code)
    return getter() if getter else ""


def require_payments() -> None:
    if not MASTER_MNEMONIC:
        raise RuntimeError(
            "Set MASTER_MNEMONIC in Railway/env. This payment module does not use your bot token."
        )
