"""USDT-margined perpetual live executor (real money, long/short, leverage).

Requires a trade API key with futures permission. Promotion gate: the bot config must
have stage: live, which must be set by a human operator, never by automation.

Differences from run_live.py: only accepts market: perp bots, uses the futures order
path, and the risk guard adds allow_short plus a leverage cap (hard cap 3). Capital is
accounted per bot (capital + this bot's position PnL), isolated from any other
positions held in the same futures account.

Usage:
    uv run python scripts/run_live_perp.py --bot <name>
"""
import _bootstrap  # noqa: F401
import argparse
import sys

from core.config import load_bot_config, load_system_config
from execution.halt import TraderHalted, exit_on_halt
from execution.live import (MAX_DRAWDOWN_CEILING, clamp_drawdown,  # noqa: F401  re-exported
                            require_live_exchange, require_positive)
from execution.live_perp import HARD_MAX_LEVERAGE, LivePerpTrader
from safety.guard import RiskConfig
from strategies import build_strategy


def build_perp_trader(sys_cfg: dict, bot_cfg: dict, dry_run: bool = False) -> LivePerpTrader:
    """Build the perp executor from system + bot config (shared with retire_live_perp.py)."""
    require_live_exchange(sys_cfg.get("exchange", "binance"))
    name = bot_cfg.get("name", "perp bot")
    overlay = sys_cfg.get("risk_perp") or {}
    # Numeric config must be finite and > 0, otherwise TraderHalted (exit 78).
    max_leverage = min(require_positive(name, "risk_perp.max_leverage",
                                        overlay.get("max_leverage", HARD_MAX_LEVERAGE)),
                       float(HARD_MAX_LEVERAGE))
    risk_kwargs = dict(sys_cfg["risk"])
    risk_kwargs.update(allow_short=True, max_leverage=max_leverage)
    # Optional per-bot kill line. Still a hard limit, clamped so a config typo cannot
    # effectively disable the circuit breaker (NaN / <= 0 is rejected outright).
    if bot_cfg.get("max_drawdown_pct") is not None:
        risk_kwargs["max_drawdown_pct"] = clamp_drawdown(bot_cfg["max_drawdown_pct"])
    risk_kwargs["max_drawdown_pct"] = clamp_drawdown(risk_kwargs.get("max_drawdown_pct", 0.15))

    # Capital cap: bot-level cash_cap wins, otherwise system live.cash_cap. Both must be
    # finite and > 0 (otherwise TraderHalted, exit 78).
    raw_cap = bot_cfg.get("cash_cap")
    if raw_cap is None:
        raw_cap = (sys_cfg.get("live") or {}).get("cash_cap")
    capital = require_positive(name, "cash_cap", raw_cap)
    # Exchange leverage: clamped to max_leverage (itself capped at the hard limit);
    # the executor clamps again.
    raw_lev = require_positive(name, "params.lev", (bot_cfg.get("params") or {}).get("lev", 2))
    leverage = max(1, int(min(raw_lev, max_leverage)))

    return LivePerpTrader(
        bot=build_strategy(bot_cfg),
        capital=capital,
        fee_bps=sys_cfg["fees"].get("perp_taker_bps", 5),
        slippage_bps=sys_cfg["slippage_bps"],
        risk=RiskConfig(**risk_kwargs),
        max_leverage=max_leverage,
        leverage=leverage,
        # Optional soft cut: an earlier "pull and re-tune" stop than the hard kill.
        soft_cut_pct=bot_cfg.get("soft_cut_pct"),
        max_consec_losses=bot_cfg.get("max_consec_losses"),
        stage=bot_cfg.get("stage"),
        dry_run=dry_run,
    )


def _refuse(msg: str):
    def run() -> None:
        raise TraderHalted(msg)
    return run


def main() -> None:
    p = argparse.ArgumentParser(description="Run a perpetual-futures bot with real money.")
    p.add_argument("--bot", required=True, help="config name under config/bots/")
    args = p.parse_args()

    sys_cfg = load_system_config()
    bot_cfg = load_bot_config(args.bot)

    if bot_cfg.get("market") != "perp":
        raise SystemExit(
            f"{args.bot} is not a perp bot - the perp executor only accepts market: perp. "
            "Use scripts/run_live.py for spot bots."
        )

    stage = (bot_cfg.get("stage") or "").lower()
    if stage != "live":
        # Deliberate refusal: exit 78 (halted) so a service manager does not restart-loop.
        exit_on_halt(_refuse(
            f"{args.bot} has stage={stage!r}, not 'live' - refusing to trade real money.\n"
            "A human operator must set stage: live in the bot config to approve live trading."
        ))

    print("WARNING: LIVE TRADING WITH REAL FUNDS. Research/personal project, not investment "
          "advice, no warranty - see DISCLAIMER.md. You are responsible for every order.",
          file=sys.stderr)

    # Deliberate halts (kill, soft cut, failed safety checks) exit with HALT_EXIT_CODE.
    exit_on_halt(lambda: build_perp_trader(sys_cfg, bot_cfg).run_forever())


if __name__ == "__main__":
    main()
