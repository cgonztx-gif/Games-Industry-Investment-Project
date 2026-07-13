from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from supabase import create_client


def main() -> None:
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    # Keepalive read to prevent free-tier inactivity pauses.
    client.table("api_cache").select("source").limit(1).execute()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    client.table("api_cache").delete().lt("fetched_at", cutoff).execute()

    # ccu_snapshots accumulates one row per game per hour (see
    # .github/workflows/ccu_hourly.yml) -- the dashboard only ever needs a
    # rolling recent window, so old raw snapshots are pruned the same way.
    ccu_cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    client.table("ccu_snapshots").delete().lt("captured_at", ccu_cutoff).execute()

    print("Supabase keepalive complete; api_cache and ccu_snapshots pruned before cutoff.")


if __name__ == "__main__":
    main()
