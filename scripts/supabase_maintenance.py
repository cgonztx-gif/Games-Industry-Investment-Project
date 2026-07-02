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
    print("Supabase keepalive complete; api_cache pruned before cutoff.")


if __name__ == "__main__":
    main()
