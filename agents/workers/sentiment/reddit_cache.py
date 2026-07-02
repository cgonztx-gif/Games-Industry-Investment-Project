from __future__ import annotations

from typing import Protocol

from supabase import Client

from database.api_cache import InMemoryApiCache, SupabaseApiCache


class RedditCache(Protocol):
    def get(self, key: str, max_age_hours: float | None = None) -> list | None: ...
    def set(self, key: str, value: list) -> None: ...


class SupabaseRedditCache(SupabaseApiCache):
    """Backward-compatible Reddit-named wrapper around the generic api_cache client."""

    def __init__(
        self,
        client: Client,
        source: str = "reddit",
        table: str = "api_cache",
    ) -> None:
        super().__init__(client=client, source=source, table=table)


class InMemoryRedditCache(InMemoryApiCache):
    """Backward-compatible test fake for RedditSource tests."""
