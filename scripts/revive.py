"""Manually revive a live bot halted by the circuit breaker.

Design rule: revive is only ever invoked by a human operator, never by automation.
You must state what you reviewed; the text is written to the event log as an audit trail.

What it does: removes the .kill marker and resets the persisted breaker state in
<bot>_state.json (killed/halt cleared; the drawdown peak and the daily-loss start equity are set to
"re-baseline at the next mark", so the executor does not measure drawdown or the daily
loss against pre-halt values and trip again immediately). Works for both the spot and the perp state formats, including
older perp states with a top-level peak_equity.

Stop the executor before reviving (the single-instance lock is checked).

Usage:
    uv run python scripts/revive.py --bot sma-demo --reviewed "drawdown caused by X; risk config verified, size reduced"
"""
import _bootstrap  # noqa: F401
import argparse
import json
import time
from pathlib import Path

from core.config import storage_dir, validate_bot_name
from data.store import TradeLog
from execution.common import InstanceLock, atomic_write_json
from execution.halt import TraderHalted


def revive_bot(live_dir: Path, bot: str, reviewed: str) -> str:
    """Clear the kill marker and persisted breaker state. Returns the old halt info.
    Raises SystemExit with a clear message if the bot is not halted or still running."""
    if len(reviewed.strip()) < 10:
        raise SystemExit("--reviewed is too short: state what you checked and why reviving is safe")
    try:
        validate_bot_name(bot)
    except ValueError as e:
        raise SystemExit(str(e)) from e
    live_dir = Path(live_dir)
    kill_file = live_dir / f"{bot}.kill"
    state_file = live_dir / f"{bot}_state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else None
    guard = (state or {}).get("guard") or {}
    if not kill_file.exists() and not guard.get("killed"):
        raise SystemExit(f"{bot} is not halted ({kill_file} does not exist)")

    try:
        lock = InstanceLock(live_dir, bot)
    except TraderHalted as e:
        raise SystemExit(f"refusing to revive while the executor runs: {e.message}") from e
    try:
        info = kill_file.read_text().strip() if kill_file.exists() else "kill recorded in state"
        if state is not None:
            old_peak = guard.get("peak_equity", state.get("peak_equity"))
            # day_start_equity / current_day cleared too: the next mark re-baselines the
            # daily-loss reference, so a same-day revive does not re-trip HALT at once.
            guard.update(killed=False, kill_reason="", halted_day="", peak_equity=None,
                         day_start_equity=None, current_day="")
            state["guard"] = guard
            state.pop("peak_equity", None)  # legacy perp field; superseded by guard
            atomic_write_json(state_file, state)
            print(f"  breaker state reset (peak {old_peak} and day start -> re-baseline at next mark)")
        if kill_file.exists():
            kill_file.unlink()
    finally:
        lock.close()
    TradeLog(live_dir / "live.sqlite").record_event(
        int(time.time() * 1000), bot, "revive",
        f"manual revive. halt: {info}; review: {reviewed.strip()}",
    )
    return info


def main() -> None:
    p = argparse.ArgumentParser(description="Revive a live bot halted by the circuit breaker.")
    p.add_argument("--bot", required=True)
    p.add_argument("--reviewed", required=True,
                   help="your conclusion about the halt cause (at least 10 characters, logged)")
    args = p.parse_args()
    info = revive_bot(storage_dir() / "live", args.bot, args.reviewed)
    print(f"{args.bot} revived (halt was: {info}). Restart its live executor to resume.")


if __name__ == "__main__":
    main()
