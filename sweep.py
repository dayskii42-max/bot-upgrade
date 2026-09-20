"""
SWEEP ENGINE.

Moves credited deposits to your treasury addresses.
Needs SWEEP_TRON_ADDRESS / SWEEP_ETH_ADDRESS / etc in env.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from eth_account import Account
from tronpy import AsyncTron
from tronpy.keys import PrivateKey as TronPrivateKey
from tronpy.providers.async_http import AsyncHTTPProvider
from web3 import AsyncWeb3

from topup.config import (
    ASSETS,
    ETH_RPC_URL,
    TRONGRID_API_KEY,
    sweep_destination,
)
from topup.database import Database, Deposit
from topup.segwit import build_p2wpkh_tx, estimate_vsize, script_pubkey
from topup.wallets import HDWalletEngine

log = logging.getLogger(__name__)

USDT_ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
]

_BTC_NET = {
    "BTC": {
        "api": "https://mempool.space/api",
        "hrp": "bc",
        "p2pkh": 0x00,
        "fallback_sat_vb": 4,
    },
    "LTC": {
        "api": "https://litecoinspace.org/api",
        "hrp": "ltc",
        "p2pkh": 0x30,
        "fallback_sat_vb": 2,
    },
}

_TRX_TOPUP_SUN = 20_000_000
_TRX_MIN_SUN = 12_000_000
_EMPTY_GRACE = timedelta(minutes=12)


@dataclass
class SweepResult:
    deposit_id: int
    asset: str
    ok: bool
    txids: list[str]
    detail: str


class Sweeper:
    def __init__(self, db: Database, wallets: HDWalletEngine, http: httpx.AsyncClient) -> None:
        self.db = db
        self.wallets = wallets
        self.http = http
        self._lock = asyncio.Lock()
        self._w3: AsyncWeb3 | None = None
        self._tron: AsyncTron | None = None

    def _w3_client(self) -> AsyncWeb3:
        if self._w3 is None:
            self._w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(ETH_RPC_URL))
        return self._w3

    def _tron_client(self) -> AsyncTron:
        if self._tron is None:
            kwargs = {}
            if TRONGRID_API_KEY:
                kwargs["api_key"] = TRONGRID_API_KEY
            self._tron = AsyncTron(AsyncHTTPProvider("https://api.trongrid.io", **kwargs))
        return self._tron

    async def close(self) -> None:
        if self._tron is not None:
            await self._tron.close()
            self._tron = None

    async def sweep_unswept(self, limit: int = 15) -> list[SweepResult]:
        deposits = await self.db.list_unswept(limit)
        results: list[SweepResult] = []
        for deposit in deposits:
            results.append(await self.sweep_deposit(deposit))
        return results

    async def sweep_deposit(self, deposit: Deposit) -> SweepResult:
        dest = sweep_destination(deposit.asset)
        if not dest:
            return SweepResult(deposit.id, deposit.asset, False, [], "No treasury address configured.")
        if dest.lower() == deposit.address.lower():
            return SweepResult(deposit.id, deposit.asset, False, [], "Treasury address matches the deposit address.")

        async with self._lock:
            try:
                if deposit.asset in _BTC_NET:
                    result = await self._sweep_utxo(deposit, dest)
                elif deposit.asset == "ETH":
                    result = await self._sweep_eth(deposit, dest)
                elif deposit.asset == "USDT_ERC20":
                    result = await self._sweep_usdt_erc20(deposit, dest)
                elif deposit.asset == "USDT_TRC20":
                    result = await self._sweep_usdt_trc20(deposit, dest)
                else:
                    result = SweepResult(deposit.id, deposit.asset, False, [], "Sweep not implemented.")
            except Exception as exc:
                log.exception("Sweep failed for deposit %s", deposit.id)
                result = SweepResult(deposit.id, deposit.asset, False, [], str(exc))

            if result.ok:
                await self.db.mark_sweep(deposit.id, "swept", ",".join(result.txids) or None)
            elif "Waiting" in result.detail or result.detail.startswith("No treasury"):
                pass
            else:
                await self.db.mark_sweep(deposit.id, "failed", None)
            return result

    async def _recent_credit(self, deposit_id: int) -> bool:
        created = await self.db.last_credit_at(deposit_id)
        if created is None:
            return False
        when = datetime.fromisoformat(created)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - when < _EMPTY_GRACE

    async def _sweep_utxo(self, deposit: Deposit, dest: str) -> SweepResult:
        net = _BTC_NET[deposit.asset]
        utxo_res = await self.http.get(f"{net['api']}/address/{deposit.address}/utxo", timeout=20.0)
        utxo_res.raise_for_status()
        confirmed = [
            (row["txid"], int(row["vout"]), int(row["value"]))
            for row in utxo_res.json()
            if (row.get("status") or {}).get("confirmed")
        ]
        if not confirmed:
            if await self._recent_credit(deposit.id):
                return SweepResult(deposit.id, deposit.asset, False, [], "Waiting for UTXO indexer.")
            return SweepResult(deposit.id, deposit.asset, True, [], "empty")

        dest_script = script_pubkey(dest, net["hrp"], net["p2pkh"])
        sat_vb = await self._fee_rate(net["api"], net["fallback_sat_vb"])
        vsize = estimate_vsize(len(confirmed), dest_script)
        fee = max(vsize * sat_vb, 200)
        priv = self.wallets.private_key_hex(deposit.asset, deposit.derivation_index)
        raw = build_p2wpkh_tx(priv, confirmed, dest_script, fee)
        push = await self.http.post(f"{net['api']}/tx", content=raw, timeout=30.0)
        push.raise_for_status()
        txid = push.text.strip()
        return SweepResult(deposit.id, deposit.asset, True, [txid], f"Swept to {dest}")

    async def _fee_rate(self, api: str, fallback: int) -> int:
        try:
            res = await self.http.get(f"{api}/v1/fees/recommended", timeout=15.0)
            res.raise_for_status()
            return max(int(res.json().get("halfHourFee") or fallback), 1)
        except Exception:
            return fallback

    async def _sweep_eth(self, deposit: Deposit, dest: str) -> SweepResult:
        w3 = self._w3_client()
        from_addr = w3.to_checksum_address(deposit.address)
        to_addr = w3.to_checksum_address(dest)
        balance = await w3.eth.get_balance(from_addr)
        gas_price = await w3.eth.gas_price
        gas = 21_000
        fee = gas_price * gas
        if balance <= fee:
            if await self._recent_credit(deposit.id):
                return SweepResult(deposit.id, deposit.asset, False, [], "Waiting for ETH balance.")
            return SweepResult(deposit.id, deposit.asset, True, [], "empty")
        priv = "0x" + self.wallets.private_key_hex(deposit.asset, deposit.derivation_index)
        nonce = await w3.eth.get_transaction_count(from_addr)
        tx = {
            "from": from_addr,
            "to": to_addr,
            "value": balance - fee,
            "gas": gas,
            "gasPrice": gas_price,
            "nonce": nonce,
            "chainId": 1,
        }
        txid = await self._broadcast_eth(priv, tx)
        return SweepResult(deposit.id, deposit.asset, True, [txid], f"Swept to {dest}")

    async def _sweep_usdt_erc20(self, deposit: Deposit, dest: str) -> SweepResult:
        w3 = self._w3_client()
        token = ASSETS["USDT_ERC20"].contract
        from_addr = w3.to_checksum_address(deposit.address)
        to_addr = w3.to_checksum_address(dest)
        contract = w3.eth.contract(address=w3.to_checksum_address(token), abi=USDT_ERC20_ABI)
        token_bal = int(await contract.functions.balanceOf(from_addr).call())
        if token_bal <= 0:
            if await self._recent_credit(deposit.id):
                return SweepResult(deposit.id, deposit.asset, False, [], "Waiting for USDT balance.")
            return SweepResult(deposit.id, deposit.asset, True, [], "empty")

        priv = "0x" + self.wallets.private_key_hex(deposit.asset, deposit.derivation_index)
        fee_priv = "0x" + self.wallets.fee_private_key_hex(deposit.asset)
        fee_addr = w3.to_checksum_address(self.wallets.fee_wallet(deposit.asset).address)
        gas_price = await w3.eth.gas_price
        gas = 70_000
        need = gas_price * gas * 12 // 10
        eth_bal = await w3.eth.get_balance(from_addr)
        txids: list[str] = []
        if eth_bal < need:
            fee_bal = await w3.eth.get_balance(fee_addr)
            if fee_bal < need + (21_000 * gas_price):
                raise RuntimeError(
                    f"Fund the ETH fee wallet with ETH for gas: {fee_addr}"
                )
            txids.append(
                await self._broadcast_eth(
                    fee_priv,
                    {
                        "from": fee_addr,
                        "to": from_addr,
                        "value": need,
                        "gas": 21_000,
                        "gasPrice": gas_price,
                        "nonce": await w3.eth.get_transaction_count(fee_addr),
                        "chainId": 1,
                    },
                )
            )
            await self._wait_eth_balance(from_addr, need)

        nonce = await w3.eth.get_transaction_count(from_addr)
        transfer = await contract.functions.transfer(to_addr, token_bal).build_transaction(
            {
                "from": from_addr,
                "gas": gas,
                "gasPrice": gas_price,
                "nonce": nonce,
                "chainId": 1,
            }
        )
        txids.append(await self._broadcast_eth(priv, transfer))
        return SweepResult(deposit.id, deposit.asset, True, txids, f"Swept USDT to {dest}")

    async def _broadcast_eth(self, priv: str, tx: dict) -> str:
        w3 = self._w3_client()
        signed = Account.sign_transaction(tx, priv)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        txid = await w3.eth.send_raw_transaction(raw)
        return txid.hex() if hasattr(txid, "hex") else str(txid)

    async def _wait_eth_balance(self, address, minimum: int) -> None:
        w3 = self._w3_client()
        for _ in range(40):
            if await w3.eth.get_balance(address) >= minimum:
                return
            await asyncio.sleep(3)
        raise RuntimeError("Timed out waiting for gas ETH to arrive.")

    async def _sweep_usdt_trc20(self, deposit: Deposit, dest: str) -> SweepResult:
        client = self._tron_client()
        contract_addr = ASSETS["USDT_TRC20"].contract
        contract = await client.get_contract(contract_addr)
        token_bal = int(await contract.functions.balanceOf(deposit.address))
        if token_bal <= 0:
            if await self._recent_credit(deposit.id):
                return SweepResult(deposit.id, deposit.asset, False, [], "Waiting for USDT balance.")
            return SweepResult(deposit.id, deposit.asset, True, [], "empty")

        priv = TronPrivateKey(bytes.fromhex(self.wallets.private_key_hex(deposit.asset, deposit.derivation_index)))
        fee_wallet = self.wallets.fee_wallet(deposit.asset)
        fee_priv = TronPrivateKey(bytes.fromhex(self.wallets.fee_private_key_hex(deposit.asset)))
        trx_sun = await self._trx_balance_sun(deposit.address)
        txids: list[str] = []
        if trx_sun < _TRX_MIN_SUN:
            fee_trx = await self._trx_balance_sun(fee_wallet.address)
            if fee_trx < 25_000_000:
                raise RuntimeError(
                    f"Fund the TRON fee wallet with TRX for energy/fees: {fee_wallet.address}"
                )
            fund_tx = await client.trx.transfer(
                fee_wallet.address, deposit.address, _TRX_TOPUP_SUN
            ).build()
            fund_tx.sign(fee_priv)
            fund_res = await fund_tx.broadcast()
            txids.append(_tron_txid(fund_res))
            await self._wait_trx(deposit.address, _TRX_MIN_SUN)

        sweep_tx = await contract.functions.transfer(dest, token_bal).with_owner(
            deposit.address
        ).fee_limit(40_000_000).build()
        sweep_tx.sign(priv)
        sweep_res = await sweep_tx.broadcast()
        txids.append(_tron_txid(sweep_res))
        await self._wait_token_empty(contract, deposit.address)
        leftover = await self._trx_balance_sun(deposit.address)
        reclaim = leftover - 300_000
        if reclaim > 100_000:
            back_tx = await client.trx.transfer(
                deposit.address, fee_wallet.address, reclaim
            ).build()
            back_tx.sign(priv)
            back_res = await back_tx.broadcast()
            txids.append(_tron_txid(back_res))
        return SweepResult(deposit.id, deposit.asset, True, txids, f"Swept USDT to {dest}")

    async def _wait_token_empty(self, contract, address: str) -> None:
        for _ in range(20):
            try:
                if int(await contract.functions.balanceOf(address)) <= 0:
                    return
            except Exception:
                return
            await asyncio.sleep(3)

    async def _trx_balance_sun(self, address: str) -> int:
        try:
            trx = await self._tron_client().get_account_balance(address)
            return int(trx * 1_000_000)
        except Exception:
            return 0

    async def _wait_trx(self, address: str, minimum_sun: int) -> None:
        for _ in range(40):
            if await self._trx_balance_sun(address) >= minimum_sun:
                return
            await asyncio.sleep(3)
        raise RuntimeError("Timed out waiting for TRX gas to arrive.")


def _tron_txid(result) -> str:
    if isinstance(result, dict):
        return str(result.get("txid") or result.get("transaction") or result)
    return str(result)
