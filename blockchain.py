from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, getcontext

import httpx

from topup.config import ASSETS, Asset, ETHERSCAN_API_KEY, ETH_API_URL, TRONGRID_API_KEY

getcontext().prec = 50
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IncomingPayment:
    txid: str
    amount: Decimal
    confirmations: int


def _amount(raw: int | str, decimals: int) -> Decimal:
    return Decimal(str(raw)) / (Decimal(10) ** decimals)


class ChainWatcher:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http

    async def incoming(self, asset_code: str, address: str) -> list[IncomingPayment]:
        asset = ASSETS[asset_code]
        try:
            if asset.kind == "btc_like":
                return await self._btc_like("https://mempool.space/api", address, asset)
            if asset.kind == "ltc_like":
                return await self._btc_like("https://litecoinspace.org/api", address, asset)
            if asset.kind == "eth_native":
                return await self._etherscan("txlist", address, asset)
            if asset.kind == "erc20":
                return await self._etherscan("tokentx", address, asset)
            if asset.kind == "trc20":
                return await self._trc20(address, asset)
        except Exception:
            log.exception("Failed watching %s %s", asset_code, address)
        return []

    async def _btc_like(self, base: str, address: str, asset: Asset) -> list[IncomingPayment]:
        tip_res = await self.http.get(f"{base}/blocks/tip/height", timeout=20.0)
        tip_res.raise_for_status()
        tip = int(tip_res.text)
        tx_res = await self.http.get(f"{base}/address/{address}/txs", timeout=20.0)
        tx_res.raise_for_status()
        payments: list[IncomingPayment] = []
        for tx in tx_res.json():
            received = 0
            for vout in tx.get("vout", []):
                if vout.get("scriptpubkey_address") == address:
                    received += int(vout.get("value") or 0)
            if received <= 0:
                continue
            status = tx.get("status") or {}
            if status.get("confirmed"):
                confirmations = tip - int(status["block_height"]) + 1
            else:
                confirmations = 0
            payments.append(
                IncomingPayment(tx["txid"], _amount(received, asset.decimals), confirmations)
            )
        return payments

    async def _etherscan(self, action: str, address: str, asset: Asset) -> list[IncomingPayment]:
        params = {
            "module": "account",
            "action": action,
            "address": address,
            "sort": "desc",
            "page": 1,
            "offset": 50,
        }
        if ETHERSCAN_API_KEY:
            params["apikey"] = ETHERSCAN_API_KEY
        if asset.contract and action == "tokentx":
            params["contractaddress"] = asset.contract
        response = await self.http.get(ETH_API_URL, params=params, timeout=20.0)
        response.raise_for_status()
        payload = response.json()
        result = payload.get("result")
        if not isinstance(result, list):
            return []

        wanted = address.lower()
        contract = (asset.contract or "").lower()
        payments: list[IncomingPayment] = []
        for tx in result:
            if str(tx.get("to", "")).lower() != wanted:
                continue
            if tx.get("isError") not in (None, "0", 0):
                continue
            if contract and str(tx.get("contractAddress", "")).lower() != contract:
                continue
            decimals = int(tx.get("tokenDecimal") or asset.decimals)
            confirmations = int(tx.get("confirmations") or 0)
            payments.append(
                IncomingPayment(
                    txid=tx.get("hash") or tx.get("transactionHash"),
                    amount=_amount(tx.get("value") or "0", decimals),
                    confirmations=confirmations,
                )
            )
        return payments

    async def _trc20(self, address: str, asset: Asset) -> list[IncomingPayment]:
        headers = {}
        if TRONGRID_API_KEY:
            headers["TRON-PRO-API-KEY"] = TRONGRID_API_KEY
        response = await self.http.get(
            f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20",
            params={
                "only_to": "true",
                "limit": 50,
                "contract_address": asset.contract,
            },
            headers=headers,
            timeout=20.0,
        )
        response.raise_for_status()
        payments: list[IncomingPayment] = []
        for tx in response.json().get("data") or []:
            if tx.get("to") != address:
                continue
            token_contract = (tx.get("token_info") or {}).get("address")
            if asset.contract and token_contract and token_contract != asset.contract:
                continue
            decimals = int((tx.get("token_info") or {}).get("decimals") or asset.decimals)
            payments.append(
                IncomingPayment(
                    txid=tx["transaction_id"],
                    amount=_amount(tx.get("value") or "0", decimals),
                    confirmations=max(asset.confirmations, 1),
                )
            )
        return payments
