"""USDT-margined perpetual live executor (real long/short with leverage).

Same discipline as the spot LiveTrader, with MarginPortfolio accounting (signed
position + leverage):
- Only manages its own symbol (e.g. SOL/USDT:USDT). Positions are read per symbol and
  capital is accounted per bot; positions on other symbols are outside its scope.
- Sets isolated margin + leverage at startup (leverage clamped to the hard cap) and
  refuses to start if the exchange rejects either setting (transient network errors
  exit as ordinary, restartable errors). Where the ccxt exchange supports reading
  them back, a mismatch also refuses to start; read-back calls that are unsupported
  or fail are logged and skipped. Hedge (dual-side) position mode is detected and
  handled.
- Persistent kill and breaker state (peak, daily HALT, KILL), single-instance lock,
  bar-decided-once marking, deterministic client order ids with timeout lookup, atomic
  state writes, capital cap, key-permission checks, auth-failure alerts and -1021 clock
  re-sync as in the spot executor.
- Exchange-truth reconciliation: a position the local books missed is adopted at the
  exchange entry price (when unambiguous) or the mark price, through the same signed
  accounting as a fill, so adoption never creates phantom PnL. Fills are booked as the
  exchange filled them (no local leverage-cap shrinking).
- Optional soft cut: an earlier, softer stop than the hard kill (see _soft_cut).
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import ccxt

from core.config import storage_dir, trade_keys
from core.models import Bar, Fill, Order, Side
from core.polling import next_sleep
from data.fetcher import fetch_latest_closed_bar
from data.store import TradeLog
from monitor.alerts import send_alert
from monitor.heartbeat import beat
from monitor.logger import get_logger
from strategies.base import Strategy
from safety.guard import RiskConfig, RiskGuard
from execution.halt import TraderHalted
from execution.common import (InstanceLock, atomic_write_json, atomic_write_text,
                              client_order_id, find_order_by_client_id,
                              key_restriction_problems, redact, reset_startup_auth_failures,
                              resolve_fill)
from execution.live import (auth_fail_detail, is_auth_error, require_live_setup,
                            require_positive, resync_clock_if_needed, startup_auth_failed)
from execution.margin_portfolio import MarginPortfolio
from execution.warmup import funding_path, warmup_strategy

log = get_logger("live_perp")

HARD_MAX_LEVERAGE = 3  # exchange leverage never exceeds this, whatever the config says


def _benign_margin_error(exc: BaseException) -> bool:
    """Binance rejects a margin-type change that is already in effect (-4046)."""
    msg = str(exc).lower()
    return "no need to change margin type" in msg or "-4046" in msg


class LivePerpTrader:
    def __init__(
        self,
        bot: Strategy,
        capital: float,
        fee_bps: float,
        slippage_bps: float,
        risk: RiskConfig,
        max_leverage: float,
        leverage: int = 2,
        exchange: ccxt.Exchange | None = None,
        live_dir: Path | None = None,
        trade_log: TradeLog | None = None,
        soft_cut_pct: float | None = None,
        max_consec_losses: int | None = None,
        stage: str | None = None,
        dry_run: bool = False,
    ):
        require_live_setup(bot.name, stage, risk)
        capital = require_positive(bot.name, "cash_cap (capital)", capital)
        require_positive(bot.name, "max_leverage", max_leverage)
        require_positive(bot.name, "leverage", leverage)
        if soft_cut_pct is not None:
            soft_cut_pct = require_positive(bot.name, "soft_cut_pct", soft_cut_pct,
                                            below_one=True)
        if max_consec_losses is not None:
            n = require_positive(bot.name, "max_consec_losses", max_consec_losses)
            if n != int(n):
                raise TraderHalted(f"{bot.name}: max_consec_losses must be a whole number "
                                   f">= 1, got {max_consec_losses!r}; fix the config")
            max_consec_losses = int(n)
        # dry_run: read-only startup (retire dry run): margin mode / leverage are not
        # changed on the exchange.
        self.dry_run = dry_run
        self.bot = bot
        self.capital = capital
        self.leverage = max(1, int(min(leverage, max_leverage, HARD_MAX_LEVERAGE)))
        if self.leverage != leverage:
            log.warning("Leverage %s clamped to %dx (max_leverage %s, hard cap %d)",
                        leverage, self.leverage, max_leverage, HARD_MAX_LEVERAGE)
        # Soft cut: triggers earlier than the hard drawdown kill. Drawdown >= soft_cut_pct
        # or max_consec_losses losing closes in a row -> flatten, halt, alert, write
        # .softcut. Unlike the kill, an operator may simply delete the marker after
        # re-tuning; no revive is needed.
        self.soft_cut_pct = soft_cut_pct
        self.max_consec_losses = max_consec_losses
        self.perp = bot.symbol + ":" + bot.symbol.split("/")[1]  # e.g. SOL/USDT:USDT
        self.fee_bps = fee_bps
        self.live_dir = Path(live_dir or (storage_dir() / "live"))
        self.live_dir.mkdir(parents=True, exist_ok=True)
        self._lock = InstanceLock(self.live_dir, bot.name)
        try:
            self._init(bot, capital, fee_bps, risk, max_leverage, exchange, trade_log)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Release the single-instance lock (also released on process exit)."""
        lock = getattr(self, "_lock", None)
        if lock is not None:
            lock.close()

    def _init(self, bot, capital, fee_bps, risk, max_leverage, exchange, trade_log) -> None:
        if self.kill_file.exists():
            info = self.kill_file.read_text().strip()
            raise TraderHalted(f"{bot.name} is halted by the kill switch ({info}). "
                             "Investigate, then revive manually with scripts/revive.py")
        if self.softcut_file.exists():
            info = self.softcut_file.read_text().strip()
            raise TraderHalted(f"{bot.name} is halted by soft cut ({info}). "
                             f"Delete {self.softcut_file.name} after re-tuning to resume")

        if exchange is None:
            key, secret = trade_keys()  # trade key is loaded from .env only
            exchange = ccxt.binance({"apiKey": key, "secret": secret, "enableRateLimit": True,
                                     "options": {"defaultType": "future"}})
        self.exchange = exchange
        self._assert_key_safety()
        self._setup_margin(apply=not self.dry_run)

        self.trade_log = trade_log or TradeLog(self.live_dir / "live.sqlite")
        # Slippage 0: live average fill prices already include real slippage; only fees
        # are applied locally to avoid double counting.
        self.portfolio = MarginPortfolio(capital, fee_bps, 0.0, max_leverage)
        self.guard = RiskGuard(risk, capital)
        self.state = self._load_state()
        self.last_bar_ts: int | None = self.state.get("last_bar_ts")
        # Baseline: pre-existing position on the same perp (if any) is excluded.
        self._baseline_perp = self.state.get("baseline_perp", self._exchange_perp_raw())
        self.portfolio.position_qty = self.state.get("position_qty", 0.0)
        self.portfolio.avg_cost = self.state.get("avg_cost", 0.0)
        # Persist accounting and the drawdown peak across restarts; otherwise cash and
        # realized PnL reset and the kill drawdown is only measured within one run.
        self.portfolio.cash = self.state.get("cash", capital)
        self.portfolio.realized_pnl = self.state.get("realized_pnl", 0.0)
        self.portfolio.fees_paid = self.state.get("fees_paid", 0.0)
        self.portfolio.funding_paid = self.state.get("funding_paid", 0.0)
        # Breakers survive restarts (peak, daily HALT, KILL). Older state files only
        # carried a top-level peak_equity.
        guard_state = self.state.get("guard")
        if guard_state is None and "peak_equity" in self.state:
            guard_state = {"peak_equity": self.state["peak_equity"]}
        self.guard.load_state(guard_state)
        if self.guard.killed:
            raise TraderHalted(
                f"{bot.name}: saved state records an active kill ({self.guard.kill_reason}). "
                "Investigate, then revive manually with scripts/revive.py")
        self._consec_losses = int(self.state.get("consec_losses", 0))

    # ---- persistence ----
    @property
    def kill_file(self) -> Path:
        return self.live_dir / f"{self.bot.name}.kill"

    @property
    def softcut_file(self) -> Path:
        return self.live_dir / f"{self.bot.name}.softcut"

    @property
    def state_file(self) -> Path:
        return self.live_dir / f"{self.bot.name}_state.json"

    def _load_state(self) -> dict:
        if self.state_file.exists():
            s = json.loads(self.state_file.read_text())
            log.info("Restored perp live state: %s (indicators rebuilt by warmup)", s)
            return s
        s = {"baseline_perp": self._exchange_perp_raw(), "position_qty": 0.0,
             "avg_cost": 0.0, "last_bar_ts": None}
        atomic_write_json(self.state_file, s)
        log.info("First start, %s perp baseline %.6f (existing position not counted)",
                 self.perp, s["baseline_perp"])
        return s

    def _save_state(self) -> None:
        self.state.update(baseline_perp=self._baseline_perp,
                          position_qty=self.portfolio.position_qty,
                          avg_cost=self.portfolio.avg_cost, last_bar_ts=self.last_bar_ts,
                          cash=self.portfolio.cash, realized_pnl=self.portfolio.realized_pnl,
                          fees_paid=self.portfolio.fees_paid,
                          funding_paid=self.portfolio.funding_paid,
                          guard=self.guard.export_state(),
                          consec_losses=self._consec_losses)
        self.state.pop("peak_equity", None)  # legacy field, now inside "guard"
        atomic_write_json(self.state_file, self.state)

    # ---- safety ----
    def _assert_key_safety(self) -> None:
        try:
            r = self.exchange.sapi_get_account_apirestrictions()
        except Exception as e:  # noqa: BLE001
            if is_auth_error(e):
                raise startup_auth_failed(self.live_dir, self.bot.name, e,
                                          "perp live startup auth failed") from e
            raise
        reset_startup_auth_failures(self.live_dir, self.bot.name)

        problems = key_restriction_problems(r, "futures")
        if problems:
            raise TraderHalted("Perp live self-check failed: " + "; ".join(problems))

    def _setup_margin(self, apply: bool = True) -> None:
        """Isolated margin + leverage, failing closed: an exchange rejection other than
        "already set" refuses to start (TraderHalted). Transient network errors
        (ccxt.NetworkError, incl. RequestTimeout) propagate as ordinary errors so the
        service manager can restart and retry. Also reads the exchange minimum amount/notional for pre-checks
        (avoids repeated rejections). apply=False (dry run) changes no exchange setting
        and only performs the reads."""
        if apply:
            self._apply_margin()
        else:
            log.info("Dry run: margin mode / leverage left unchanged on the exchange")
        self._read_limits_and_mode()

    def _apply_margin(self) -> None:
        try:
            self.exchange.set_margin_mode("isolated", self.perp)
        except ccxt.NetworkError:
            raise  # transient (timeout/connectivity): ordinary error, restartable
        except Exception as e:  # noqa: BLE001
            if not _benign_margin_error(e):
                raise TraderHalted(f"{self.bot.name}: could not set isolated margin on "
                                   f"{self.perp} ({redact(e)}); refusing to start") from e
            log.info("Isolated margin already set on %s", self.perp)
        try:
            self.exchange.set_leverage(self.leverage, self.perp)
        except ccxt.NetworkError:
            raise  # transient (timeout/connectivity): ordinary error, restartable
        except Exception as e:  # noqa: BLE001
            raise TraderHalted(f"{self.bot.name}: could not set leverage {self.leverage}x on "
                               f"{self.perp} ({redact(e)}); refusing to start") from e
        self._verify_margin()

    def _read_limits_and_mode(self) -> None:
        self._min_amt = 0.0
        self._min_cost = 0.0
        try:
            m = self.exchange.market(self.perp)
            self._min_amt = float(m["limits"]["amount"]["min"] or 0.0)
            self._min_cost = float((m["limits"].get("cost") or {}).get("min") or 0.0)
        except Exception as e:  # noqa: BLE001  test fakes have no market(); skip pre-check
            log.info("Could not read min order limits (pre-check disabled): %s", redact(e)[:80])
        # Position mode: in hedge (dual-side) mode every order needs positionSide or it is
        # rejected with -4061. Switching modes requires a flat account, so adapt instead.
        self._hedge = False
        try:
            r = self.exchange.fapiPrivateGetPositionSideDual()
            self._hedge = str(r.get("dualSidePosition")).lower() == "true"
            log.info("Position mode: %s",
                     "hedge (orders carry positionSide)" if self._hedge else "one-way")
        except Exception as e:  # noqa: BLE001  fakes lack this endpoint -> one-way
            log.info("Position mode query failed (assuming one-way): %s", redact(e)[:80])

    def _verify_margin(self) -> None:
        """Read back margin mode and leverage where the ccxt exchange supports it; refuse
        to start on a mismatch. Unsupported or failed reads are logged and skipped."""
        problems = []
        fetch_mode = getattr(self.exchange, "fetch_margin_mode", None)
        if callable(fetch_mode):
            try:
                mode = (fetch_mode(self.perp) or {}).get("marginMode")
                if mode and str(mode).lower() != "isolated":
                    problems.append(f"margin mode is {mode!r}, expected 'isolated'")
            except Exception as e:  # noqa: BLE001
                log.warning("Margin mode read-back skipped: %s", redact(e)[:80])
        fetch_lev = getattr(self.exchange, "fetch_leverage", None)
        if callable(fetch_lev):
            try:
                r = fetch_lev(self.perp) or {}
                for k in ("longLeverage", "shortLeverage"):
                    v = r.get(k)
                    if v is not None and int(float(v)) != self.leverage:
                        problems.append(f"{k} is {v}, expected {self.leverage}")
            except Exception as e:  # noqa: BLE001
                log.warning("Leverage read-back skipped: %s", redact(e)[:80])
        if problems:
            raise TraderHalted(f"{self.bot.name}: margin setup verification failed on "
                               f"{self.perp}: " + "; ".join(problems) + "; refusing to start")

    # ---- account ----
    def _mark(self) -> float:
        return float(self.exchange.fetch_ticker(self.perp)["last"])

    def _exchange_perp_info(self) -> tuple[float, float | None]:
        """(signed net position, entry price if exactly one leg is open else None)."""
        # In hedge mode a symbol can have LONG and SHORT legs; net them (signed).
        net = 0.0
        legs = []
        for p in self.exchange.fetch_positions([self.perp]):
            amt = float(p.get("contracts") or 0.0)
            if not math.isfinite(amt):
                raise ValueError(f"non-finite position size {p.get('contracts')!r} for {self.perp}")
            if amt:
                net += -amt if p.get("side") == "short" else amt
                legs.append(p)
        entry = None
        if len(legs) == 1:
            try:
                entry = float(legs[0].get("entryPrice") or 0.0) or None
            except (TypeError, ValueError):
                entry = None
            if entry is not None and not math.isfinite(entry):
                entry = None
        return net, entry

    def _exchange_perp_raw(self) -> float:
        return self._exchange_perp_info()[0]

    def _perp_position(self) -> float:
        """This bot's signed position = exchange perp position - baseline."""
        return self._exchange_perp_raw() - self._baseline_perp

    def _reconcile(self, ts: int, exchange_qty: float, mark: float | None = None,
                   entry_price: float | None = None) -> None:
        """Adopt the exchange position when local books disagree (e.g. a fill the
        response did not report). The difference is booked like a fee-less fill:
        - at the exchange entry price when local was flat and there is no baseline
          (the exchange entry is then exactly this bot's entry), else
        - at the mark price, so unrealized PnL of the adopted part starts at 0.
        Cash moves only by PnL realized on any reduced part, keeping equity consistent."""
        expected = self.portfolio.position_qty
        if abs(exchange_qty - expected) <= max(1e-6, abs(expected) * 0.01):
            return
        price = None
        how = ""
        if (entry_price and abs(expected) < 1e-12 and abs(self._baseline_perp) < 1e-12):
            price, how = entry_price, "exchange entry price"
        else:
            if mark is None:
                try:
                    mark = self._mark()
                except Exception as e:  # noqa: BLE001
                    log.warning("Mark read failed during reconcile: %s", redact(e))
                    mark = None
            if mark is not None and math.isfinite(mark) and mark > 0:
                price, how = mark, "mark price"
        if price is None:
            # No usable price: adopt the size only and keep avg cost (never invent PnL).
            self.portfolio.position_qty = exchange_qty
            if abs(exchange_qty) < 1e-12:
                self.portfolio.position_qty, self.portfolio.avg_cost = 0.0, 0.0
            how = "no price available, avg cost unchanged"
        else:
            self.portfolio.adopt_position(exchange_qty, price)
        msg = (f"reconcile mismatch: local {expected:.6f}, exchange {exchange_qty:.6f}; "
               f"adopting exchange value ({how})")
        log.warning("[reconcile] %s", msg)
        self.trade_log.record_event(ts, self.bot.name, "reconcile", msg)
        send_alert(f"{self.bot.name}: {msg}")

    # ---- orders ----
    def _suborders(self, side: Side, qty: float, net: float) -> list[tuple[str, str | None, float]]:
        """Split one net-position order into exchange sub-orders. One-way mode: a single
        order (positionSide=None). Hedge mode: positionSide depends on the current net
        position: opening/adding uses its own side, closing uses the side being closed,
        and any excess beyond the current position flips to the other side (two orders)."""
        if not getattr(self, "_hedge", False):
            return [(side.value, None, qty)]
        out: list[tuple[str, str | None, float]] = []
        if side == Side.BUY:
            if net < 0:  # short -> close short first
                close = min(qty, -net)
                out.append(("buy", "SHORT", close))
                if qty - close > 0:  # flip long
                    out.append(("buy", "LONG", qty - close))
            else:  # flat/long -> open/add long
                out.append(("buy", "LONG", qty))
        else:  # SELL
            if net > 0:  # long -> close long first
                close = min(qty, net)
                out.append(("sell", "LONG", close))
                if qty - close > 0:  # flip short
                    out.append(("sell", "SHORT", qty - close))
            else:  # flat/short -> open/add short
                out.append(("sell", "SHORT", qty))
        return out

    def _place(self, order: Order, closing: bool = False, tag: str | int = 0,
               reduce_only: bool = False) -> Fill | None:
        """Send one net-position order and book what the exchange filled.

        closing=True is the flatten path: no minimum-size pre-check (reduce-only orders
        are exempt on the exchange). The caller decides reduce_only (one-way mode); in
        hedge mode each sub-order carries positionSide instead. Every sub-order carries a
        deterministic client order id (<bot>-<bar_ts>-<tag>.<leg>); an ambiguous send
        (timeout / network error) is resolved by looking the order up by that id."""
        try:
            qty = float(self.exchange.amount_to_precision(self.perp, order.qty))
        except ccxt.InvalidOrder as e:
            if not closing:
                raise
            # Dust below the exchange's amount precision cannot be sent. Treat it as
            # flat; the caller's _verify_flat re-reads the exchange and decides.
            log.warning("[perp] flatten qty %.10f below exchange precision (%s); "
                        "treating as flat", order.qty, str(e)[:80])
            return None
        if qty <= 0:
            return None
        if not closing:
            # Minimum-order pre-check: below exchange min amount/notional, skip cleanly
            # (not an error; avoids an error on every bar).
            notional = qty * self._mark()
            mn_amt = getattr(self, "_min_amt", 0.0)
            mn_cost = getattr(self, "_min_cost", 0.0)
            if (mn_amt and qty < mn_amt) or (mn_cost and notional < mn_cost):
                msg = (f"signal order {qty} (notional ~{notional:.1f}) below exchange minimum "
                       f"{mn_amt}/{mn_cost}; skipped. Capital/leverage too small for the "
                       "minimum order size")
                log.warning("[perp] %s", msg)
                self.trade_log.record_event(order.ts, self.bot.name, "skip_min", msg)
                return None
        if getattr(self, "_hedge", False):
            reduce_only = False  # hedge mode: positionSide bounds each leg instead
        filled = 0.0
        cost = 0.0
        sent = 0.0
        subs = self._suborders(order.side, qty, self.portfolio.position_qty)
        for i, (side_str, pos_side, sq) in enumerate(subs):
            try:
                sq = float(self.exchange.amount_to_precision(self.perp, sq))
            except ccxt.InvalidOrder:
                if not closing:
                    raise
                continue  # dust leg on the flatten path: nothing sendable
            if sq <= 0:
                continue
            cid = client_order_id(self.bot.name, order.ts, f"{tag}.{i}")
            params: dict = {"positionSide": pos_side} if pos_side else {}
            if reduce_only:
                params["reduceOnly"] = True
            params["newClientOrderId"] = cid
            sent += sq
            try:
                try:
                    resp = self.exchange.create_order(self.perp, "market", side_str, sq,
                                                      params=params)
                except ccxt.NetworkError as e:
                    resp = find_order_by_client_id(self.exchange, self.perp, cid)
                    if resp is None:
                        raise
                    log.warning("[perp] send timed out (%s); found order %s by client id",
                                redact(e), cid)
            except ccxt.BaseError as e:
                resync_clock_if_needed(self.exchange, e)
                skipped = len(subs) - i - 1
                msg = (f"perp order failed {side_str} {sq} (positionSide={pos_side}, client id "
                       f"{cid}): {redact(e)}"
                       + (f"; {skipped} remaining sub-order(s) aborted" if skipped else ""))
                log.error("[perp] %s", msg)
                self.trade_log.record_event(order.ts, self.bot.name, "order_error", msg)
                send_alert(f"{self.bot.name}: {msg}")
                break
            resp, got, px = resolve_fill(self.exchange, self.perp, resp, cid,
                                         poll_s=getattr(self, "_fill_poll_s", 0.5))
            if got > 0 and px <= 0:
                px = self._mark()
                log.warning("[perp] fill without average price; booking at mark %.4f", px)
            if got > 0 and not (math.isfinite(got) and math.isfinite(px) and px > 0):
                msg = (f"perp order {side_str} {sq} (client id {cid}) returned a non-finite "
                       f"fill (filled={got!r}, price={px!r}); not booked, next bar reconciles")
                log.critical("[perp] %s", msg)
                self.trade_log.record_event(order.ts, self.bot.name, "order_error", msg)
                send_alert(f"[CRITICAL] {self.bot.name}: {msg}")
                got = 0.0
            if got <= 0:
                msg = (f"perp order {side_str} {sq} (client id {cid}) reported no fill; "
                       "next bar reconciles against the exchange")
                log.error("[perp] %s", msg)
                self.trade_log.record_event(order.ts, self.bot.name, "order_error", msg)
                send_alert(f"{self.bot.name}: {msg}")
            filled += got
            cost += got * px
            if got < sq * (1 - 1e-6) and i < len(subs) - 1:
                # Hedge flip: never open the new side unless the close fully filled.
                msg = (f"closing leg {side_str} {sq} filled only {got}; "
                       "opening leg not sent")
                log.error("[perp] %s", msg)
                self.trade_log.record_event(order.ts, self.bot.name, "order_error", msg)
                send_alert(f"{self.bot.name}: {msg}")
                break
        if filled <= 0:
            return None
        avg = cost / filled
        if not math.isfinite(avg):
            return None
        if sent > 0 and abs(filled - qty) > qty * 1e-6:
            msg = f"filled {filled:.6f} of requested {qty:.6f}; booking the exchange fill"
            log.warning("[perp] %s", msg)
            self.trade_log.record_event(order.ts, self.bot.name, "partial_fill", msg)
            send_alert(f"{self.bot.name}: {msg}")
        # Book exactly what the exchange filled (MarginPortfolio handles add/reduce/flip);
        # never shrink or drop an exchange fill with the local leverage cap.
        fill = self.portfolio.book_fill(
            Order(order.ts, order.symbol, order.side, filled, order.bot, order.reason),
            filled, avg, filled * avg * self.fee_bps / 10_000)
        if fill:
            self.trade_log.record_fill(fill)
            if fill.realized_pnl:  # only closes count toward the losing streak
                self._consec_losses = self._consec_losses + 1 if fill.realized_pnl < 0 else 0
            log.info("[perp fill] %s %.6f @ %.2f (%s)", order.side.value, filled, avg, order.reason)
            send_alert(f"{self.bot.name} live perp {order.side.value} "
                       f"{filled:.6f} @ {avg:,.2f} ({order.reason})")
        return fill

    def _flatten_and_persist(self, bar: Bar, reason: str) -> str:
        """Close the bot's position, verify against the exchange and persist the final
        state before a halt. Returns "" when flat, otherwise a note for the halt message.

        Kill, soft cut and retire all go through here; it is the only sanctioned order
        path that skips RiskGuard.check_order (once killed the guard rejects everything,
        yet the close must still go out).

        The exchange position is re-read right before closing. With raw = exchange net
        position, baseline = the pre-existing position recorded at first start (not this
        bot's) and the bot's position = raw - baseline (one-way mode):
        - Baseline closed outside the bot (see _detect_baseline_change: the exchange is
          flat, or raw has the baseline's sign but a smaller size, and the local books do
          not show the bot holding raw - baseline): the baseline is reset, a CRITICAL
          alert/event is raised and only the bot's own booked position (if any) is
          closed; with the bot flat, nothing is sent.
        - raw has the bot position's sign: a reduceOnly close capped at
          min(|bot position|, |raw|), so it can never open exposure.
        - Otherwise the bot's own position (confirmed by the local books) offsets the
          baseline: a plain order of exactly |raw - baseline| restores the baseline;
          reduceOnly would be rejected there.
        Hedge mode: legs carry positionSide. If the position read fails, the local
        position is closed reduceOnly.

        Each flatten attempt uses a fresh client order id (a persisted attempt counter is
        part of the tag), so a re-run on the same bar never reuses an old id.

        step() saves state at its end, which a SystemExit would skip; without this the
        state file would freeze before the close (phantom position, missing realized loss).
        """
        hedge = getattr(self, "_hedge", False)
        try:
            raw = self._exchange_perp_raw()
        except Exception as e:  # noqa: BLE001
            raw = None
            log.error("[flatten] position read failed (%s); closing the local position",
                      redact(e))
        note = ""
        if raw is not None:
            if self._detect_baseline_change(bar.ts, raw):
                note = "BASELINE CHANGED OUTSIDE THE BOT"
            self._reconcile(bar.ts, raw - self._baseline_perp)
        pos = self.portfolio.position_qty
        if abs(pos) > 1e-9:
            side = Side.SELL if pos > 0 else Side.BUY
            qty = abs(pos)
            reduce_only = True
            if raw is not None and not hedge:
                if raw * pos > 0:
                    qty = min(qty, abs(raw))
                elif abs(self._baseline_perp) > 1e-12:
                    reduce_only = False  # bot's own position offsets the baseline
            attempt = int(self.state.get("flatten_attempt", 0)) + 1
            self.state["flatten_attempt"] = attempt
            self._save_state()  # persist the nonce before anything is sent
            self._place(Order(bar.ts, bar.symbol, side, qty, self.bot.name, reason),
                        closing=True, tag=f"x{attempt}", reduce_only=reduce_only)
        elif note:
            note += ", nothing sent"
        residual_note = self._verify_flat(bar)
        self.last_bar_ts = bar.ts
        self._save_state()
        return "; ".join(n for n in (note, residual_note) if n)

    def _detect_baseline_change(self, ts: int, raw: float) -> bool:
        """Detect a baseline (pre-existing, not this bot's) position closed outside the
        bot, one-way mode. Adopting raw - baseline as the bot's position there would
        invent a phantom position, and closing it would re-open the closed exposure.

        Fires when a baseline exists, the local books disagree with raw - baseline and
        either the exchange is flat or raw has the baseline's sign with a smaller size.
        The bot's booked position is kept when raw - booked is a smaller same-sign
        baseline; otherwise (exchange flat) the bot is booked flat at the mark. The
        baseline is reset and a CRITICAL alert/event raised. Returns True if it fired."""
        base = self._baseline_perp
        if getattr(self, "_hedge", False) or abs(base) < 1e-12:
            return False
        local = self.portfolio.position_qty
        derived = raw - base
        if abs(local - derived) <= max(1e-6, abs(local) * 0.01):
            return False  # books agree: the bot genuinely holds raw - baseline
        flat = abs(raw) < 1e-12
        if not (flat or (raw * base > 0 and abs(raw) < abs(base))):
            return False
        new_base = raw - local
        if abs(new_base) < 1e-12 or (new_base * base > 0 and abs(new_base) < abs(base)):
            how = "bot position kept"
        else:  # exchange flat: whatever the books held is gone too
            if abs(local) > 1e-12:
                self._reconcile(ts, 0.0)  # realize the booked position at the mark
            self.portfolio.position_qty, self.portfolio.avg_cost = 0.0, 0.0
            new_base, how = raw, "bot booked flat"
        self._baseline_perp = new_base
        msg = (f"baseline position changed outside the bot (baseline {base:+.6f}, exchange "
               f"{raw:+.6f}, local books {local:+.6f}); baseline reset to {new_base:+.6f}, "
               f"{how}; no order sent for the baseline. Check the account manually")
        log.critical("[baseline] %s", msg)
        self.trade_log.record_event(ts, self.bot.name, "baseline_changed", msg)
        send_alert(f"[CRITICAL] {self.bot.name}: {msg}")
        self._save_state()
        return True

    def _verify_flat(self, bar: Bar) -> str:
        """Re-read the exchange position after a flatten. A residual is adopted locally
        and raised as a CRITICAL alert instead of being reported as flat."""
        try:
            residual = self._perp_position()
        except Exception as e:  # noqa: BLE001
            msg = (f"flatten could not be verified (position read failed: {redact(e)}); "
                   "check the account manually")
            note = "FLATTEN UNVERIFIED"
        else:
            try:
                closable = float(self.exchange.amount_to_precision(self.perp, abs(residual)))
            except ccxt.InvalidOrder:
                closable = 0.0  # below exchange precision: unsendable dust counts as flat
            except Exception:  # noqa: BLE001
                closable = abs(residual)
            if closable <= 1e-12:
                return ""
            self.portfolio.position_qty = residual
            msg = f"flatten incomplete, residual {residual:+.6f} {self.perp} still open on the exchange"
            note = f"FLATTEN INCOMPLETE, residual {residual:+.6f}"
        log.critical("[flatten] %s", msg)
        self.trade_log.record_event(bar.ts, self.bot.name, "flatten_incomplete", msg)
        send_alert(f"[CRITICAL] {self.bot.name}: {msg}")
        return note

    def _kill(self, bar: Bar) -> None:
        note = self._flatten_and_persist(bar, "kill switch liquidation")
        suffix = f"; {note}" if note else ""
        atomic_write_text(self.kill_file, f"ts={bar.ts} reason={self.guard.kill_reason}{suffix}\n")
        self.close()
        send_alert(f"{self.bot.name} perp live kill switch halt: {self.guard.kill_reason}; "
                   f"manual revive required{suffix}")
        raise TraderHalted(f"Kill switch halt: {self.guard.kill_reason}{suffix}")

    def _soft_cut_reason(self, equity: float) -> str | None:
        """Soft-cut check (earlier than the hard kill): drawdown or losing-streak threshold."""
        if self.soft_cut_pct and self.guard.peak_equity > 0:
            dd = 1 - equity / self.guard.peak_equity
            if dd >= self.soft_cut_pct:
                return f"drawdown {dd:.1%} >= soft cut {self.soft_cut_pct:.0%}"
        if self.max_consec_losses and self._consec_losses >= self.max_consec_losses:
            return f"{self._consec_losses} consecutive losses >= {self.max_consec_losses}"
        return None

    def _soft_cut(self, bar: Bar, reason: str) -> None:
        """Soft cut: flatten, write .softcut marker, alert, halt. An operator can resume by
        deleting the marker after re-tuning (unlike the hard kill)."""
        note = self._flatten_and_persist(bar, "soft cut liquidation")
        suffix = f"; {note}" if note else ""
        atomic_write_text(self.softcut_file, f"ts={bar.ts} reason={reason}{suffix}\n")
        self.close()
        self.trade_log.record_event(bar.ts, self.bot.name, "soft_cut", reason + suffix)
        send_alert(f"{self.bot.name} soft cut: {reason}; "
                   + (f"halted{suffix}" if note else "flattened and halted"))
        raise TraderHalted(f"Soft cut halt: {reason}{suffix}")

    def _inject_signals(self, bar: Bar) -> None:
        """Inject sentiment/funding signals into the strategy (same no-lookahead rules as
        paper/backtest). Signal injection only: real funding accounting comes from the
        exchange income history in _sync_funding; never apply_funding locally here, or
        funding would be counted twice."""
        try:
            from sentiment.composite import as_of
            from sentiment.store import load_composite
            comp = load_composite()
            if comp is not None and len(comp):
                score = as_of(comp, bar.ts)
                if score is not None:
                    self.bot.sentiment = score
        except Exception as e:  # noqa: BLE001  missing file -> strategy sees no data
            log.debug("Sentiment injection skipped: %s", str(e)[:60])
        try:
            import pandas as pd

            fpath = funding_path(self.bot.symbol)
            if fpath.exists():
                fund = pd.read_parquet(fpath)
                prev = self.last_bar_ts or (bar.ts - 86_400_000)
                w = fund[(fund["ts"] > prev) & (fund["ts"] <= bar.ts)]
                if len(w):
                    self.bot.funding = float(w["value"].sum())
        except Exception as e:  # noqa: BLE001
            log.debug("Funding signal injection skipped: %s", str(e)[:60])

    def _sync_funding(self, now_ts: int) -> None:
        """Book funding actually settled by the exchange into local accounts (once per bar).

        Perp funding moves wallet cash at each settlement; if MarginPortfolio ignored it,
        local cash would drift from reality and equity/kill checks would be off.
        Binance income: positive = received. Failures never break the main loop."""
        try:
            since = int(self.state.get("funding_synced_ts") or 0)
            if not since:  # first run: start from now, do not backfill history
                self.state["funding_synced_ts"] = now_ts
                return
            hist = self.exchange.fetch_funding_history(self.perp, since=since + 1)
        except Exception as e:  # noqa: BLE001  fakes lack the endpoint / network blip
            log.debug("Funding sync skipped: %s", str(e)[:60])
            return
        total = 0.0
        bad = []
        for h in hist:
            try:
                amt = float(h.get("amount") or 0.0)
            except (TypeError, ValueError):
                amt = float("nan")
            if math.isfinite(amt):
                total += amt
            else:
                bad.append(h)
        if bad:
            msg = (f"{len(bad)} funding record(s) with a non-finite amount skipped "
                   f"(e.g. {bad[0].get('amount')!r} at {bad[0].get('timestamp')}); "
                   "check the exchange income history")
            log.critical("[funding] %s", msg)
            self.trade_log.record_event(now_ts, self.bot.name, "funding_error", msg)
            send_alert(f"[CRITICAL] {self.bot.name}: {msg}")
        stamps = []
        for h in hist:
            try:
                stamps.append(int(h["timestamp"]))
            except (KeyError, TypeError, ValueError, OverflowError):
                pass
        if stamps:
            self.state["funding_synced_ts"] = max(stamps)
        if abs(total) > 1e-9:
            self.portfolio.cash += total
            self.portfolio.funding_paid += -total  # funding_paid: positive = net paid
            log.info("[funding] settled %+.4f USDT (%d periods)", total, len(hist))
            self.trade_log.record_event(now_ts, self.bot.name, "funding",
                                        f"funding settled {total:+.4f} USDT ({len(hist)} periods)")

    # ---- main loop ----
    def step(self, raw: list) -> None:
        bar = Bar(ts=int(raw[0]), open=float(raw[1]), high=float(raw[2]), low=float(raw[3]),
                  close=float(raw[4]), volume=float(raw[5]),
                  symbol=self.bot.symbol, timeframe=self.bot.timeframe)
        name = self.bot.name
        if self.last_bar_ts is not None and bar.ts <= self.last_bar_ts:
            log.info("Bar %s already decided; not re-running it", bar.ts)
            return
        raw_pos, entry = self._exchange_perp_info()
        self._detect_baseline_change(bar.ts, raw_pos)
        self._reconcile(bar.ts, raw_pos - self._baseline_perp, mark=bar.close, entry_price=entry)
        self._sync_funding(bar.ts)
        self._inject_signals(bar)
        equity = self.portfolio.equity(bar.close)
        for ev in self.guard.on_mark(bar.ts, equity):
            log.warning("[risk] %s", ev)
            self.trade_log.record_event(bar.ts, name, "risk", ev)
            send_alert(f"{name} (perp): {ev}")
        if self.guard.killed:
            self._kill(bar)
        soft = self._soft_cut_reason(equity)
        if soft:
            self._soft_cut(bar, soft)
        account = self.portfolio.view(bar.close)
        # Mark the bar decided before any order goes out: a crash or exception from here
        # on must never lead to on_bar running (and ordering) twice for the same bar.
        self.last_bar_ts = bar.ts
        self._save_state()
        for i, sig in enumerate(self.bot.on_bar(bar, account)):
            order = Order(bar.ts, bar.symbol, sig.side, sig.qty, name, reason=sig.reason)
            v = self.guard.check_order(order, bar.close, account.equity, self.portfolio.position_qty)
            if not v.ok:
                log.warning("[reject] %s", v.reason)
                self.trade_log.record_event(bar.ts, name, "reject", v.reason)
                continue
            self._place(order, tag=i)
        self.trade_log.record_equity(bar.ts, name, self.portfolio.equity(bar.close),
                                     self.portfolio.cash, self.portfolio.position_qty, bar.close)
        try:
            self.trade_log.record_strategy_state(
                bar.ts, name, json.dumps(self.bot.describe(), ensure_ascii=False, default=str))
        except Exception as e:  # noqa: BLE001
            log.warning("Strategy snapshot write failed: %s", e)
        self.last_bar_ts = bar.ts
        self._save_state()

    def run_forever(self) -> None:
        log.info("Perp live executor started: %s capital %.2f leverage %dx",
                 self.bot.name, self.capital, self.leverage)
        send_alert(f"{self.bot.name} perp live executor started "
                   f"(capital {self.capital:.2f}, {self.leverage}x)")
        warmed = warmup_strategy(self.bot)
        if warmed is not None:
            self.last_bar_ts = max(self.last_bar_ts or 0, warmed)
        errors = 0
        auth_alerted = False
        while True:
            beat(self.bot.name)
            try:
                raw = fetch_latest_closed_bar(self.exchange, self.bot.symbol, self.bot.timeframe)
                if raw and (self.last_bar_ts is None or raw[0] > self.last_bar_ts):
                    self.step(raw)
                else:
                    self.exchange.fetch_balance()  # authenticated probe between bars
                errors = 0
                auth_alerted = False
            except SystemExit:
                raise
            except Exception as e:  # noqa: BLE001
                errors += 1
                log.error("Perp polling error (%d in a row): %s", errors, redact(e))
                resync_clock_if_needed(self.exchange, e)
                if not auth_alerted and is_auth_error(e):
                    auth_alerted = True
                    full, masked = auth_fail_detail("perp live auth failed")
                    log.error("[auth] %s", full)
                    self.trade_log.record_event(int(time.time() * 1000), self.bot.name,
                                                "auth_fail", masked)
                    send_alert(f"{self.bot.name}: {masked}")
            time.sleep(next_sleep(self.bot.timeframe, errors))
