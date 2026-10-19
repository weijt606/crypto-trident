"""Retire a live perp bot safely: flatten -> persist final state -> write a .softcut halt marker.

Why not just stop the service: without a controlled shutdown the position stays open on
the exchange unmanaged and the local state freezes before the close (a ghost position).
This script reuses the executor's own _soft_cut path: the closing order goes through
_place in closing mode (exchange precision, reduce-only / positionSide-bound), fills are
booked, the exchange position is re-read to verify the account is flat (a residual raises
a CRITICAL alert), the final state is persisted, and .softcut makes the executor refuse
to restart. Like kill and soft cut, this close deliberately skips RiskGuard.check_order.

Order of operations (so two processes never manage the same position):
    1. Stop the running service, e.g. `systemctl --user disable --now trident-live-<name>`
    2. uv run python scripts/retire_live_perp.py --bot <name> --reason "..." --yes
The executor's single-instance lock (<live_dir>/<name>.lock) is taken here too: if the
service is still running, this script refuses with exit code 78 and sends nothing.
Without --yes this is a dry run: it reads positions and prints the planned action; no orders
are sent and no exchange setting (margin mode, leverage) is changed.

Retirement is a human decision; --reason records why.
"""
import _bootstrap  # noqa: F401
import argparse

from core.config import load_bot_config, load_system_config
from core.models import Bar
from data.fetcher import fetch_latest_closed_bar
from execution.halt import exit_on_halt
from execution.live_perp import LivePerpTrader
from run_live_perp import build_perp_trader


def retire(trader: LivePerpTrader, raw: list, reason: str) -> None:
    """Reconcile, then take the controlled _soft_cut shutdown. SystemExit is expected."""
    bar = Bar(ts=int(raw[0]), open=float(raw[1]), high=float(raw[2]), low=float(raw[3]),
              close=float(raw[4]), volume=float(raw[5]),
              symbol=trader.bot.symbol, timeframe=trader.bot.timeframe)
    # _soft_cut -> _flatten_and_persist re-reads the exchange position and reconciles
    # (detecting a baseline closed outside the bot) right before closing.
    try:
        trader._soft_cut(bar, f"retired: {reason}")
    except SystemExit:
        pass  # _soft_cut ends with SystemExit by design


def main() -> None:
    # Lock refusal and other deliberate halts exit with HALT_EXIT_CODE (78).
    exit_on_halt(_main)


def _main() -> None:
    p = argparse.ArgumentParser(description="Flatten and permanently halt a live perp bot.")
    p.add_argument("--bot", required=True)
    p.add_argument("--reason", required=True, help="why the bot is being retired (logged)")
    p.add_argument("--yes", action="store_true", help="execute for real; without it, dry run only")
    args = p.parse_args()

    sys_cfg = load_system_config()
    bot_cfg = load_bot_config(args.bot)
    if bot_cfg.get("market") != "perp":
        raise SystemExit(f"{args.bot} is not a perp bot - this script only retires live perp bots.")
    if (bot_cfg.get("stage") or "").lower() != "live":
        raise SystemExit(f"{args.bot} has stage={bot_cfg.get('stage')!r}, not 'live' - nothing to retire.")

    # Dry run: read-only startup, no margin-mode / leverage change on the exchange.
    trader = build_perp_trader(sys_cfg, bot_cfg, dry_run=not args.yes)

    raw = fetch_latest_closed_bar(trader.exchange, trader.bot.symbol, trader.bot.timeframe)
    if not raw:
        raise SystemExit("could not fetch the latest closed bar; aborting (time must come from market data).")

    exchange_pos = trader._perp_position()
    print(f"{args.bot}: local position {trader.portfolio.position_qty:+.6f}, "
          f"exchange position {exchange_pos:+.6f}, bar ts={raw[0]}")
    if not args.yes:
        print("[dry run] without --yes: would flatten the position above and write the .softcut marker. "
              "Re-run with --yes to execute.")
        return

    retire(trader, raw, args.reason)
    residual = trader._perp_position()
    if abs(residual) > 1e-9:
        raise SystemExit(f"FLATTEN INCOMPLETE: residual {residual:+.6f} on {trader.perp} "
                         "(beyond the baseline); close it manually on the exchange. "
                         f"Marker written to {trader.softcut_file.name}")
    print(f"retired: flat on {trader.perp} (baseline {trader._baseline_perp:+.6f} untouched); "
          f"final state persisted, marker written to {trader.softcut_file.name}")


if __name__ == "__main__":
    main()
