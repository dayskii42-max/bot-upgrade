from __future__ import annotations

import time
from decimal import Decimal

import httpx

from topup.config import ASSETS, USD_QUANT

_IDS = ",".join(sorted({asset.coingecko_id for asset in ASSETS.values()}))
_CACHE_TTL = 60.0


class PriceOracle:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http
        self._cache: dict[str, Decimal] = {}
        self._cached_at = 0.0

    async def usd_rate(self, asset_code: str) -> Decimal:
        asset = ASSETS[asset_code]
        if self._cache and (time.monotonic() - self._cached_at) < _CACHE_TTL:
            return self._cache[asset.coingecko_id]
        response = await self.http.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": _IDS, "vs_currencies": "usd"},
            timeout=20.0,
        )
        response.raise_for_status()
        payload = response.json()
        for gecko_id, values in payload.items():
            self._cache[gecko_id] = Decimal(str(values["usd"]))
        self._cached_at = time.monotonic()
        return self._cache[asset.coingecko_id]

    async def to_usd(self, asset_code: str, amount: Decimal) -> Decimal:
        return (amount * await self.usd_rate(asset_code)).quantize(USD_QUANT)
