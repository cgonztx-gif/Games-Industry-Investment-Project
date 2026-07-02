"""
RAWG Backfill — one-time script to populate games.rawg_slug and games.steam_app_id.

For every game in the DB where rawg_slug IS NULL, this script:
  1. Searches RAWG by title (3 s sleep — free tier is 20 req/min)
  2. On a match, fetches the game's /stores endpoint to extract the Steam app ID (another 3 s sleep)
  3. Updates the games row with rawg_slug, steam_app_id (if found), metacritic, esrb_rating

The script is fully resumable: re-running it only touches rows still missing rawg_slug.

Usage:
    python scripts/rawg_backfill.py                   # full run (rawg_slug IS NULL)
    python scripts/rawg_backfill.py --dry-run         # print plan, no DB writes
    python scripts/rawg_backfill.py --limit 50        # process at most N games
    python scripts/rawg_backfill.py --offset 200      # skip first N games (resume)
    python scripts/rawg_backfill.py --limit 50 --offset 200
    python scripts/rawg_backfill.py --chunk-size 100  # one bounded, stateful chunk
    python scripts/rawg_backfill.py --chunk-size 100 --max-chunks 5
    python scripts/rawg_backfill.py --cleanup-unsafe-state --dry-run
    python scripts/rawg_backfill.py --cleanup-unsafe-state
    python scripts/rawg_backfill.py --fix-steam       # fix games that have rawg_slug but no steam_app_id
    python scripts/rawg_backfill.py --fix-steam --limit 100
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from agents.workers.market_player.rawg_client import search_game, get_steam_app_id, _title_match_score
from database.db_client import get_client, get_games_missing_rawg, update_game_rawg_data

DEFAULT_STATE_FILE = _ROOT / ".rawg_backfill_state.json"
STATE_VERSION = 1
FETCH_PAGE_SIZE = 1000


def _release_year(release_date: str | None) -> int | None:
    if release_date and len(release_date) >= 4:
        try:
            return int(release_date[:4])
        except ValueError:
            pass
    return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_state() -> dict:
    return {"version": STATE_VERSION, "rawg_missing": {}}


def _load_state(path: Path) -> dict:
    if not path.exists():
        return _new_state()

    with path.open("r", encoding="utf-8") as fh:
        state = json.load(fh)

    if not isinstance(state, dict):
        raise ValueError(f"State file is not a JSON object: {path}")
    if state.get("version") != STATE_VERSION:
        raise ValueError(f"Unsupported RAWG backfill state version in {path}")
    state.setdefault("rawg_missing", {})
    return state


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")
    tmp_path.replace(path)


def _state_skip_ids(state: dict, retry_errors: bool) -> set[str]:
    skipped: set[str] = set()
    for game_id, attempt in state.get("rawg_missing", {}).items():
        status = attempt.get("status")
        if status in {"matched", "no_match"} or (status == "error" and not retry_errors):
            skipped.add(str(game_id))
    return skipped


def _record_state_attempt(
    state: dict,
    game: dict,
    status: str,
    rawg_slug: str | None = None,
    rawg_name: str | None = None,
    match_score: float | None = None,
    steam_app_id: str | None = None,
    error: str | None = None,
) -> None:
    state["rawg_missing"][str(game["game_id"])] = {
        "status": status,
        "title": game["title"],
        "attempted_at": _utc_now(),
    }
    if rawg_slug:
        state["rawg_missing"][str(game["game_id"])]["rawg_slug"] = rawg_slug
    if rawg_name:
        state["rawg_missing"][str(game["game_id"])]["rawg_name"] = rawg_name
    if match_score is not None:
        state["rawg_missing"][str(game["game_id"])]["match_score"] = match_score
    if steam_app_id:
        state["rawg_missing"][str(game["game_id"])]["steam_app_id"] = steam_app_id
    if error:
        state["rawg_missing"][str(game["game_id"])]["error"] = error[:500]


def _count_games_missing_rawg(client) -> int:
    resp = (
        client.table("games")
        .select("game_id", count="exact")
        .is_("rawg_slug", "null")
        .limit(1)
        .execute()
    )
    return int(resp.count or 0)


def _get_unattempted_games_missing_rawg(
    client,
    limit: int,
    skip_ids: set[str],
) -> list[dict]:
    """Return up to limit missing-RAWG games that are not in the chunk state."""
    selected: list[dict] = []
    offset = 0

    while len(selected) < limit:
        rows = (
            client.table("games")
            .select("game_id, title, release_date, steam_app_id")
            .is_("rawg_slug", "null")
            .order("title")
            .range(offset, offset + FETCH_PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        if not rows:
            break

        for row in rows:
            if str(row["game_id"]) in skip_ids:
                continue
            selected.append(row)
            if len(selected) >= limit:
                break

        if len(rows) < FETCH_PAGE_SIZE:
            break
        offset += FETCH_PAGE_SIZE

    return selected


def _get_games_missing_steam(client, limit: int, offset: int) -> list[dict]:
    """Return games that have rawg_slug but are still missing steam_app_id."""
    q = (
        client.table("games")
        .select("game_id, title, rawg_slug")
        .not_.is_("rawg_slug", "null")
        .is_("steam_app_id", "null")
        .order("title")
    )
    if offset:
        q = q.range(offset, offset + (limit or 10_000) - 1)
    elif limit:
        q = q.limit(limit)
    return q.execute().data


def _fix_steam_pass(api_key: str, db, limit: int, offset: int, dry_run: bool) -> None:
    """Second-pass: fill steam_app_id for games that already have rawg_slug."""
    games = _get_games_missing_steam(db if not dry_run else get_client(), limit=limit, offset=offset)
    total = len(games)
    print(f"[rawg_backfill --fix-steam] {total} game(s) have rawg_slug but no steam_app_id.")
    if dry_run:
        for g in games[:20]:
            print(f"  {g['title'][:50]}  rawg_slug={g['rawg_slug']}")
        if total > 20:
            print(f"  ... and {total - 20} more")
        return

    found = 0
    errors = 0
    for i, game in enumerate(games, 1):
        slug = game["rawg_slug"]
        title = game["title"]
        print(f"  [{i}/{total}] {title[:50]}", end="", flush=True)
        try:
            steam_app_id = get_steam_app_id(api_key, slug)
        except Exception as exc:
            print(f"  | ERROR: {exc}")
            errors += 1
            continue
        if steam_app_id:
            update_game_rawg_data(db, game["game_id"], {"steam_app_id": steam_app_id})
            print(f"  | steam_app_id={steam_app_id}")
            found += 1
        else:
            print(f"  | no steam ID")
        if i % 25 == 0:
            print(f"\n  --- progress: {i}/{total} | found={found} errors={errors} ---\n")

    print("\n" + "=" * 60)
    print(f"Fix-steam complete.\n  Processed: {total}\n  Steam IDs found: {found}\n  Errors: {errors}")
    print("=" * 60)


def _process_rawg_games(
    api_key: str,
    db,
    games: list[dict],
    dry_run: bool,
    state: dict | None = None,
    state_path: Path | None = None,
) -> dict:
    total = len(games)
    matched = 0
    steam_found = 0
    no_match = 0
    errors = 0

    for i, game in enumerate(games, 1):
        game_id = game["game_id"]
        title = game["title"]
        year = _release_year(game.get("release_date"))
        has_steam = bool(game.get("steam_app_id"))

        print(f"  [{i}/{total}] {title}", end="", flush=True)

        if dry_run:
            print(f"  | year={year or '?'}  steam_app_id={'already set' if has_steam else 'missing'}")
            continue

        # --- RAWG search ---
        try:
            result = search_game(api_key, title, year)
        except Exception as exc:
            print(f"  | ERROR during search: {exc}")
            errors += 1
            if state is not None:
                _record_state_attempt(state, game, "error", error=str(exc))
                _save_state(state_path, state)
            continue

        if not result or not result.get("rawg_slug"):
            print("  | no match")
            no_match += 1
            if state is not None:
                _record_state_attempt(state, game, "no_match")
                _save_state(state_path, state)
            continue

        slug = result["rawg_slug"]
        updates: dict = {"rawg_slug": slug}

        # --- Steam app ID via detail endpoint (skip if already set) ---
        steam_app_id: str | None = None
        if not has_steam:
            try:
                steam_app_id = get_steam_app_id(api_key, slug)
            except Exception as exc:
                print(f"  | WARNING: detail fetch failed: {exc}", end="")
            if steam_app_id:
                updates["steam_app_id"] = steam_app_id
                steam_found += 1

        update_game_rawg_data(db, game_id, updates)
        matched += 1

        if state is not None:
            _record_state_attempt(
                state,
                game,
                "matched",
                rawg_slug=slug,
                rawg_name=result.get("rawg_name"),
                match_score=result.get("match_score"),
                steam_app_id=steam_app_id,
            )
            _save_state(state_path, state)

        steam_note = f"  steam_app_id={steam_app_id}" if steam_app_id else ("  steam already set" if has_steam else "  no steam ID")
        score_note = f"  match_score={result['match_score']}" if result.get("match_score") is not None else ""
        print(f"  | slug={slug}{steam_note}{score_note}")

        if i % 25 == 0:
            pct = i / total * 100
            print(f"\n  --- progress: {i}/{total} ({pct:.0f}%) | matched={matched} no_match={no_match} errors={errors} ---\n")

    return {
        "total": total,
        "matched": matched,
        "steam_found": steam_found,
        "no_match": no_match,
        "errors": errors,
    }


def _chunked_rawg_pass(
    api_key: str,
    db,
    chunk_size: int,
    max_chunks: int,
    dry_run: bool,
    state_path: Path,
    retry_errors: bool,
) -> None:
    client = db if not dry_run else get_client()
    state = _load_state(state_path)
    missing_total = _count_games_missing_rawg(client)

    print(
        f"[rawg_backfill --chunk-size {chunk_size}] {missing_total} game(s) still missing rawg_slug. "
        f"state_file={state_path}"
    )
    if dry_run:
        print("[rawg_backfill] DRY-RUN - no DB writes or state updates will be made.\n")

    aggregate = {"total": 0, "matched": 0, "steam_found": 0, "no_match": 0, "errors": 0}
    attempted_this_run: set[str] = set()

    for chunk_number in range(1, max_chunks + 1):
        skip_ids = _state_skip_ids(state, retry_errors) | attempted_this_run
        games = _get_unattempted_games_missing_rawg(client, limit=chunk_size, skip_ids=skip_ids)
        if not games:
            print("No unattempted games remain in the missing-RAWG backlog.")
            break

        print(f"\n--- chunk {chunk_number}/{max_chunks}: processing {len(games)} game(s) ---\n")
        summary = _process_rawg_games(
            api_key=api_key,
            db=db,
            games=games,
            dry_run=dry_run,
            state=None if dry_run else state,
            state_path=None if dry_run else state_path,
        )

        attempted_this_run.update(str(game["game_id"]) for game in games)
        for key in aggregate:
            aggregate[key] += summary[key]

        if dry_run or len(games) < chunk_size:
            break

    print("\n" + "=" * 60)
    if dry_run:
        print(f"DRY-RUN complete. Would process {aggregate['total']} game(s) in chunk mode.")
    else:
        remaining = _count_games_missing_rawg(db)
        print(
            f"Chunked backfill complete.\n"
            f"  Total processed        : {aggregate['total']}\n"
            f"  RAWG matched           : {aggregate['matched']}\n"
            f"  Steam IDs found        : {aggregate['steam_found']}\n"
            f"  No RAWG match          : {aggregate['no_match']}\n"
            f"  Errors                 : {aggregate['errors']}\n"
            f"  Still missing rawg_slug: {remaining}"
        )
    print("=" * 60)


def _cleanup_unsafe_state_matches(db, state_path: Path, dry_run: bool) -> None:
    state = _load_state(state_path)
    unsafe_matches: list[tuple[str, dict]] = []

    for game_id, attempt in state.get("rawg_missing", {}).items():
        if attempt.get("status") != "matched" or not attempt.get("rawg_slug"):
            continue
        candidate_title = attempt.get("rawg_name") or attempt["rawg_slug"].replace("-", " ")
        score = _title_match_score(attempt["title"], candidate_title)
        if score <= 0:
            unsafe_matches.append((game_id, attempt))

    print(
        f"[rawg_backfill --cleanup-unsafe-state] {len(unsafe_matches)} unsafe matched row(s) "
        f"found in {state_path}"
    )

    if dry_run:
        for game_id, attempt in unsafe_matches[:50]:
            steam_note = f", steam_app_id={attempt['steam_app_id']}" if attempt.get("steam_app_id") else ""
            print(f"  {game_id}: {attempt['title']} -> {attempt['rawg_slug']}{steam_note}")
        if len(unsafe_matches) > 50:
            print(f"  ... and {len(unsafe_matches) - 50} more")
        print("DRY-RUN complete. No DB writes or state updates were made.")
        return

    reverted = 0
    for game_id, attempt in unsafe_matches:
        updates = {"rawg_slug": None}
        if attempt.get("steam_app_id"):
            updates["steam_app_id"] = None
        db.table("games").update(updates).eq("game_id", game_id).execute()
        del state["rawg_missing"][game_id]
        _save_state(state_path, state)
        reverted += 1

    print(f"Cleanup complete. Reverted {reverted} unsafe row(s).")


def main(
    dry_run: bool,
    limit: int,
    offset: int,
    fix_steam: bool,
    chunk_size: int,
    max_chunks: int,
    state_file: str,
    retry_errors: bool,
    cleanup_unsafe_state: bool,
) -> None:
    if cleanup_unsafe_state:
        if limit or offset or chunk_size or fix_steam:
            print("ERROR: --cleanup-unsafe-state cannot be combined with backfill selection flags.")
            sys.exit(2)
        db = None if dry_run else get_client()
        _cleanup_unsafe_state_matches(db, state_path=Path(state_file), dry_run=dry_run)
        return

    api_key = os.environ.get("RAWG_API_KEY", "")
    if not api_key and not dry_run:
        print("ERROR: RAWG_API_KEY not set in .env")
        sys.exit(1)

    db = None if dry_run else get_client()

    if chunk_size:
        if fix_steam:
            print("ERROR: --chunk-size is for the rawg_slug backfill pass, not --fix-steam.")
            sys.exit(2)
        if limit or offset:
            print("ERROR: --chunk-size cannot be combined with --limit or --offset.")
            sys.exit(2)
        if chunk_size < 1 or max_chunks < 1:
            print("ERROR: --chunk-size and --max-chunks must be positive integers.")
            sys.exit(2)

        _chunked_rawg_pass(
            api_key=api_key,
            db=db,
            chunk_size=chunk_size,
            max_chunks=max_chunks,
            dry_run=dry_run,
            state_path=Path(state_file),
            retry_errors=retry_errors,
        )
        return

    if fix_steam:
        _fix_steam_pass(api_key, db, limit=limit, offset=offset, dry_run=dry_run)
        return

    games = get_games_missing_rawg(db if not dry_run else get_client(), limit=limit, offset=offset)

    total = len(games)
    print(f"[rawg_backfill] {total} game(s) missing rawg_slug (limit={limit or 'none'}, offset={offset})")
    if dry_run:
        print("[rawg_backfill] DRY-RUN — no DB writes will be made.\n")

    summary = _process_rawg_games(api_key=api_key, db=db, games=games, dry_run=dry_run)

    print("\n" + "=" * 60)
    if dry_run:
        print(f"DRY-RUN complete. Would process {total} game(s).")
    else:
        print(
            f"Backfill complete.\n"
            f"  Total processed  : {total}\n"
            f"  RAWG matched     : {summary['matched']}\n"
            f"  Steam IDs found  : {summary['steam_found']}\n"
            f"  No RAWG match    : {summary['no_match']}\n"
            f"  Errors           : {summary['errors']}"
        )
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill rawg_slug and steam_app_id for games table.")
    parser.add_argument("--dry-run", action="store_true", help="Print plan; no DB writes.")
    parser.add_argument("--limit", type=int, default=0, metavar="N", help="Process at most N games.")
    parser.add_argument("--offset", type=int, default=0, metavar="N", help="Skip first N games (for resuming).")
    parser.add_argument("--chunk-size", type=int, default=0, metavar="N", help="Process a bounded stateful chunk of N games.")
    parser.add_argument("--max-chunks", type=int, default=1, metavar="N", help="Number of chunks to process in chunk mode.")
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE), help="State file used by chunk mode.")
    parser.add_argument("--retry-errors", action="store_true", help="Retry rows previously recorded as errors in chunk state.")
    parser.add_argument("--cleanup-unsafe-state", action="store_true", help="Revert unsafe matches recorded in the chunk state.")
    parser.add_argument("--fix-steam", action="store_true", help="Fix games that have rawg_slug but no steam_app_id.")
    args = parser.parse_args()
    main(
        dry_run=args.dry_run,
        limit=args.limit,
        offset=args.offset,
        fix_steam=args.fix_steam,
        chunk_size=args.chunk_size,
        max_chunks=args.max_chunks,
        state_file=args.state_file,
        retry_errors=args.retry_errors,
        cleanup_unsafe_state=args.cleanup_unsafe_state,
    )
