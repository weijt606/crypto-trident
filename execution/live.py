"""Spot live executor.

Shares RiskGuard, market polling, the strategy interface and no-lookahead sentiment
injection with the paper trader. The difference: orders go to the exchange through
ccxt, and the exchange is the source of truth for the account.

Execution-layer safeguards (on top of RiskGuard):
- Key permission self-check at startup: withdrawals, universal/internal transfer,
  margin borrowing and options off, spot trading on; any violation refuses to start.
  Missing IP whitelist is a warning. Repeated startup auth failures (see
  STARTUP_AUTH_FAIL_LIMIT) halt with exit 78 instead of restart-looping.
- Single instance: an exclusive flock on <live_dir>/<name>.lock; a second process for
  the same bot refuses to start (exit 78).
- Persistent breakers: RiskGuard state (drawdown peak, daily HALT, KILL) is saved in the
  state file and restored on restart. A kill also writes <live_dir>/<name>.kill and
  restarts are refused; revival is manual only (scripts/revive.py), never automated.
- Each bar is decided at most once: last_bar_ts is persisted before the first order of
  a bar, so orders are never re-sent for a decided bar; every order carries a
  deterministic client order id (<bot>-<bar_ts>-<n>) and a timed-out send is resolved
  by client-id lookup.
- Fills are taken from the exchange: an async/empty ack is re-read with fetch_order.
- State files are written atomically (tmp + fsync + rename).
- Per-bar reconciliation: expected position vs exchange; on mismatch, alert and adopt
  the exchange value.
- Baseline subtraction: pre-existing balance of the base asset (dust etc.) recorded at
  first start is excluded; strategy position = current - baseline.
- Base-asset fees: a buy whose fee is charged in the base coin books filled - fee. Every
  sell is capped at the free base balance minus the baseline (rounded down to
  precision); a leftover below min_order_notional is dust, treated as flat.
- Live fills are stored in a separate database (storage/live/live.sqlite).
- Robust error handling: auth errors (-2015/-2014/-1022) raise a single actionable
  alert; timestamp errors (-1021) trigger a clock re-sync before the next retry.
  Exchange error text is redacted (query strings / signatures) before logging/alerts.
- Halts that a restart cannot fix (kill, soft cut, failed startup safety checks) raise
  TraderHalted; the run_live*.py scripts turn it into exit code HALT_EXIT_CODE so a
  service manager can be told not to restart (systemd RestartPreventExitStatus).
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import ccxt

from core.config import storage_dir, trade_keys
from core.models import AccountView, Bar, Fill, Order, Side
from core.netinfo import _outbound_ip, mask_ip
from core.polling import next_sleep
from data.fetcher import fetch_latest_closed_bar
from monitor.heartbeat import beat
from execution.halt import HALT_EXIT_CODE, TraderHalted  # noqa: F401  re-exported
from execution.common import (STARTUP_AUTH_FAIL_LIMIT, InstanceLock, atomic_write_json,
                              atomic_write_text, client_order_id, find_order_by_client_id,
                              key_restriction_problems, record_startup_auth_failure,
                              redact, reset_startup_auth_failures, resolve_fill)
from execution.warmup import warmup_strategy
from data.store import TradeLog
from monitor.alerts import send_alert
from monitor.logger import get_logger
from strategies.base import Strategy
from safety.guard import RiskConfig, RiskGuard
from sentiment.composite import as_of
from sentiment.store import load_composite

log = get_logger("live")

_AUTH_ERROR_MARKERS = ("-2015", "-2014", "Invalid API-key", "-1022")

BOOKED_IDS_KEEP = 200  # recent exchange order ids kept in state to refuse double booking
MAX_DRAWDOWN_CEILING = 0.30  # live kill line may be tuned, but never loosened past this


def clamp_drawdown(value) -> float:
    """Live max_drawdown_pct: must be a finite fraction > 0; clamped to the ceiling.
    An invalid value (non-numeric, NaN, <= 0) is a config error a restart cannot fix,
    so it halts (TraderHalted, exit 78) with a clear message."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = float("nan")
    if not math.isfinite(v) or v <= 0:
        raise TraderHalted(f"max_drawdown_pct must be a finite fraction > 0, got {value!r}; "
                           "fix the risk config")
    return min(v, MAX_DRAWDOWN_CEILING)


def require_positive(name: str, field: str, value, *, below_one: bool = False) -> float:
    """Numeric live config value: must be finite and > 0 (and < 1 when below_one).
    Anything else (missing, non-numeric, NaN, inf, <= 0) is a config error a restart
    cannot fix, so it halts (TraderHalted, exit 78) with a clear message."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = float("nan")
    if isinstance(value, bool) or not math.isfinite(v) or v <= 0 or (below_one and v >= 1):
        rng = "a fraction in (0, 1)" if below_one else "a finite number > 0"
        raise TraderHalted(f"{name}: {field} must be {rng}, got {value!r}; fix the config")
    return v


def require_live_setup(name: str, stage: str | None, risk: RiskConfig) -> None:
    """Startup gate shared by the live executors: stage must be 'live' (set by a human)
    and the risk config must carry a non-empty symbol whitelist. The drawdown kill line
    is clamped to MAX_DRAWDOWN_CEILING."""
    if (stage or "").lower() != "live":
        raise TraderHalted(
            f"{name} has stage={stage!r}, not 'live' - refusing to trade real money. "
            "A human operator must set stage: live in the bot config.")
    if not risk.allowed_symbols:
        raise TraderHalted(
            f"{name}: risk.allowed_symbols is missing or empty - live trading requires an "
            "explicit symbol whitelist in config/system.yaml.")
    if risk.max_drawdown_pct > MAX_DRAWDOWN_CEILING:
        log.warning("%s: max_drawdown_pct %.2f clamped to %.2f", name,
                    risk.max_drawdown_pct, MAX_DRAWDOWN_CEILING)
        risk.max_drawdown_pct = MAX_DRAWDOWN_CEILING


def startup_auth_failed(live_dir: Path, name: str, exc: BaseException, prefix: str):
    """Handle an auth failure during the startup key check: alert (throttled), count
    consecutive failures, and return the exception to raise: an ordinary SystemExit
    (restartable) until STARTUP_AUTH_FAIL_LIMIT is reached, then TraderHalted (exit 78)."""
    full, masked = auth_fail_detail(prefix)
    log.error("[self-check] %s", full)
    send_startup_auth_alert(live_dir, name, f"{name}: {masked}")
    n = record_startup_auth_failure(live_dir, name)
    if n >= STARTUP_AUTH_FAIL_LIMIT:
        return TraderHalted(
            f"{name}: {n} consecutive startup auth failures ({masked}); halting instead of "
            "restart-looping. Fix the key / IP whitelist, then start the executor manually.")
    return SystemExit(f"Live startup self-check failed: {full}")


def auth_fail_detail(prefix: str) -> tuple[str, str]:
    """(full detail for the local log, detail with a masked IP for alerts/events)."""
    ip = _outbound_ip()
    tmpl = f"{prefix} (check API key / IP whitelist; current outbound IP {{}})"
    return tmpl.format(ip), tmpl.format(mask_ip(ip))


AUTH_ALERT_MIN_INTERVAL_S = 3600


def send_startup_auth_alert(live_dir: Path, name: str, message: str,
                            min_interval_s: float = AUTH_ALERT_MIN_INTERVAL_S) -> bool:
    """Send a startup auth-failure alert at most once per min_interval_s per bot.

    A startup auth failure exits with an ordinary error so the service manager keeps
    restarting the executor; without this every restart would re-send the same alert.
    The last-sent time lives in <live_dir>/<name>.auth_alert. Wall-clock time is fine
    here: this only throttles notifications, it never affects trading decisions.
    Returns True if the alert was sent."""
    marker = Path(live_dir) / f"{name}.auth_alert"
    now = time.time()
    try:
        last = float(marker.read_text().strip())
    except (OSError, ValueError):
        last = 0.0
    if not math.isfinite(last):
        last = 0.0
    # Small negative gaps (clock adjustments) still count as "recent".
    if last > 0 and -60 < now - last < min_interval_s:
        log.info("Startup auth alert suppressed (last sent %.0fs ago)", now - last)
        return False
    send_alert(message)
    try:
        atomic_write_text(marker, f"{now:.3f}\n")
    except OSError as e:
        log.warning("Could not record auth alert time: %s", e)
    return True


def is_auth_error(exc: BaseException) -> bool:
    """Binance auth/permission/IP-whitelist rejection (-2015/-2014/-1022)."""
    msg = str(exc)
    return any(m in msg for m in _AUTH_ERROR_MARKERS)


def resync_clock_if_needed(exchange, exc: BaseException) -> bool:
    """On -1021 (timestamp outside recvWindow, e.g. local clock drift),
    refresh ccxt's cached server time offset so the next retry is signed correctly.
    Returns True if a re-sync was attempted."""
    if "-1021" not in str(exc):
        return False
    loader = getattr(exchange, "load_time_difference", None)
    if loader is None:
        return False
    try:
        loader()
        log.info("Clock re-synced with exchange after -1021")
    except Exception as e:  # noqa: BLE001
        log.warning("Clock re-sync failed: %s", e)
    return True


class LiveTrader:
    def __init__(
        self,
        bot: Strategy,
        initial_cash: float,
        risk: RiskConfig,
        exchange: ccxt.Exchange | None = None,
        live_dir: Path | None = None,
        trade_log: TradeLog | None = None,
        cash_cap: float | None = None,
        stage: str | None = None,
        fee_bps: float = 10.0,
    ):
        require_live_setup(bot.name, stage, risk)
        initial_cash = require_positive(bot.name, "initial_cash", initial_cash)
        if cash_cap is not None:
            cash_cap = require_positive(bot.name, "cash_cap", cash_cap)
        self.bot = bot
        # Only used to estimate a fee the exchange did not report (see _resolve_fees).
        self.fee_bps = fee_bps
        # Hard capital cap: free quote above this is invisible to the strategy.
        self.cash_cap = cash_cap
        self.base, self.quote = bot.symbol.split("/")
        self.live_dir = Path(live_dir or (storage_dir() / "live"))
        self.live_dir.mkdir(parents=True, exist_ok=True)
        self._lock = InstanceLock(self.live_dir, bot.name)
        try:
            self._init(bot, initial_cash, risk, exchange, trade_log)
        except BaseException:
            self.close()
            raise

    def _init(self, bot, initial_cash, risk, exchange, trade_log) -> None:
        if self.kill_file.exists():
            info = self.kill_file.read_text().strip()
            raise TraderHalted(
                f"{bot.name} is halted by the kill switch ({info}). "
                "Investigate, then revive manually with scripts/revive.py"
            )

        if exchange is None:
            key, secret = trade_keys()  # trade key is loaded from .env only
            exchange = ccxt.binance({"apiKey": key, "secret": secret, "enableRateLimit": True})
        self.exchange = exchange
        self._assert_key_safety()
        self.exchange.load_markets()

        self.trade_log = trade_log or TradeLog(self.live_dir / "live.sqlite")
        self.guard = RiskGuard(risk, initial_cash)
        self.state = self._load_state()
        # Breakers survive restarts: peak, daily HALT and KILL come back from the state file.
        self.guard.load_state(self.state.get("guard"))
        if self.guard.killed:
            raise TraderHalted(
                f"{bot.name}: saved state records an active kill ({self.guard.kill_reason}). "
                "Investigate, then revive manually with scripts/revive.py")
        self.last_bar_ts: int | None = self.state.get("last_bar_ts")

    def close(self) -> None:
        """Release the single-instance lock (also released on process exit)."""
        lock = getattr(self, "_lock", None)
        if lock is not None:
            lock.close()

    # ---- persistence ----

    @property
    def kill_file(self) -> Path:
        return self.live_dir / f"{self.bot.name}.kill"

    @property
    def state_file(self) -> Path:
        return self.live_dir / f"{self.bot.name}_state.json"

    def _load_state(self) -> dict:
        if self.state_file.exists():
            state = json.loads(self.state_file.read_text())
            log.info("Restored live state: %s (strategy indicators rebuilt by warmup)", state)
            return state
        # First start: record the existing base-asset balance as baseline; strategy starts flat.
        bal = self.exchange.fetch_balance()
        state = {
            "baseline_base": float(bal["total"].get(self.base) or 0.0),
            "position_qty": 0.0,
            "avg_cost": 0.0,
            "realized_pnl": 0.0,
            "fees_paid": 0.0,
            "last_bar_ts": None,
        }
        self._save_state(state)
        log.info("First start, recorded %s baseline %.8f", self.base, state["baseline_base"])
        return state

    def _save_state(self, state: dict | None = None) -> None:
        st = state if state is not None else self.state
        st["guard"] = self.guard.export_state()
        atomic_write_json(self.state_file, st)

    # ---- safety self-check ----

    def _assert_key_safety(self) -> None:
        try:
            r = self.exchange.sapi_get_account_apirestrictions()
        except Exception as e:  # noqa: BLE001
            # Startup auth failure (commonly the outbound IP is not whitelisted). Turn it
            # into an actionable alert that includes the current outbound IP instead of
            # crash-looping silently under a service manager.
            if is_auth_error(e):
                raise startup_auth_failed(self.live_dir, self.bot.name, e,
                                          "live startup auth failed") from e
            raise
        reset_startup_auth_failures(self.live_dir, self.bot.name)

        def b(field: str) -> bool:
            return str(r.get(field)).lower() == "true"

        # Hard requirements: with withdrawals and transfers disabled, a leaked key cannot
        # move funds out of the account (same rules as scripts/verify_trade_key.py).
        problems = key_restriction_problems(r, "spot")
        if problems:
            raise TraderHalted("Live startup self-check failed: " + "; ".join(problems))
        # IP whitelist is recommended but not required; warn only.
        if not b("ipRestrict"):
            msg = ("key has no IP whitelist; funds are isolated by disabled withdrawals/"
                   "transfers, keep .env secrets safe")
            log.warning("[self-check] %s", msg)
            send_alert(f"{self.bot.name} live: {msg}")

    # ---- account ----

    def _account_raw(self) -> tuple[float, float]:
        """Return (strategy position = exchange balance - baseline, free quote).
        The exchange is the source of truth; free quote is clamped to cash_cap."""
        bal = self.exchange.fetch_balance()
        base_total = float(bal["total"].get(self.base) or 0.0)
        free_quote = float(bal["free"].get(self.quote) or 0.0)
        if not (math.isfinite(base_total) and math.isfinite(free_quote)):
            # Never let a bad balance read reach the books or the state file.
            raise ValueError(f"non-finite balance from exchange ({self.base}={base_total!r}, "
                             f"{self.quote}={free_quote!r})")
        if self.cash_cap is not None:
            free_quote = min(free_quote, self.cash_cap)
        return max(0.0, base_total - self.state["baseline_base"]), free_quote

    def _reconcile(self, ts: int, exchange_qty: float, price: float | None = None) -> float:
        """Adopt the exchange position on mismatch. The difference is booked at `price`
        (the bar close): an increase is re-averaged into avg_cost, a decrease realizes
        PnL against avg_cost, so a later sell never books its full proceeds as profit."""
        st = self.state
        expected = st["position_qty"]
        tol = max(1e-8, abs(expected) * 0.005)
        if (abs(exchange_qty - expected) > tol and self._is_dust(exchange_qty, price)
                and self._is_dust(expected, price)):
            # Both sides are unsellable dust (e.g. precision leftovers after a full exit):
            # fold the exchange leftover into the baseline; the strategy is flat.
            st["baseline_base"] = float(st["baseline_base"]) + exchange_qty - expected
            st["position_qty"] = expected
            self._fold_dust(ts, price)
            self._save_state()
            return st["position_qty"]
        if abs(exchange_qty - expected) > tol:
            msg = (f"reconcile mismatch: expected {expected:.8f} {self.base}, "
                   f"exchange {exchange_qty:.8f}; adopting exchange value")
            log.warning("[reconcile] %s", msg)
            self.trade_log.record_event(ts, self.bot.name, "reconcile", msg)
            send_alert(f"{self.bot.name}: {msg}")
            if price is not None and math.isfinite(price) and price > 0:
                if exchange_qty > expected:
                    st["avg_cost"] = ((st["avg_cost"] * expected
                                       + (exchange_qty - expected) * price) / exchange_qty)
                elif st["avg_cost"] > 0:
                    st["realized_pnl"] += (price - st["avg_cost"]) * (expected - exchange_qty)
            st["position_qty"] = exchange_qty
            if exchange_qty < 1e-12:
                st["position_qty"], st["avg_cost"] = 0.0, 0.0
            self._save_state()
        return st["position_qty"]

    # ---- order placement ----

    def _fee_entries(self, resp: dict, symbol: str, filled: float, avg: float) -> list[dict]:
        """Fee entries ({"cost", "currency"}) for an order, as reported by the exchange.

        Order responses (and fetch_order re-reads) often omit trades/fees. Then the
        order's trades are fetched (fetch_my_trades by order id) when the exchange
        supports it; failing that the fee is estimated from fee_bps in the quote asset
        and logged (the next bar's reconciliation corrects any base-asset difference)."""
        def collect(src: dict) -> list[dict]:
            out = []
            for f in list(src.get("fees") or []) + ([src["fee"]] if src.get("fee") else []):
                if isinstance(f, dict) and f.get("cost") is not None:
                    out.append(f)
            return out

        entries: list[dict] = []
        for t in resp.get("trades") or []:
            if isinstance(t, dict):
                entries += collect(t)
        if not entries:
            entries = collect(resp)
        if entries:
            return entries
        oid = resp.get("id")
        fetch_trades = getattr(self.exchange, "fetch_my_trades", None)
        if oid and callable(fetch_trades):
            try:
                trades = fetch_trades(symbol, params={"orderId": oid}) or []
                for t in trades:
                    if isinstance(t, dict) and str(t.get("order") or oid) == str(oid):
                        entries += collect(t)
            except Exception as e:  # noqa: BLE001
                log.warning("Fee lookup via trades failed for order %s: %s", oid, redact(e))
            if entries:
                return entries
        est = filled * avg * self.fee_bps / 10_000
        log.warning("No fee info for order %s; estimating %.6f %s from fee_bps=%s",
                    oid, est, self.quote, self.fee_bps)
        return [{"cost": est, "currency": self.quote}]

    def _fee_to_quote(self, entries: list | None, avg_price: float) -> tuple[float, float]:
        """(total fee in quote, part of it charged in the base asset, in base units)."""
        fee = 0.0
        base_fee = 0.0
        for f in entries or []:
            if "fee" in f and isinstance(f.get("fee"), dict):  # trade dicts are accepted too
                f = f["fee"]
            try:
                cost = float(f.get("cost") or 0.0)
            except (TypeError, ValueError):
                cost = float("nan")
            cur = f.get("currency")
            if not math.isfinite(cost):
                log.warning("Non-finite fee %r %s ignored", f.get("cost"), cur)
                continue
            if not cost:
                continue
            if cur == self.quote:
                fee += cost
            elif cur == self.base:
                base_fee += cost
                fee += cost * avg_price
            else:  # fee paid in another asset (e.g. BNB): convert at the current price
                try:
                    px = float(self.exchange.fetch_ticker(f"{cur}/{self.quote}")["last"])
                    if not math.isfinite(px):
                        raise ValueError(f"non-finite price {px!r}")
                    fee += cost * px
                except Exception:
                    log.warning("Could not convert fee currency %s; booking 0 "
                                "(profit slightly overstated)", cur)
        return fee, base_fee

    def _free_sellable(self, symbol: str) -> float:
        """Free base balance minus the baseline, rounded down to exchange precision.
        Selling more than this would be rejected for insufficient balance (e.g. after a
        buy whose fee was charged in the base asset)."""
        bal = self.exchange.fetch_balance()
        free = float((bal.get("free") or {}).get(self.base) or 0.0)
        avail = free - float(self.state["baseline_base"])
        if not math.isfinite(avail):
            raise ValueError(f"non-finite free {self.base} balance {free!r}")
        if avail <= 0:
            return 0.0
        limit = avail * (1 + 1e-9)  # float representation slack only
        try:
            q = float(self.exchange.amount_to_precision(symbol, avail))
            if q > limit:  # exchange rounded to nearest: step back below the free balance
                q = float(self.exchange.amount_to_precision(symbol, avail - (q - avail)))
        except ccxt.InvalidOrder:
            return 0.0
        return q if 0 < q <= limit else 0.0

    def _fold_dust(self, ts: int, price: float) -> None:
        """Treat a leftover below min_order_notional as flat: it cannot be sold, so it
        moves into the baseline (outside the strategy) and the position is closed."""
        st = self.state
        dust = st["position_qty"]
        if dust <= 0:
            return
        st["baseline_base"] = float(st["baseline_base"]) + dust
        st["realized_pnl"] -= dust * st["avg_cost"]
        st["position_qty"], st["avg_cost"] = 0.0, 0.0
        msg = (f"dust {dust:.8f} {self.base} (~{dust * price:.4f} {self.quote}) below "
               "min_order_notional treated as flat; moved to baseline")
        log.info("[live] %s", msg)
        self.trade_log.record_event(ts, self.bot.name, "dust", msg)

    def _is_dust(self, qty: float, price: float | None) -> bool:
        if price is None or not math.isfinite(price) or price <= 0:
            return False
        return qty * price < self.guard.cfg.min_order_notional

    def _place(self, order: Order, free_quote: float, tag: str | int = 0,
               price: float | None = None, cap_to_position: bool = True) -> Fill | None:
        """Send one market order with a deterministic client order id and book what the
        exchange actually filled. A timeout/network error is resolved by looking the
        order up by client id; an empty async ack is re-read with fetch_order.

        A SELL is capped at the exchange's free base balance minus the baseline (rounded
        down to precision) and, unless cap_to_position=False (kill liquidation), at the
        booked position. A sellable amount below min_order_notional is dust: nothing is
        sent and, if the booked position is dust too, it is treated as flat."""
        qty = order.qty
        cid = client_order_id(self.bot.name, order.ts, tag)
        latency_ms = 0.0
        if order.side == Side.SELL:
            if cap_to_position:
                qty = min(qty, self.state["position_qty"])
            try:
                qty = min(qty, self._free_sellable(order.symbol))
            except Exception as e:  # noqa: BLE001  read failed: send uncapped, reconcile later
                log.warning("[live] free balance read failed (%s); sell not capped", redact(e))
            if self._is_dust(qty, price):
                if self._is_dust(self.state["position_qty"], price):
                    self._fold_dust(order.ts, price)
                    self._save_state()
                log.info("[live] sell of %.8f %s is below min notional; nothing sent",
                         qty, self.base)
                return None
        try:
            qty = float(self.exchange.amount_to_precision(order.symbol, qty))
            if qty <= 0:
                raise ccxt.InvalidOrder("quantity rounds to zero at exchange precision")
            t0 = time.monotonic()
            try:
                resp = self.exchange.create_order(order.symbol, "market", order.side.value, qty,
                                                  params={"newClientOrderId": cid})
            except ccxt.NetworkError as e:
                # Ambiguous: the order may have reached the exchange. Look it up by id.
                resp = find_order_by_client_id(self.exchange, order.symbol, cid)
                if resp is None:
                    raise
                log.warning("[live] send timed out (%s); found order %s by client id",
                            redact(e), cid)
            latency_ms = (time.monotonic() - t0) * 1000
        except ccxt.BaseError as e:
            resync_clock_if_needed(self.exchange, e)
            msg = f"order failed {order.side.value} {qty} (client id {cid}): {redact(e)}"
            log.error("[live] %s", msg)
            self.trade_log.record_event(order.ts, self.bot.name, "order_error", msg)
            send_alert(f"{self.bot.name}: {msg}")
            return None

        resp, filled, avg = resolve_fill(self.exchange, order.symbol, resp, cid,
                                         poll_s=getattr(self, "_fill_poll_s", 0.5))
        resp = resp or {}
        oid = resp.get("id")
        booked = self.state.setdefault("booked_order_ids", [])
        if oid is not None and str(oid) in booked:
            # A client-id lookup returned an order whose fill is already in the books
            # (e.g. a re-run after a restart): never book the same fill twice.
            msg = (f"order {oid} (client id {cid}) is already booked; not booking it again, "
                   "next bar reconciles against the exchange")
            log.warning("[live] %s", msg)
            self.trade_log.record_event(order.ts, self.bot.name, "order_error", msg)
            return None
        if not (filled > 0 and avg > 0 and math.isfinite(filled * avg)):
            msg = (f"order not filled or bad response: id={resp.get('id')} client id={cid} "
                   f"filled={filled} avg={avg}; next bar reconciles against the exchange")
            log.error("[live] %s", msg)
            self.trade_log.record_event(order.ts, self.bot.name, "order_error", msg)
            send_alert(f"{self.bot.name}: {msg}")
            return None
        if filled < qty * (1 - 1e-6):
            msg = f"partial fill {filled:.8f} of {qty:.8f} {self.base} (client id {cid})"
            log.warning("[live] %s", msg)
            self.trade_log.record_event(order.ts, self.bot.name, "partial_fill", msg)
            send_alert(f"{self.bot.name}: {msg}")
        fee, base_fee = self._fee_to_quote(self._fee_entries(resp, order.symbol, filled, avg),
                                           avg)

        st = self.state
        if order.side == Side.BUY:
            # A fee charged in the base asset is deducted from what arrives in the
            # account: book filled - fee (base); its value is already in the quote spent.
            got = max(0.0, filled - base_fee)
            basis = st["avg_cost"] * st["position_qty"] + filled * avg + (fee - base_fee * avg)
            st["position_qty"] += got
            st["avg_cost"] = basis / st["position_qty"] if st["position_qty"] > 0 else 0.0
            realized = 0.0
        else:
            realized = (avg - st["avg_cost"]) * filled - fee
            st["position_qty"] = max(0.0, st["position_qty"] - filled - base_fee)
            if st["position_qty"] < 1e-12:
                st["position_qty"], st["avg_cost"] = 0.0, 0.0
            st["realized_pnl"] += realized
        st["fees_paid"] += fee
        if oid is not None:
            booked.append(str(oid))
            del booked[:-BOOKED_IDS_KEEP]
        if order.side == Side.SELL and self._is_dust(st["position_qty"], avg):
            self._fold_dust(order.ts, avg)
        self._save_state()

        fill = Fill(ts=order.ts, symbol=order.symbol, side=order.side, qty=filled,
                    price=avg, fee=fee, bot=order.bot, realized_pnl=realized)
        self.trade_log.record_fill(fill)
        log.info("[live fill] %s %.8f @ %.2f fee %.4f latency %.0fms (%s)",
                 fill.side.value, filled, avg, fee, latency_ms, order.reason)
        send_alert(f"{self.bot.name} live {order.side.value} "
                   f"{filled:.8f} {self.base} @ {avg:,.2f} ({order.reason})")
        return fill

    def _kill(self, bar: Bar) -> None:
        """Drawdown kill: liquidate -> verify -> write kill file -> halt. Revival is manual only.

        The liquidation sells what the exchange reports as free above the baseline (read
        right before sending), not the local books, so a base-asset fee or other drift
        cannot make it fail for insufficient balance.

        Together with the perp executor's flatten path this is the only sanctioned order
        path that skips RiskGuard.check_order: once killed, the guard rejects every order,
        yet the liquidation must still go out."""
        try:
            sellable = self._free_sellable(bar.symbol)
        except Exception as e:  # noqa: BLE001
            log.error("[kill] free balance read failed (%s); selling the booked position",
                      redact(e))
            sellable = self.state["position_qty"]
        if sellable > 0 and not self._is_dust(sellable, bar.close):
            liq = Order(bar.ts, bar.symbol, Side.SELL, sellable, self.bot.name,
                        reason="kill switch liquidation")
            # Persisted retry counter in the client order id (like the perp flatten): a
            # kill re-run on the same bar after a restart sends a fresh order for what is
            # still sellable instead of colliding with, or re-booking, the earlier one.
            attempt = int(self.state.get("kill_attempt", 0)) + 1
            self.state["kill_attempt"] = attempt
            self._save_state()  # persist the nonce before anything is sent
            self._place(liq, 0.0, tag=f"kill{attempt}", price=bar.close,
                        cap_to_position=False)
        residual = self._flatten_residual(bar)
        note = ("" if residual == 0 else "; FLATTEN UNVERIFIED" if residual != residual
                else f"; FLATTEN INCOMPLETE, residual {residual:.8f} {self.base}")
        # Persist the final state (incl. killed=True) so a restart never forgets the kill.
        self.state["last_bar_ts"] = self.last_bar_ts = bar.ts
        self._save_state()
        atomic_write_text(self.kill_file, f"ts={bar.ts} reason={self.guard.kill_reason}{note}\n")
        self.close()
        send_alert(f"{self.bot.name} live kill switch halt: {self.guard.kill_reason}; "
                   f"manual revive required{note}")
        raise TraderHalted(f"Live kill switch halt: {self.guard.kill_reason}{note}")

    def _flatten_residual(self, bar: Bar) -> float:
        """Re-read the exchange after a liquidation. Returns the residual position (0.0 if
        flat or only unsellable dust below min_order_notional, which is moved to the
        baseline). A residual is booked locally and raised as a CRITICAL alert instead of
        being reported as flat."""
        try:
            residual, _ = self._account_raw()
        except Exception as e:  # noqa: BLE001
            msg = (f"flatten could not be verified (exchange read failed: {redact(e)}); "
                   "check the account manually")
            log.critical("[kill] %s", msg)
            self.trade_log.record_event(bar.ts, self.bot.name, "flatten_incomplete", msg)
            send_alert(f"[CRITICAL] {self.bot.name}: {msg}")
            return float("nan")
        if residual * bar.close < self.guard.cfg.min_order_notional:
            if residual > 0:
                self.state["position_qty"] = residual
                self._fold_dust(bar.ts, bar.close)
            else:
                self.state["position_qty"], self.state["avg_cost"] = 0.0, 0.0
            self._save_state()
            return 0.0
        msg = f"flatten incomplete, residual {residual:.8f} {self.base} still on the exchange"
        log.critical("[kill] %s", msg)
        self.trade_log.record_event(bar.ts, self.bot.name, "flatten_incomplete", msg)
        send_alert(f"[CRITICAL] {self.bot.name}: {msg}")
        self.state["position_qty"] = residual
        self._save_state()
        return residual

    # ---- main loop ----

    def step(self, raw_bar: list) -> None:
        bar = Bar(
            ts=int(raw_bar[0]), open=float(raw_bar[1]), high=float(raw_bar[2]),
            low=float(raw_bar[3]), close=float(raw_bar[4]), volume=float(raw_bar[5]),
            symbol=self.bot.symbol, timeframe=self.bot.timeframe,
        )
        name = self.bot.name
        if self.last_bar_ts is not None and bar.ts <= self.last_bar_ts:
            log.info("Bar %s already decided; not re-running it", bar.ts)
            return

        exchange_qty, free_quote = self._account_raw()
        position = self._reconcile(bar.ts, exchange_qty, bar.close)
        equity = free_quote + position * bar.close

        for event in self.guard.on_mark(bar.ts, equity):
            log.warning("[risk] %s", event)
            self.trade_log.record_event(bar.ts, name, "risk", event)
            send_alert(f"{name} (live): {event}")
        if self.guard.killed:
            self._kill(bar)

        score = as_of(load_composite(), bar.ts)
        if score is not None:
            self.bot.sentiment = score

        account = AccountView(cash=free_quote, position_qty=position,
                              avg_cost=self.state["avg_cost"], last_price=bar.close)
        # Mark the bar decided before any order goes out: a crash or exception from here
        # on must never lead to on_bar running (and ordering) twice for the same bar.
        self.state["last_bar_ts"] = self.last_bar_ts = bar.ts
        self._save_state()
        for i, sig in enumerate(self.bot.on_bar(bar, account)):
            order = Order(bar.ts, bar.symbol, sig.side, sig.qty, name, reason=sig.reason)
            verdict = self.guard.check_order(order, bar.close, equity, position)
            if not verdict.ok:
                log.warning("[reject] %s %s: %s", sig.side.value, sig.qty, verdict.reason)
                self.trade_log.record_event(bar.ts, name, "reject", verdict.reason)
                continue
            fill = self._place(order, free_quote, tag=i, price=bar.close)
            if fill:
                position = self.state["position_qty"]
                _, free_quote = self._account_raw()

        self.trade_log.record_equity(bar.ts, name, equity, free_quote, position, bar.close)
        try:
            self.trade_log.record_strategy_state(
                bar.ts, name, json.dumps(self.bot.describe(), ensure_ascii=False, default=str))
        except Exception as e:
            log.warning("Strategy snapshot write failed: %s", e)
        self.state["last_bar_ts"] = self.last_bar_ts = bar.ts
        self._save_state()

    def run_forever(self) -> None:
        log.info("Live executor started: %s %s %s (exchange is source of truth)",
                 self.bot.name, self.bot.symbol, self.bot.timeframe)
        send_alert(f"{self.bot.name} live executor started")
        # Warmup rebuilds indicators (same as paper) and aligns last_bar_ts to its end.
        warmed = warmup_strategy(self.bot)
        if warmed is not None:
            self.last_bar_ts = max(self.last_bar_ts or 0, warmed)
        errors = 0
        auth_alerted = False
        while True:
            beat(self.bot.name)  # heartbeat each loop: proves the process is polling
            try:
                raw = fetch_latest_closed_bar(
                    self.exchange, self.bot.symbol, self.bot.timeframe)
                if raw and (self.last_bar_ts is None or raw[0] > self.last_bar_ts):
                    self.step(raw)  # step calls fetch_balance, which exercises auth
                else:
                    # No new bar: run a cheap authenticated probe so credential/whitelist
                    # problems surface promptly (slow timeframes otherwise only hit
                    # public endpoints between bars).
                    self.exchange.fetch_balance()
                errors = 0  # success: reset backoff
                auth_alerted = False
            except SystemExit:
                raise
            except Exception as e:  # network blips / rate limits: back off and retry
                errors += 1
                log.error("Polling error (%d in a row): %s", errors, redact(e))
                resync_clock_if_needed(self.exchange, e)
                # Auth failure: record a risk event and alert once, with the outbound IP.
                if not auth_alerted and is_auth_error(e):
                    auth_alerted = True
                    full, masked = auth_fail_detail("live auth failed; cannot trade or read balances")
                    log.error("[auth] %s", full)
                    self.trade_log.record_event(int(time.time() * 1000), self.bot.name,
                                                "auth_fail", masked)
                    send_alert(f"{self.bot.name}: {masked}")
            time.sleep(next_sleep(self.bot.timeframe, errors))
