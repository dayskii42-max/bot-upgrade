from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from topup.config import ASSETS, AUTO_SWEEP, WATCH_WINDOW_DAYS
from topup.database import Database
from topup.blockchain import ChainWatcher
from topup.prices import PriceOracle
from topup.sweep import Sweeper

log = logging.getLogger(__name__)


class DepositPoller:
    def __init__(
        self,
        db: Database,
        chain: ChainWatcher,
        prices: PriceOracle,
        sweeper: Sweeper | None = None,
    ) -> None:
        self.db = db
        self.chain = chain
        self.prices = prices
        self.sweeper = sweeper

    async def poll(self, bot: Any) -> None:
        deposits = await self.db.list_watchable(WATCH_WINDOW_DAYS)
        for deposit in deposits:
            payments = await self.chain.incoming(deposit.asset, deposit.address)
            asset = ASSETS[deposit.asset]
            for payment in payments:
                if payment.amount <= 0:
                    continue
                if payment.confirmations < asset.confirmations:
                    continue
                if await self.db.already_credited(deposit.asset, payment.txid, deposit.id):
                    continue
                try:
                    amount_usd = await self.prices.to_usd(deposit.asset, payment.amount)
                except Exception:
                    log.exception("Price lookup failed; skipping credit until next poll")
                    continue
                new_balance = await self.db.credit(
                    telegram_id=deposit.telegram_id,
                    deposit_id=deposit.id,
                    asset=deposit.asset,
                    txid=payment.txid,
                    amount_crypto=payment.amount,
                    amount_usd=amount_usd,
                )
                if new_balance is None:
                    continue
                await self._notify(
                    bot,
                    deposit.telegram_id,
                    asset.name,
                    payment.amount,
                    amount_usd,
                    new_balance,
                )
        if self.sweeper and AUTO_SWEEP:
            await self.sweeper.sweep_unswept(limit=8)

    async def _notify(
        self,
        bot: Any,
        telegram_id: int,
        asset_name: str,
        amount: Decimal,
        amount_usd: Decimal,
        new_balance: Decimal,
    ) -> None:
        try:
            await bot.send_message(
                chat_id=telegram_id,
                text=(
                    f"Payment received: {format(amount, 'f').rstrip('0').rstrip('.')} {asset_name}\n"
                    f"Credited: ${amount_usd:.2f}\n"
                    f"New balance: ${new_balance:.2f}"
                ),
            )
        except Exception:
            log.exception("Could not notify user %s", telegram_id)
