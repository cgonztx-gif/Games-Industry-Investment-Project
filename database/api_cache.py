from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Protocol

from supabase import Client

logger = logging.getLogger("api_cache")


class ApiCache(Protocol):
    def get(self, key: str, max_age_hours: float | None = None) -> list | dict | None: ...
    def set(self, key: str, value: list | dict) -> None: ...


class SupabaseApiCache:
    """
    Generic api_cache table client.

    Fails open: cache read/write problems are logged and treated as misses/no-ops
    so a cache outage does not fail a worker.
    """

    def __init__(
        self,
        client: Client,
        source: str,
        table: str = "api_cache",
    ) -> None:
        self.client = client
        self.source = source
        self.table = table

    def get(self, key: str, max_age_hours: float | None = None) -> list | dict | None:
        try:
            q = (
                self.client.table(self.table)
                .select("payload, fetched_at")
                .eq("source", self.source)
                .eq("key", key)
            )
            if max_age_hours is not None:
                cutoff = (
                    datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
                ).isoformat()
                q = q.gte("fetched_at", cutoff)

            rows = q.limit(1).execute().data or []
            if not rows:
                return None
            return rows[0]["payload"]
        except Exception:
            logger.warning(
                "cache get failed for %s:%s; treating as miss",
                self.source,
                key,
                exc_info=True,
            )
            return None

    def set(self, key: str, value: list | dict) -> None:
        try:
            self.client.table(self.table).upsert(
                {
                    "source": self.source,
                    "key": key,
                    "payload": value,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="source,key",
            ).execute()
        except Exception:
            logger.warning(
                "cache set failed for %s:%s; continuing uncached",
                self.source,
                key,
                exc_info=True,
            )


class InMemoryApiCache:
    """Test fake with the same fresh/stale TTL semantics as SupabaseApiCache."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[list | dict, float]] = {}

    def get(self, key: str, max_age_hours: float | None = None) -> list | dict | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        payload, ts = entry
        if max_age_hours is not None and (time.time() - ts) > max_age_hours * 3600:
            return None
        return payload

    def set(self, key: str, value: list | dict) -> None:
        self._store[key] = (value, time.time())
