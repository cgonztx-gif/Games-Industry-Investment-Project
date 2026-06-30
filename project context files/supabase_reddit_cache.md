# SupabaseRedditCache — Design

The concrete implementation of the `RedditCache` Protocol that `CachedRedditSource`
depends on. It backs the cache with a Supabase (Postgres) table so the weekly run
never re-fetches data it already has, and — critically — so a blocked Reddit run can
still return **last-known-good** data instead of nothing.

## Role in the system

```
CachedRedditSource
   │  get(key, max_age_hours) / set(key, value)
   ▼
SupabaseRedditCache  ──►  api_cache table (Postgres/JSONB on Supabase)
```

Two design decisions worth stating up front:

1. **The cache is deliberately source-agnostic.** The table is `api_cache`, not
   `reddit_cache`, and rows carry a `source` column. The same table (and the same
   class, instantiated with a different `source`) can cache Steam, YouTube, or any
   other adapter's payloads. Don't couple a cache to one upstream.
2. **The cache stores JSON-native values (`list[dict]`), not domain dataclasses.**
   It knows nothing about `RedditPost`. The dataclass ↔ dict conversion lives one
   layer up, in `CachedRedditSource`. This keeps the cache reusable and is a small
   refinement to the Protocol sketch from the adapter doc — see §4.

## TTL semantics (the important subtlety)

`get(key, max_age_hours)` behaves differently depending on the argument, and
`CachedRedditSource` relies on both modes:

- **Fresh read** — `max_age_hours=24`: return the row only if it was fetched within
  the window; otherwise treat as a miss so the source re-fetches.
- **Stale read** — `max_age_hours=None`: return whatever exists regardless of age.
  This is the **graceful-degradation path**: when Reddit is blocked, the wrapper
  calls `get(key)` with no TTL to serve the last good copy.

---

## 1. Table schema

```sql
create table if not exists api_cache (
    source      text        not null default 'reddit',
    key         text        not null,
    payload     jsonb       not null,
    fetched_at  timestamptz not null default now(),
    primary key (source, key)
);

-- supports the pruning query below
create index if not exists api_cache_fetched_at_idx on api_cache (fetched_at);
```

`(source, key)` is the composite primary key, so the same key string can exist under
different sources and lookups hit the PK index directly. `payload` is JSONB — Postgres
stores the list of post/comment dicts natively and it round-trips cleanly through
`supabase-py`.

## 2. The implementation

```python
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client

logger = logging.getLogger("reddit_cache")


class SupabaseRedditCache:
    """RedditCache backed by the api_cache table. Generic JSON blob store with TTL.
    Fails OPEN: a cache outage degrades to a cache miss, never crashes the run."""

    def __init__(self, url: str, service_key: str,
                 source: str = "reddit", table: str = "api_cache"):
        self.client: Client = create_client(url, service_key)
        self.source = source
        self.table = table

    def get(self, key: str, max_age_hours: float | None = None) -> list | None:
        try:
            q = (self.client.table(self.table)
                 .select("payload, fetched_at")
                 .eq("source", self.source)
                 .eq("key", key))
            if max_age_hours is not None:
                cutoff = (datetime.now(timezone.utc)
                          - timedelta(hours=max_age_hours)).isoformat()
                q = q.gte("fetched_at", cutoff)        # stale rows filtered out
            resp = q.maybe_single().execute()
            if resp is None or not resp.data:
                return None                            # miss (or filtered as stale)
            return resp.data["payload"]
        except Exception:                              # network, auth, decode, ...
            logger.warning("cache get failed for %s:%s — treating as miss",
                           self.source, key, exc_info=True)
            return None                                # fail open

    def set(self, key: str, value: list) -> None:
        try:
            self.client.table(self.table).upsert(
                {
                    "source": self.source,
                    "key": key,
                    "payload": value,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="source,key",              # last-write-wins
            ).execute()
        except Exception:
            logger.warning("cache set failed for %s:%s — continuing uncached",
                           self.source, key, exc_info=True)
            # swallow: caching is an optimization, not a critical path
```

`fetched_at` is written explicitly on every `set` (not left to the column default) so
that an upsert *refreshes* the timestamp on update, not just on first insert — otherwise
TTL math would be measured from the row's original creation forever.

## 3. Wiring it in

```python
import os
cache = SupabaseRedditCache(
    url=os.environ["SUPABASE_URL"],
    service_key=os.environ["SUPABASE_SERVICE_KEY"],   # service role — see notes
    source="reddit",
)
reddit = build_reddit_source(cache)                   # from the adapter doc
```

## 4. The serialization boundary (refinement to the adapter doc)

Because the cache now stores `list[dict]`, `CachedRedditSource` owns the conversion to
and from dataclasses. This is the clean separation; update the wrapper's methods to:

```python
from dataclasses import asdict

class CachedRedditSource:
    def __init__(self, inner, cache, ttl_hours: int = 24):
        self.inner, self.cache, self.ttl_hours = inner, cache, ttl_hours

    def fetch_posts(self, subreddit, sort="top", timeframe="week", limit=100):
        key = f"posts:{subreddit}:{sort}:{timeframe}"
        fresh = self.cache.get(key, max_age_hours=self.ttl_hours)
        if fresh is not None:
            return [RedditPost(**d) for d in fresh]            # dict -> dataclass
        try:
            posts = self.inner.fetch_posts(subreddit, sort, timeframe, limit)
            self.cache.set(key, [asdict(p) for p in posts])    # dataclass -> dict
            return posts
        except RedditBlocked:
            stale = self.cache.get(key)                         # no TTL: stale ok
            if stale is not None:
                logger.warning("blocked; serving stale cache for r/%s", subreddit)
                return [RedditPost(**d) for d in stale]
            raise
    # fetch_comments mirrors this with RedditComment(**d)
```

`RedditPost(**d)` works directly because the dataclasses are flat and the dict keys
match the field names — keep them flat for exactly this reason. If you ever add nested
fields, switch to a small `from_dict` classmethod instead of `**d`.

## 5. In-memory fake for tests

So CI never touches Supabase and cache behavior is unit-testable. It honors the same
TTL semantics, including the stale path.

```python
import time

class InMemoryRedditCache:
    def __init__(self):
        self._store: dict[str, tuple[list, float]] = {}   # key -> (payload, ts)

    def get(self, key: str, max_age_hours: float | None = None) -> list | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        payload, ts = entry
        if max_age_hours is not None and (time.time() - ts) > max_age_hours * 3600:
            return None
        return payload

    def set(self, key: str, value: list) -> None:
        self._store[key] = (value, time.time())
```

A useful test: prime the fake, raise `RedditBlocked` from a stub inner source, and
assert `CachedRedditSource` returns the stale entry rather than propagating the error.

---

## Operational notes

**Use the service-role key, and keep it server-side.** This is a backend cache written
by the GitHub Actions job, not user-facing data. The service-role key bypasses Row
Level Security, which is what you want here — store it in GitHub Actions **secrets**
(`SUPABASE_SERVICE_KEY`) and never ship it to the Next.js frontend. If you'd rather
keep RLS on, add a policy scoped to the service role for the `api_cache` table.

**Fail-open is intentional.** A cache `get` or `set` that errors (Supabase down,
transient network) is logged and treated as a miss / no-op so the run continues. The
one place this bites is the degradation path: if Supabase is unreachable *and* Reddit
is blocked at the same time, there's no fallback left and `RedditBlocked` propagates —
acceptable, and your observability should surface it.

**Prune so the free tier stays roomy.** The free tier is ~500 MB. Cached payloads are
small (single-digit MB/week at the volumes in the adapter doc), but unbounded growth is
sloppy. Run a weekly cleanup — either as a step in the Actions cron or via Supabase
`pg_cron`:

```sql
delete from api_cache where fetched_at < now() - interval '14 days';
```

Set the retention longer than your TTL plus a comfortable margin, so the stale-fallback
path always has something to serve.

**Last-write-wins is fine for a cache.** Concurrent weekly runs aren't a concern at
this scale; the upsert's conflict resolution is sufficient and there's no need for
optimistic locking.

**Reuse it.** When you build the Steam, YouTube, or studio-intel collectors, instantiate
`SupabaseRedditCache(..., source="steam")` (or rename the class to `SupabaseApiCache`
to drop the misleading name). One table, namespaced by `source`, caches all of them —
and the pruning query covers every source at once.
