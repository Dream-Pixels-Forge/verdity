"""
Async SQLite wrapper using stdlib sqlite3 + asyncio.to_thread.

Provides an async interface compatible with patterns used by aiosqlite,
so no extra package installation is needed.
All sqlite3 operations run on a single executor thread to respect SQLite's
thread-affinity rules; cursors and Row objects never cross thread boundaries.
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any, Sequence


class AsyncConnection:
    """Thin async wrapper around sqlite3.Connection."""

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._conn: sqlite3.Connection | None = None
        self.row_factory = sqlite3.Row

    async def connect(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._conn = await self._loop.run_in_executor(
            None, lambda: sqlite3.connect(self._path, check_same_thread=False)
        )
        self._conn.row_factory = self.row_factory
        await self._loop.run_in_executor(
            None,
            lambda c=self._conn: (
                c.execute("PRAGMA journal_mode=WAL"),
                c.execute("PRAGMA synchronous=NORMAL"),
            ),
        )

    async def close(self) -> None:
        if self._conn:
            await self._loop.run_in_executor(None, self._conn.close)
            self._conn = None

    async def __aenter__(self) -> AsyncConnection:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        await self.close()

    async def execute(self, sql: str, params: Sequence[Any] | None = None) -> list[dict]:
        """Execute SQL and return ALL rows as a list of dicts. For queries."""
        conn = self._conn
        return await self._loop.run_in_executor(
            None, lambda: [dict(r) for r in conn.execute(sql, params or ())]
        )

    async def execute_one(self, sql: str, params: Sequence[Any] | None = None) -> dict | None:
        """Execute SQL and return the FIRST row as a dict, or None."""
        conn = self._conn
        return await self._loop.run_in_executor(
            None, lambda: _row_to_dict(conn.execute(sql, params or ()).fetchone())
        )

    async def executescript(self, sql: str) -> None:
        """Execute multiple SQL statements (no params, no return value)."""
        conn = self._conn
        await self._loop.run_in_executor(None, conn.executescript, sql)

    async def commit(self) -> None:
        await self._loop.run_in_executor(None, self._conn.commit)

    async def rollback(self) -> None:
        await self._loop.run_in_executor(None, self._conn.rollback)


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)
