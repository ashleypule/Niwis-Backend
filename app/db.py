"""Read-only database access.

Every connection in the pool is opened with `default_transaction_read_only = on` and a statement timeout, so even a
buggy query cannot write to the shared Supabase database. Values are always bind parameters ($1, $2 …). Table and
column names come only from the validated dataset registry and are quoted with `ident()`; they are never taken from a
request.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Sequence
from typing import Any

import asyncpg

from app.config import Settings

log = logging.getLogger("niwis.db")

# Registry names include digits, dots, spaces (4_6, 10_2 tables) and, in the §9–§16 tables, parentheses, slashes,
# hyphens and colons ("Electrical Conductivity (EC)", "Lead/Cadmium/Chromium", "Ammonia (NH3-N)", "system:index").
# Never a double quote, so quoting cannot be broken out of.
_IDENT_OK = re.compile(r"^[A-Za-z0-9_. ()/:\-]{1,63}$")


def ident(name: str) -> str:
    """Quote a registry identifier for SQL. Rejects anything outside the character set the registry allows."""
    if not _IDENT_OK.match(name):
        raise ValueError(f"illegal identifier: {name!r}")
    return '"' + name + '"'


def qualified(schema: str, table: str) -> str:
    return f"{ident(schema)}.{ident(table)}"


class Database:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: asyncpg.Pool | None = None
        # background work (table statistics, category pre-warm) shares this limit; with the module warm-up running
        # one query at a time beside it, at least two connections stay free for requests
        self.background = asyncio.Semaphore(max(1, settings.db_pool_max - 3))

    @property
    def configured(self) -> bool:
        return self._settings.supabase_db_url is not None

    async def connect(self) -> None:
        if self._pool is not None or not self.configured:
            return
        assert self._settings.supabase_db_url is not None
        # Supabase's pooler does not forward startup parameters, so read-only mode and the timeout are set with SQL
        # on every new connection, verified, and re-asserted whenever a connection returns to the pool (asyncpg's
        # default reset runs RESET ALL, which would undo them).
        session_sql = (
            "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY; "
            f"SET statement_timeout = {int(self._settings.db_statement_timeout_ms)}; "
            "SET application_name = 'niwis-api';"
        )

        async def init(conn: asyncpg.Connection) -> None:
            await conn.execute(session_sql)
            if await conn.fetchval("SELECT current_setting('default_transaction_read_only')") != "on":
                raise RuntimeError("could not make the database session read-only; refusing to use it")

        async def reset(conn: asyncpg.Connection) -> None:
            await conn.execute("SELECT pg_advisory_unlock_all(); CLOSE ALL; " + session_sql)

        self._pool = await asyncpg.create_pool(
            dsn=self._settings.supabase_db_url.get_secret_value(),
            min_size=self._settings.db_pool_min,
            max_size=self._settings.db_pool_max,
            timeout=self._settings.db_connect_timeout_s,
            command_timeout=self._settings.db_statement_timeout_ms / 1000 + 5,
            # Supabase's pooler may run in transaction mode; named prepared statements are unsafe there.
            statement_cache_size=0,
            init=init,
            reset=reset,
        )
        log.info("database pool ready", extra={"pool_max": self._settings.db_pool_max})

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("database not connected")
        return self._pool

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        t0 = time.perf_counter()
        async with self._require_pool().acquire() as conn:
            rows: list[asyncpg.Record] = await conn.fetch(query, *args)
        log.debug("query", extra={"ms": round((time.perf_counter() - t0) * 1000, 1), "rows": len(rows)})
        return rows

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        async with self._require_pool().acquire() as conn:
            row: asyncpg.Record | None = await conn.fetchrow(query, *args)
            return row

    async def fetchrow_slow(self, query: str, *args: Any, timeout_ms: int = 60_000) -> asyncpg.Record | None:
        """A background statistics query (whole-table counts on 500 000-row tables) with a longer statement timeout,
        local to its read-only transaction. Never used on a request path; callers run them one at a time."""
        async with self._require_pool().acquire() as conn, conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
            row: asyncpg.Record | None = await conn.fetchrow(query, *args, timeout=timeout_ms / 1000 + 5)
            return row

    async def fetchval(self, query: str, *args: Any) -> Any:
        async with self._require_pool().acquire() as conn:
            return await conn.fetchval(query, *args)

    async def ping(self) -> tuple[bool, float | None, bool | None]:
        """(reachable, latency ms, session is read-only)."""
        if self._pool is None:
            return False, None, None
        t0 = time.perf_counter()
        try:
            async with self._pool.acquire() as conn:
                ro = await conn.fetchval("SELECT current_setting('default_transaction_read_only')")
            return True, round((time.perf_counter() - t0) * 1000, 1), ro == "on"
        except Exception:
            log.exception("database ping failed")
            return False, None, None


def as_dicts(rows: Sequence[asyncpg.Record]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]
