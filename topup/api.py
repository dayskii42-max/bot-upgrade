"""
COPY THIS MODULE INTO YOUR EXISTING BOT.

This is the only payment file you import.

    from topup.api import payments

Do not import bot.py. Do not replace your Railway start command.
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

import httpx

from topup.blockchain import ChainWatcher
from topup.config import (
    ASSETS,
    DATABASE_PATH,
    ENABLED_ASSETS,
    MASTER_MNEMONIC,
    POLL_INTERVAL_SECONDS,
    require_payments,
)
from topup.database import Database
from topup.poller import DepositPoller
from topup.prices import PriceOracle
from topup.sweep import Sweeper
from topup.wallets import HDWalletEngine

log = logging.getLogger(__name__)


class Payments:
    """HD one-time wallets, credit whatever arrives, optional sweep to treasury."""

    def __init__(self) -> None:
        self.db = Database(DATABASE_PATH)
        self.http: httpx.AsyncClient | None = None
        self.wallets: HDWalletEngine | None = None
        self.poller: DepositPoller | None = None
        self.sweeper: Sweeper | None = None
        self._task: asyncio.Task | None = None

    async def start(self, bot=None, autostart_poller: bool = True) -> None:
        """Call this from your existing bot startup. Pass your Telegram bot object."""
        require_payments()
        await self.db.connect()
        self.http = httpx.AsyncClient(headers={"User-Agent": "store-payments/1.0"})
        self.wallets = HDWalletEngine(MASTER_MNEMONIC)
        chain = ChainWatcher(self.http)
        prices = PriceOracle(self.http)
        self.sweeper = Sweeper(self.db, self.wallets, self.http)
        self.poller = DepositPoller(self.db, chain, prices, self.sweeper)
        if autostart_poller and bot is not None:
            self._task = asyncio.create_task(self._poll_loop(bot), name="payments-poller")
        log.info("Payments module started. Existing bot handlers are unchanged.")

    async def stop(self) -> None:
        """Call this from your existing bot shutdown."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self.sweeper is not None:
            await self.sweeper.close()
        if self.http is not None:
            await self.http.aclose()
        await self.db.close()

    async def _poll_loop(self, bot) -> None:
        while True:
            try:
                await self.poll(bot)
            except Exception:
                log.exception("Payment poll failed")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def poll(self, bot) -> None:
        if self.poller is None:
            raise RuntimeError("Call payments.start(bot) first.")
        await self.poller.poll(bot)

    def coins(self) -> list[dict[str, str]]:
        """Coins you can show as buttons: [{code, name}, ...]."""
        return [{"code": code, "name": ASSETS[code].name} for code in ENABLED_ASSETS]

    async def get_balance(self, telegram_id: int) -> Decimal:
        account = await self.db.get_user(telegram_id)
        return account.balance_usd

    async def balance_text(self, telegram_id: int) -> str:
        amount = await self.get_balance(telegram_id)
        return f"Your balance: ${amount:.2f}"

    async def create_topup(
        self,
        telegram_id: int,
        username: str | None,
        asset_code: str,
    ) -> dict:
        """
        Makes a unique derived wallet for this user/coin.
        Send result['message'] and result['address'] in Telegram.
        """
        if self.wallets is None:
            raise RuntimeError("Call payments.start(bot) first.")
        if asset_code not in ENABLED_ASSETS:
            raise ValueError(f"Coin not enabled: {asset_code}")
        await self.db.upsert_user(telegram_id, username)
        index = await self.db.next_index(asset_code)
        derived = self.wallets.derive(asset_code, index)
        deposit = await self.db.create_deposit(
            telegram_id=telegram_id,
            asset=asset_code,
            address=derived.address,
            derivation_index=derived.index,
            path=derived.path,
        )
        asset = ASSETS[asset_code]
        message = (
            f"Send any amount of {asset.name} to this one-time address.\n\n"
            f"<code>{derived.address}</code>\n\n"
            "There is no set invoice amount. The exact amount that arrives "
            f"is converted to USD and credited after {asset.confirmations} confirmation(s).\n\n"
            "Keep this chat open. You will get a message when it is credited."
        )
        return {
            "deposit_id": deposit.id,
            "asset": asset_code,
            "asset_name": asset.name,
            "address": derived.address,
            "confirmations": asset.confirmations,
            "index": derived.index,
            "path": derived.path,
            "message": message,
        }

    async def check_deposit(self, telegram_id: int, deposit_id: int, bot) -> str:
        deposit = await self.db.get_deposit(deposit_id)
        if deposit is None or deposit.telegram_id != telegram_id:
            return "That deposit was not found."
        await self.poll(bot)
        if await self.db.has_credit(deposit.id):
            return "Payment found and credited."
        return "No confirmed payment yet. Keep the address. It will credit automatically."

    async def sweep_now(self, limit: int = 40) -> str:
        if self.sweeper is None:
            raise RuntimeError("Call payments.start(bot) first.")
        results = await self.sweeper.sweep_unswept(limit=limit)
        if not results:
            return "Nothing to sweep."
        lines = []
        for item in results:
            status = "ok" if item.ok else "failed"
            lines.append(f"{item.asset} #{item.deposit_id}: {status} — {item.detail}")
        return "\n".join(lines)

    def fee_wallet_text(self) -> str:
        if self.wallets is None:
            raise RuntimeError("Call payments.start(bot) first.")
        lines = ["Fund these fee wallets so USDT sweeps can pay gas:"]
        for code in ("ETH", "USDT_ERC20", "USDT_TRC20"):
            if code in ASSETS:
                fee = self.wallets.fee_wallet(code)
                lines.append(f"{code}: {fee.address}")
        return "\n".join(lines)


# Import this in your current bot: from topup.api import payments
payments = Payments()
