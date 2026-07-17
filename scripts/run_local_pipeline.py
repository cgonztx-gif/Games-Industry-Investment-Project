"""
Local pipeline runner: executes the four weekly phases sequentially, each
with unbuffered output captured to its own log file under logs/, continuing
to the next phase even if one fails (mirroring weekly.yml's !cancelled()
chain semantics). A runner log records phase start/end times and exit codes.

Usage:
    python scripts/run_local_pipeline.py            # all four phases
    python scripts/run_local_pipeline.py news       # a subset, in order

Logs land in logs/local-run-<UTC date>-<phase>.log; follow one live with:
    Get-Content logs\\local-run-<date>-<phase>.log -Wait -Tail 50
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PHASES = ["collect", "news", "sentiment", "synthesize"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    phases = sys.argv[1:] or _PHASES
    invalid = [p for p in phases if p not in _PHASES]
    if invalid:
        print(f"unknown phase(s): {invalid}; choose from {_PHASES}")
        return 2

    logs_dir = _ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)
    run_name = f"local-run-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    runner_log = logs_dir / f"{run_name}-runner.log"

    def note(message: str) -> None:
        line = f"{message}\n"
        with runner_log.open("a", encoding="utf-8") as f:
            f.write(line)
        print(line, end="", flush=True)

    worst_rc = 0
    for phase in phases:
        phase_log = logs_dir / f"{run_name}-{phase}.log"
        note(f"=== phase {phase} start: {_utc_now()} (log: {phase_log.name}) ===")
        with phase_log.open("w", encoding="utf-8") as out:
            rc = subprocess.call(
                [sys.executable, "-u", str(_ROOT / "run_weekly.py"), "--phase", phase],
                stdout=out,
                stderr=subprocess.STDOUT,
                cwd=_ROOT,
            )
        note(f"=== phase {phase} exit={rc} end: {_utc_now()} ===")
        worst_rc = worst_rc or rc  # keep going regardless; report first failure

    note(f"=== all phases done: {_utc_now()} (worst exit={worst_rc}) ===")
    return worst_rc


if __name__ == "__main__":
    sys.exit(main())
