"""Paper trading driven by live market data (no API key needed; Ctrl-C to stop; resumable).

Usage:
    uv run python scripts/run_paper.py --bot sma-demo
"""
import _bootstrap  # noqa: F401
import argparse

from core.config import load_bot_config, load_system_config
from execution.halt import exit_on_halt
from execution.paper import PaperTrader
from safety.guard import RiskConfig
from strategies import build_strategy


def main() -> None:
    p = argparse.ArgumentParser(description="Run a bot in paper trading mode.")
    p.add_argument("--bot", required=True, help="config name under config/bots/")
    args = p.parse_args()

    sys_cfg = load_system_config()
    bot_cfg = load_bot_config(args.bot)

    # Perp overlay: allow shorts + leverage cap (hard cap 3), same as run_backtest.
    market = bot_cfg.get("market", "spot")
    risk_kwargs = dict(sys_cfg["risk"])
    fee_bps = sys_cfg["fees"]["taker_bps"]
    max_leverage = 1.0
    if market == "perp":
        overlay = sys_cfg.get("risk_perp", {})
        max_leverage = min(float(overlay.get("max_leverage", 3)), 3.0)
        risk_kwargs.update(allow_short=True, max_leverage=max_leverage)
        fee_bps = sys_cfg["fees"].get("perp_taker_bps", 5)

    trader = PaperTrader(
        bot=build_strategy(bot_cfg),
        initial_cash=sys_cfg["paper"]["initial_cash"],
        fee_bps=fee_bps,
        slippage_bps=sys_cfg["slippage_bps"],
        risk=RiskConfig(**risk_kwargs),
        exchange_id=sys_cfg["exchange"],
        market=market,
        max_leverage=max_leverage,
    )
    # Deliberate halts (kill, liquidation) exit with HALT_EXIT_CODE.
    exit_on_halt(trader.run_forever)


if __name__ == "__main__":
    main()
