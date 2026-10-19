"""Spot live executor (real money; requires a trade API key in .env).

Promotion gate: the bot config must have stage: live. Setting stage to live is the
human approval step for real-money trading; it must be changed by a human operator,
never by automation.

Usage:
    uv run python scripts/run_live.py --bot sma-demo
"""
import _bootstrap  # noqa: F401
import argparse
import sys

from core.config import load_bot_config, load_system_config
from execution.halt import TraderHalted, exit_on_halt
from execution.live import LiveTrader, clamp_drawdown
from safety.guard import RiskConfig
from strategies import build_strategy


def _refuse(msg: str):
    def run() -> None:
        raise TraderHalted(msg)
    return run


def main() -> None:
    p = argparse.ArgumentParser(description="Run a spot bot with real money.")
    p.add_argument("--bot", required=True, help="config name under config/bots/")
    args = p.parse_args()

    sys_cfg = load_system_config()
    bot_cfg = load_bot_config(args.bot)

    if bot_cfg.get("market", "spot") != "spot":
        raise SystemExit(
            f"{args.bot} is not a spot bot. The spot executor only supports spot orders; "
            "use scripts/run_live_perp.py for market: perp."
        )

    stage = (bot_cfg.get("stage") or "").lower()
    if stage != "live":
        # Deliberate refusal: exit 78 (halted) so a service manager does not restart-loop.
        exit_on_halt(_refuse(
            f"{args.bot} has stage={stage!r}, not 'live' - refusing to trade real money.\n"
            "Promote only after paper trading, an out-of-sample backtest you have reviewed and a passing "
            "circuit-breaker drill; a human operator must set stage: live in the bot config."
        ))

    print("WARNING: LIVE TRADING WITH REAL FUNDS. Research/personal project, not investment "
          "advice, no warranty - see DISCLAIMER.md. You are responsible for every order.",
          file=sys.stderr)

    def run() -> None:
        risk_kwargs = dict(sys_cfg["risk"])
        # Same ceiling as the perp executor: a config typo can never loosen the kill line
        # past MAX_DRAWDOWN_CEILING (non-numeric / NaN / <= 0 halts with exit 78).
        risk_kwargs["max_drawdown_pct"] = clamp_drawdown(
            risk_kwargs.get("max_drawdown_pct", 0.15))
        LiveTrader(
            bot=build_strategy(bot_cfg),
            initial_cash=sys_cfg["live"]["initial_cash"],
            risk=RiskConfig(**risk_kwargs),
            cash_cap=sys_cfg["live"].get("cash_cap"),
            stage=stage,
            # Only used to estimate a fee the exchange response did not report.
            fee_bps=(sys_cfg.get("fees") or {}).get("taker_bps", 10),
        ).run_forever()

    # Deliberate halts (kill, failed safety checks) exit with HALT_EXIT_CODE.
    exit_on_halt(run)


if __name__ == "__main__":
    main()
