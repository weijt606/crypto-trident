"""Risk layer: independent of strategies, holds a veto over every order.

Design rules:
- Backtest, paper and live share the same RiskGuard. Time always comes from
  bar timestamps (never the system clock), so risk behavior exercised in a
  backtest is identical live. Every order path must go through check_order().
- Daily loss halt: for the rest of the UTC day, blocks any order that would
  increase absolute exposure (opening/adding a long or a short) or flip the
  position sign (long -> short or short -> long, even at equal size); a pure
  reduction that stays on the same side or ends flat is still allowed (selling
  part of a long, buying to cover a short). Resets automatically the next day.
- Max-drawdown kill: never resets automatically. The execution layer must
  flatten and stop, and wait for a human operator to revive it.
- Fail closed on bad numbers: non-finite or non-positive qty/price, non-finite
  equity/position, and opening orders while equity <= 0 are rejected. A
  non-finite equity mark is ignored (it never moves the peak and never clears
  an existing kill/halt).
- Live executors persist the breaker state (export_state/load_state) so a
  restart neither forgets the drawdown peak nor clears an active HALT/KILL.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.models import Order, Side


@dataclass
class RiskConfig:
    max_order_notional: float = 1000.0   # max notional per order (USDT)
    max_position_pct: float = 0.95       # max position as a fraction of equity
    daily_loss_pct: float = 0.03         # daily loss halt threshold
    max_drawdown_pct: float = 0.15       # max drawdown from peak -> kill
    # Order rate limit (guards against runaway bugs). Measured on order timestamps,
    # which are bar timestamps (time comes from market data, not the system clock):
    # at most N orders within any 60 s span of bar time. For 1m+ bars every order of
    # one bar shares a timestamp, so in practice this caps orders per bar.
    max_orders_per_minute: int = 10
    min_order_notional: float = 5.0      # reject orders below the exchange minimum
    allowed_symbols: list[str] | None = None  # symbol whitelist; None = unrestricted (tests)
    allow_short: bool = False            # allow shorting (perp only, opt-in; always False for spot)
    max_leverage: float = 1.0            # max position notional / equity

    def __post_init__(self) -> None:
        """Validate limits so a config typo (NaN, 0, 1.5 for 15%, ...) cannot silently
        disable a breaker. Raises ValueError."""
        def num(name: str) -> float:
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"risk.{name} must be a number, got {v!r}")
            v = float(v)
            if not math.isfinite(v):
                raise ValueError(f"risk.{name} must be finite, got {v!r}")
            setattr(self, name, v)
            return v

        if num("max_order_notional") <= 0:
            raise ValueError("risk.max_order_notional must be > 0")
        if not 0 < num("max_position_pct") <= 1:
            raise ValueError("risk.max_position_pct must be in (0, 1]")
        for name in ("daily_loss_pct", "max_drawdown_pct"):
            if not 0 < num(name) < 1:
                raise ValueError(f"risk.{name} must be in (0, 1) (a fraction, e.g. 0.15)")
        if num("min_order_notional") < 0:
            raise ValueError("risk.min_order_notional must be >= 0")
        if self.min_order_notional > self.max_order_notional:
            raise ValueError("risk.min_order_notional must not exceed max_order_notional")
        if num("max_leverage") < 1:
            raise ValueError("risk.max_leverage must be >= 1")
        rate = self.max_orders_per_minute
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) \
                or rate < 1 or int(rate) != rate:
            raise ValueError("risk.max_orders_per_minute must be a positive integer")
        self.max_orders_per_minute = int(rate)
        if self.allowed_symbols is not None:
            self.allowed_symbols = list(self.allowed_symbols)


def _finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""


def _utc_date(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


@dataclass
class RiskGuard:
    cfg: RiskConfig
    initial_equity: float

    peak_equity: float = field(init=False)
    day_start_equity: float = field(init=False)
    current_day: str = field(init=False, default="")
    halted_day: str = field(init=False, default="")   # UTC day currently halted
    killed: bool = field(init=False, default=False)
    kill_reason: str = field(init=False, default="")
    _order_ts: deque = field(init=False, default_factory=deque)
    # True after a manual revive: the next finite mark becomes the new peak.
    _rebase_peak: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        if not _finite(self.initial_equity):
            raise ValueError(f"initial_equity must be finite, got {self.initial_equity!r}")
        self.peak_equity = self.initial_equity
        self.day_start_equity = self.initial_equity

    # ---- persistence (live executors) ----

    def export_state(self) -> dict:
        """Breaker state to persist across restarts (JSON-serializable)."""
        return {
            "peak_equity": None if self._rebase_peak else self.peak_equity,
            "day_start_equity": self.day_start_equity,
            "current_day": self.current_day,
            "halted_day": self.halted_day,
            "killed": self.killed,
            "kill_reason": self.kill_reason,
        }

    def load_state(self, state: dict | None) -> None:
        """Restore export_state() output. Invalid numbers are ignored (defaults kept).
        peak_equity None means "re-baseline at the next mark" (written by a manual revive)."""
        if not state:
            return
        if "peak_equity" in state and state["peak_equity"] is None:
            self._rebase_peak = True
        elif _finite(state.get("peak_equity")):
            self.peak_equity = float(state["peak_equity"])
        if _finite(state.get("day_start_equity")):
            self.day_start_equity = float(state["day_start_equity"])
        self.current_day = str(state.get("current_day") or "")
        self.halted_day = str(state.get("halted_day") or "")
        self.killed = bool(state.get("killed", False))
        self.kill_reason = str(state.get("kill_reason") or "")
        if self.killed and not self.kill_reason:
            self.kill_reason = "restored kill state"

    # ---- equity mark: call at every bar close; returns triggered events ----

    def on_mark(self, ts: int, equity: float) -> list[str]:
        events: list[str] = []
        if not _finite(equity):
            # Never let a bad mark move the peak, reset the day or mask a breaker.
            return [f"WARN: non-finite equity mark {equity!r} ignored"]
        equity = float(equity)
        day = _utc_date(ts)
        if day != self.current_day:
            self.current_day = day
            self.day_start_equity = equity

        if self._rebase_peak:
            self.peak_equity = equity
            self._rebase_peak = False
        self.peak_equity = max(self.peak_equity, equity)

        if not self.killed and self.peak_equity > 0:
            drawdown = 1 - equity / self.peak_equity
            if drawdown >= self.cfg.max_drawdown_pct:
                self.killed = True
                self.kill_reason = f"drawdown {drawdown:.1%} >= {self.cfg.max_drawdown_pct:.0%}"
                events.append(f"KILL: {self.kill_reason}")

        if self.halted_day != day and self.day_start_equity > 0:
            daily_loss = 1 - equity / self.day_start_equity
            if daily_loss >= self.cfg.daily_loss_pct:
                self.halted_day = day
                events.append(
                    f"HALT: daily loss {daily_loss:.1%} >= {self.cfg.daily_loss_pct:.0%}, opening blocked today"
                )
        return events

    # ---- pre-trade order check ----

    def check_order(self, order: Order, price: float, account_equity: float,
                    position_qty: float) -> Verdict:
        cfg = self.cfg

        if self.killed:
            return Verdict(False, f"kill switch active, trading stopped: {self.kill_reason}")

        if not (_finite(order.qty) and order.qty > 0):
            return Verdict(False, f"invalid order qty {order.qty!r}")
        if not (_finite(price) and price > 0):
            return Verdict(False, f"invalid price {price!r}")
        if not _finite(account_equity):
            return Verdict(False, f"invalid account equity {account_equity!r}")
        if not _finite(position_qty):
            return Verdict(False, f"invalid position {position_qty!r}")
        notional = order.qty * price

        if cfg.allowed_symbols is not None and order.symbol not in cfg.allowed_symbols:
            return Verdict(False, f"{order.symbol} is not in the symbol whitelist")

        signed_after = position_qty + (order.qty if order.side == Side.BUY else -order.qty)
        increases_exposure = abs(signed_after) > abs(position_qty) * (1 + 1e-9) + 1e-12
        # A flip opens new exposure on the other side even if |after| <= |before|.
        flips_sign = signed_after * position_qty < 0
        opens_exposure = increases_exposure or flips_sign

        if opens_exposure and self.halted_day == _utc_date(order.ts):
            return Verdict(False, "daily loss halt active, opening blocked")

        if opens_exposure and account_equity <= 0:
            return Verdict(False, f"account equity {account_equity:.2f} <= 0, opening blocked")

        if notional < cfg.min_order_notional:
            return Verdict(False, f"below min order notional {cfg.min_order_notional} USDT")

        if notional > cfg.max_order_notional:
            return Verdict(False, f"order notional {notional:.2f} exceeds limit {cfg.max_order_notional} USDT")

        if not cfg.allow_short and order.side == Side.SELL and order.qty > position_qty * (1 + 1e-9):
            return Verdict(False, "short selling not allowed: sell qty exceeds position")

        # notional cap = equity x max_position_pct x max_leverage (spot defaults to 1x)
        exposure_after = abs(signed_after) * price
        limit_pct = cfg.max_position_pct * cfg.max_leverage
        # 1e-9 relative tolerance so an allocation exactly at the cap is not
        # rejected by float error; only checked when the order increases
        # exposure or flips the sign (reducing/closing is always allowed)
        if (
            opens_exposure
            and exposure_after / account_equity > limit_pct * (1 + 1e-9)
        ):
            return Verdict(
                False,
                f"position notional would reach {exposure_after / account_equity:.0%} of equity, exceeds limit {limit_pct:.0%}",
            )

        # rate limit (including this order), over a 60 s window of bar timestamps
        # (= orders per bar for 1m+ timeframes; see RiskConfig.max_orders_per_minute)
        window_start = order.ts - 60_000
        while self._order_ts and self._order_ts[0] < window_start:
            self._order_ts.popleft()
        if len(self._order_ts) + 1 > cfg.max_orders_per_minute:
            return Verdict(False, f"order rate limit exceeded {cfg.max_orders_per_minute} "
                                  "per 60 s of bar time (per bar for 1m+ bars)")

        self._order_ts.append(order.ts)
        return Verdict(True)

    def revive(self) -> None:
        """Manual revive: reset all breakers. Must only be invoked explicitly by a
        human operator; never call it automatically anywhere in code."""
        self.killed = False
        self.kill_reason = ""
        self.halted_day = ""
        self.peak_equity = self.initial_equity
