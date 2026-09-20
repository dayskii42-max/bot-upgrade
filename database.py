"""
PAYMENT DATABASE.

Uses data/payments.db and pay_* tables so this will not overwrite
your current bot users/orders tables.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import aiosqlite


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Deposit:
    id: int
    telegram_id: int
    asset: str
    address: str
    derivation_index: int
    path: str
    created_at: str
    sweep_status: str = "pending"
    sweep_txid: str | None = None


@dataclass
class UserAccount:
    telegram_id: int
    username: str | None
    balance_usd: Decimal


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA foreign_keys=ON;")
        await self._create()
        await self._migrate()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database is not connected.")
        return self._db

    async def _create(self) -> None:
        await self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS pay_users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                balance_usd TEXT NOT NULL DEFAULT '0',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pay_hd_counters (
                asset TEXT PRIMARY KEY,
                next_index INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pay_deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                asset TEXT NOT NULL,
                address TEXT NOT NULL UNIQUE,
                derivation_index INTEGER NOT NULL,
                path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                sweep_status TEXT NOT NULL DEFAULT 'pending',
                sweep_txid TEXT,
                FOREIGN KEY (telegram_id) REFERENCES pay_users(telegram_id)
            );

            CREATE TABLE IF NOT EXISTS pay_credits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                deposit_id INTEGER NOT NULL,
                asset TEXT NOT NULL,
                txid TEXT NOT NULL,
                amount_crypto TEXT NOT NULL,
                amount_usd TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (asset, txid, deposit_id),
                FOREIGN KEY (deposit_id) REFERENCES pay_deposits(id)
            );
            """
        )
        await self.db.commit()

    async def _migrate(self) -> None:
        async with self.db.execute("PRAGMA table_info(pay_deposits)") as cursor:
            cols = {row[1] for row in await cursor.fetchall()}
        if "sweep_status" not in cols:
            await self.db.execute(
                "ALTER TABLE pay_deposits ADD COLUMN sweep_status TEXT NOT NULL DEFAULT 'pending'"
            )
        if "sweep_txid" not in cols:
            await self.db.execute("ALTER TABLE pay_deposits ADD COLUMN sweep_txid TEXT")
        await self.db.commit()

    async def upsert_user(self, telegram_id: int, username: str | None) -> UserAccount:
        await self.db.execute(
            """
            INSERT INTO pay_users (telegram_id, username, balance_usd, created_at)
            VALUES (?, ?, '0', ?)
            ON CONFLICT(telegram_id) DO UPDATE SET username = excluded.username
            """,
            (telegram_id, username, _utcnow()),
        )
        await self.db.commit()
        return await self.get_user(telegram_id)

    async def get_user(self, telegram_id: int) -> UserAccount:
        async with self.db.execute(
            "SELECT telegram_id, username, balance_usd FROM pay_users WHERE telegram_id = ?",
            (telegram_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return UserAccount(telegram_id, None, Decimal("0"))
        return UserAccount(row["telegram_id"], row["username"], Decimal(row["balance_usd"]))

    async def next_index(self, asset: str) -> int:
        async with self._lock:
            async with self.db.execute(
                "SELECT next_index FROM pay_hd_counters WHERE asset = ?",
                (asset,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                index = 0
                await self.db.execute(
                    "INSERT INTO pay_hd_counters (asset, next_index) VALUES (?, 1)",
                    (asset,),
                )
            else:
                index = int(row["next_index"])
                await self.db.execute(
                    "UPDATE pay_hd_counters SET next_index = next_index + 1 WHERE asset = ?",
                    (asset,),
                )
            await self.db.commit()
            return index

    async def create_deposit(
        self,
        telegram_id: int,
        asset: str,
        address: str,
        derivation_index: int,
        path: str,
    ) -> Deposit:
        cursor = await self.db.execute(
            """
            INSERT INTO pay_deposits (
                telegram_id, asset, address, derivation_index, path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (telegram_id, asset, address, derivation_index, path, _utcnow()),
        )
        await self.db.commit()
        return Deposit(
            id=cursor.lastrowid,
            telegram_id=telegram_id,
            asset=asset,
            address=address,
            derivation_index=derivation_index,
            path=path,
            created_at=_utcnow(),
        )

    async def get_deposit(self, deposit_id: int) -> Deposit | None:
        async with self.db.execute(
            """
            SELECT id, telegram_id, asset, address, derivation_index, path,
                   created_at, sweep_status, sweep_txid
            FROM pay_deposits WHERE id = ?
            """,
            (deposit_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return self._deposit_from_row(row) if row else None

    async def list_watchable(self, window_days: int) -> list[Deposit]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        async with self.db.execute(
            """
            SELECT id, telegram_id, asset, address, derivation_index, path,
                   created_at, sweep_status, sweep_txid
            FROM pay_deposits
            WHERE created_at >= ?
            ORDER BY id ASC
            """,
            (cutoff,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._deposit_from_row(row) for row in rows]

    async def already_credited(self, asset: str, txid: str, deposit_id: int) -> bool:
        async with self.db.execute(
            """
            SELECT 1 FROM pay_credits
            WHERE asset = ? AND txid = ? AND deposit_id = ?
            """,
            (asset, txid, deposit_id),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def credit(
        self,
        telegram_id: int,
        deposit_id: int,
        asset: str,
        txid: str,
        amount_crypto: Decimal,
        amount_usd: Decimal,
    ) -> Decimal | None:
        async with self._lock:
            async with self.db.execute(
                """
                SELECT 1 FROM pay_credits
                WHERE asset = ? AND txid = ? AND deposit_id = ?
                """,
                (asset, txid, deposit_id),
            ) as cursor:
                if await cursor.fetchone() is not None:
                    return None

            async with self.db.execute(
                "SELECT balance_usd FROM pay_users WHERE telegram_id = ?",
                (telegram_id,),
            ) as cursor:
                row = await cursor.fetchone()
            current = Decimal(row["balance_usd"] if row else "0")
            new_balance = current + amount_usd
            await self.db.execute(
                """
                INSERT INTO pay_credits (
                    telegram_id, deposit_id, asset, txid,
                    amount_crypto, amount_usd, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    telegram_id,
                    deposit_id,
                    asset,
                    txid,
                    format(amount_crypto, "f"),
                    format(amount_usd, "f"),
                    _utcnow(),
                ),
            )
            await self.db.execute(
                "UPDATE pay_users SET balance_usd = ? WHERE telegram_id = ?",
                (format(new_balance, "f"), telegram_id),
            )
            await self.db.commit()
            return new_balance

    async def has_credit(self, deposit_id: int) -> bool:
        async with self.db.execute(
            "SELECT 1 FROM pay_credits WHERE deposit_id = ? LIMIT 1",
            (deposit_id,),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def admin_stats(self) -> tuple[int, int, Decimal]:
        async with self.db.execute("SELECT COUNT(*) AS n FROM pay_users") as cursor:
            users = int((await cursor.fetchone())["n"])
        async with self.db.execute("SELECT COUNT(*) AS n FROM pay_deposits") as cursor:
            deposits = int((await cursor.fetchone())["n"])
        async with self.db.execute(
            "SELECT COALESCE(SUM(CAST(amount_usd AS REAL)), 0) AS total FROM pay_credits"
        ) as cursor:
            total = Decimal(str((await cursor.fetchone())["total"]))
        return users, deposits, total

    async def list_unswept(self, limit: int) -> list[Deposit]:
        async with self.db.execute(
            """
            SELECT DISTINCT d.id, d.telegram_id, d.asset, d.address, d.derivation_index,
                   d.path, d.created_at, d.sweep_status, d.sweep_txid
            FROM pay_deposits d
            INNER JOIN pay_credits c ON c.deposit_id = d.id
            WHERE d.sweep_status IN ('pending', 'failed')
            ORDER BY d.id ASC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._deposit_from_row(row) for row in rows]

    async def mark_sweep(self, deposit_id: int, status: str, txid: str | None) -> None:
        await self.db.execute(
            "UPDATE pay_deposits SET sweep_status = ?, sweep_txid = ? WHERE id = ?",
            (status, txid, deposit_id),
        )
        await self.db.commit()

    async def last_credit_at(self, deposit_id: int) -> str | None:
        async with self.db.execute(
            "SELECT MAX(created_at) AS created_at FROM pay_credits WHERE deposit_id = ?",
            (deposit_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return row["created_at"] if row and row["created_at"] else None

    @staticmethod
    def _deposit_from_row(row: aiosqlite.Row) -> Deposit:
        keys = row.keys()
        return Deposit(
            id=row["id"],
            telegram_id=row["telegram_id"],
            asset=row["asset"],
            address=row["address"],
            derivation_index=row["derivation_index"],
            path=row["path"],
            created_at=row["created_at"],
            sweep_status=row["sweep_status"] if "sweep_status" in keys else "pending",
            sweep_txid=row["sweep_txid"] if "sweep_txid" in keys else None,
        )
