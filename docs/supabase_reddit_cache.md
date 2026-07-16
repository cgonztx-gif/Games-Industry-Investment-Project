# SupabaseApiCache — Design

**Renamed from `SupabaseRedditCache` to `SupabaseApiCache`** (this doc's own closing
suggestion — see the "Reuse it" note at the bottom — was acted on). The class now
lives at `database/api_cache.py`, outside any single worker's package, since every
worker (news, market_player, sentiment, patch_notes, financial_overlay, the Returns
Tracker) constructs it directly. A thin backward-compatible `SupabaseRedditCache`
subclass remains in `agents/workers/sentiment/reddit_cache.py` for the Reddit
adapter's own call sites and tests, but new code should use `SupabaseApiCache`.

The concrete implementation of the `ApiCache` Protocol that `CachedRedditSource`
(and every other adapter's cache-wrapper) depends on. It backs the cache with a
Supabase (Postgres) table so the weekly run never re-fetches data it already has,
and — critically — so a blocked Reddit run can still return **last-known-good**
data instead of nothing.

## Role in the system

```
CachedRedditSource (or any other adapter's cache wrapper)
   │  get(key, max_age_hours) / set(key, value)
   ▼
SupabaseApiCache  ──►  api_cache table (Postgres/JSONB on Supabase)
```

Two design decisions worth stating up front:

1. **The cache is deliberately source-agnostic.** The table is `api_cache`, not
   `reddit_cache`, and rows carry a `source` column. The same table (and the same
   class, instantiated with a different `source`) caches Reddit, Steam reviews,
   YouTube comments, yfinance, GDELT, RSS, and Google News payloads today — see
   `agents/workers/*/worker.py`'s `SupabaseApiCache(client=db, source="...")`
   call sites. Don't couple a cache to one upstream.
2. **The cache stores JSON-native values (`list[dict]`), not domain dataclasses.**
   It knows nothing about `RedditPost`. The dataclass ↔ dict conversion lives one
   layer up, in `CachedRedditSource` — the adapter doc's §5 implements exactly this
   boundary; the rationale is in §4 below. This keeps the cache reusable for any
   source.

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
    source      text        not null,
    key         text        not null,
    payload     jsonb       not null,
    fetched_at  timestamptz not null default now(),
    primary key (source, key)
);

-- supports the pruning query below
create index if not exists api_cache_fetched_at_idx on api_cache (fetched_at);
```

(As actually applied via `database/migrations/002_api_cache.sql` — no default on
`source`; every caller names its namespace explicitly, matching §2's constructor
below.)

`(source, key)` is the composite primary key, so the same key string can exist under
different sources and lookups hit the PK index directly. `payload` is JSONB — Postgres
stores the list of post/comment dicts natively and it round-trips cleanly through
`supabase-py`.

## 2. The implementation

As actually built (`database/api_cache.py`) — a `client: Client` is passed in
already-constructed, rather than this class building its own from a URL/key pair,
so every worker shares the one `get_client()` connection instead of opening a
second one per adapter:

```python
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from typing import Protocol
from supabase import Client

logger = logging.getLogger("api_cache")


class ApiCache(Protocol):
    def get(self, key: str, max_age_hours: float | None = None) -> list | dict | None: ...
    def set(self, key: str, value: list | dict) -> None: ...


class SupabaseApiCache:
    """Generic api_cache table client. Fails OPEN: a cache outage degrades to a
    cache miss, never crashes the run."""

    def __init__(self, client: Client, source: str, table: str = "api_cache"):
        self.client = client
        self.source = source
        self.table = table

    def get(self, key: str, max_age_hours: float | None = None) -> list | dict | None:
        try:
            q = (self.client.table(self.table)
                 .select("payload, fetched_at")
                 .eq("source", self.source)
                 .eq("key", key))
            if max_age_hours is not None:
                cutoff = (datetime.now(timezone.utc)
                          - timedelta(hours=max_age_hours)).isoformat()
                q = q.gte("fetched_at", cutoff)        # stale rows filtered out
            rows = q.limit(1).execute().data or []     # a miss is an empty list —
            if not rows:                               # never an exception.
                return None
            return rows[0]["payload"]
        except Exception:                              # network, auth, decode, ...
            logger.warning("cache get failed for %s:%s; treating as miss",
                           self.source, key, exc_info=True)
            return None                                # fail open

    def set(self, key: str, value: list | dict) -> None:
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
            logger.warning("cache set failed for %s:%s; continuing uncached",
                           self.source, key, exc_info=True)
            # swallow: caching is an optimization, not a critical path
```

`fetched_at` is written explicitly on every `set` (not left to the column default) so
that an upsert *refreshes* the timestamp on update, not just on first insert — otherwise
TTL math would be measured from the row's original creation forever. `source` has no
default (unlike this doc's original sketch, which defaulted it to `"reddit"`) — every
call site names its own namespace explicitly (`"reddit"`, `"steam_appreviews"`,
`"gdelt"`, `"yfinance"`, ...), which is what keeps a typo from silently sharing another
adapter's cache rows.

## 3. Wiring it in

```python
from database.db_client import get_client
from database.api_cache import SupabaseApiCache

db = get_client()                                     # SUPABASE_URL + SUPABASE_KEY
cache = SupabaseApiCache(client=db, source="reddit")
reddit = build_reddit_source(lambda source: SupabaseApiCache(client=db, source=source))
                                                        # from the adapter doc --
                                                        # build_reddit_source takes a
                                                        # cache_factory, not one cache
                                                        # instance, since the fallback
                                                        # chain needs a separate
                                                        # namespace per leaf
```

## 4. The serialization boundary (rationale)

Because the cache stores `list[dict]`, `CachedRedditSource` owns the conversion to
and from dataclasses. This is the clean separation, and the adapter doc's §5 now
shows the full wrapper with both methods — the sketch below is the shape of the
`fetch_posts` side, kept here so this doc reads standalone:

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

As built, `InMemoryApiCache` (also in `database/api_cache.py`), with a thin
`InMemoryRedditCache` backward-compatible subclass alongside `SupabaseRedditCache`
in `agents/workers/sentiment/reddit_cache.py` for the Reddit adapter's own tests:

```python
import time

class InMemoryApiCache:
    def __init__(self):
        self._store: dict[str, tuple[list | dict, float]] = {}   # key -> (payload, ts)

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
```

A useful test: prime the fake, raise `RedditBlocked` from a stub inner source, and
assert `CachedRedditSource` returns the stale entry rather than propagating the error.

---

## Operational notes

**Use the service-role key, and keep it server-side.** This is a backend cache written
by the GitHub Actions job, not user-facing data. The service-role key bypasses Row
Level Security, which is what you want here — store it in GitHub Actions **secrets**
(as `SUPABASE_KEY`, the env var `get_client()` actually reads — this doc originally
sketched it as `SUPABASE_SERVICE_KEY`, a name the codebase doesn't use) and never ship
it to the Next.js frontend, which instead uses the separate anon key (see CLAUDE.md's
"Dashboard internals" section on why the two are never interchangeable). If you'd
rather keep RLS on, add a policy scoped to the service role for the `api_cache` table.

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

**Mind the 7-day inactivity pause.** Supabase pauses free-tier projects after **one
week without database activity**, and restoring is manual. A weekly cron sits exactly
on that boundary — one delayed or failed run and the *next* run finds a paused
database, which (because the cache fails open) silently degrades every fetch to
uncached and removes the stale-fallback safety net at the same time. The fix is a
second, midweek GitHub Actions job that does one trivial read or insert against this
table. Seconds of runtime; removes the whole failure class.

**Last-write-wins is fine for a cache.** Concurrent weekly runs aren't a concern at
this scale; the upsert's conflict resolution is sufficient and there's no need for
optimistic locking.

**Reuse it — done.** The class was renamed to `SupabaseApiCache` (see the top of this
doc) and is now instantiated directly by every collector that needs it:
`SupabaseApiCache(client=db, source="steam_appreviews")`,
`source="steam_review_text"`, `source="youtube_comments"`, `source="yfinance"`,
`source="gdelt"`, `source="news_rss"`, `source="gnews_rss"`,
`source="news_disambiguation"`, `source="dev_blog"`, and the original
`source="reddit"` / `"reddit_proxy"` / `"reddit_oauth"` namespaces. One table,
namespaced by `source`, caches all of them — and the pruning query covers every
source at once.
