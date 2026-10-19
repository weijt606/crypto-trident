"""Margin portfolio accounting for USDT-margined (linear) perpetuals, signed position.

Same interface as the spot Portfolio (view/equity/execute/fill_price); the backtest
engine and paper trader choose an implementation from bot.market. Differences:
- position_qty is signed: positive = long, negative = short
- cash is the margin wallet balance; notional can reach equity x max_leverage
- funding: longs pay positive rates, shorts receive them (apply_funding is called by
  the engine at settlement boundaries)
- simplified liquidation: equity <= maintenance rate x notional -> close everything and
  charge a liquidation fee. With modest leverage the RiskGuard drawdown kill fires well
  before this; it is an honesty backstop, not the normal path
"""
from __future__ import annotations

from core.models import AccountView, Fill, Order, Side

MAINT_MARGIN_RATE = 0.005   # maintenance margin rate (Binance USDT-M tier 1 is 0.4%-0.5%; use the stricter)
LIQUIDATION_FEE_RATE = 0.0125  # liquidation fee (conservative vs Binance clearance fee tiers)


class MarginPortfolio:
    def __init__(self, cash: float, fee_bps: float, slippage_bps: float,
                 max_leverage: float = 3.0):
        self.cash = cash
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps
        self.max_leverage = max_leverage
        self.position_qty = 0.0   # signed
        self.avg_cost = 0.0       # entry break-even price incl. entry fees (side-aware, see _book)
        self.fees_paid = 0.0
        self.funding_paid = 0.0   # cumulative net funding paid (negative = net received)
        self.realized_pnl = 0.0
        self.liquidated = False

    # ---- same interface as Portfolio ----

    def view(self, last_price: float) -> AccountView:
        return AccountView(cash=self.cash, position_qty=self.position_qty,
                           avg_cost=self.avg_cost, last_price=last_price)

    def equity(self, last_price: float) -> float:
        return self.cash + (last_price - self.avg_cost) * self.position_qty

    def fill_price(self, side: Side, ref_price: float) -> float:
        slip = self.slippage_bps / 10_000
        return ref_price * (1 + slip) if side == Side.BUY else ref_price * (1 - slip)

    def execute(self, order: Order, ref_price: float) -> Fill | None:
        """Signed fill: adding in the same direction re-averages; the opposite side closes
        first and may flip. Quantity is shrunk to the leverage cap if it exceeds it."""
        if self.liquidated:
            return None
        price = self.fill_price(order.side, ref_price)
        fee_rate = self.fee_bps / 10_000
        signed = order.qty if order.side == Side.BUY else -order.qty
        equity = self.equity(price)
        if equity <= 0:
            return None

        # leverage cap: |position after| <= equity * max_leverage / price
        max_abs = equity * self.max_leverage / price
        after = self.position_qty + signed
        if abs(after) > max_abs:
            after = max_abs if after > 0 else -max_abs
            signed = after - self.position_qty
            if signed * (1 if order.side == Side.BUY else -1) <= 0:
                return None  # nothing left after shrinking (already at the cap)
        qty = abs(signed)
        if qty * price < 1e-9:
            return None
        return self._book(order, qty, price, qty * price * fee_rate)

    def book_fill(self, order: Order, qty: float, price: float, fee: float) -> Fill | None:
        """Book a fill exactly as the exchange executed it (live executors).

        Unlike execute(), no leverage cap or slippage is applied: once the exchange has
        filled, local accounts must follow it, never shrink or drop it."""
        if qty <= 0 or price <= 0:
            return None
        return self._book(order, qty, price, fee)

    def _book(self, order: Order, qty: float, price: float, fee: float) -> Fill:
        signed = qty if order.side == Side.BUY else -qty
        realized = 0.0
        if self.position_qty * signed >= 0:
            # same-direction add (or open from flat): entry fees folded into the
            # break-even price, side-aware so they always reduce equity. A long's
            # break-even rises by fee/qty; a short's falls by fee/qty (equity =
            # cash + (last - avg_cost) * signed qty).
            fee_sign = 1.0 if signed > 0 else -1.0
            basis = self.avg_cost * abs(self.position_qty) + qty * price + fee_sign * fee
            self.position_qty += signed
            self.avg_cost = basis / abs(self.position_qty)
        else:
            closed = min(qty, abs(self.position_qty))
            direction = 1.0 if self.position_qty > 0 else -1.0
            realized = (price - self.avg_cost) * closed * direction - fee
            self.cash += realized
            self.realized_pnl += realized
            remainder = qty - closed
            self.position_qty += signed
            if abs(self.position_qty) < 1e-12:
                self.position_qty, self.avg_cost = 0.0, 0.0
            elif remainder > 1e-12:
                self.avg_cost = price  # flipped remainder takes this fill price as new avg cost
        self.fees_paid += fee
        return Fill(ts=order.ts, symbol=order.symbol, side=order.side, qty=qty,
                    price=price, fee=fee, bot=order.bot, realized_pnl=realized)

    def adopt_position(self, new_qty: float, price: float) -> float:
        """Adopt an exchange position that local books missed (reconciliation).

        The difference is treated as a fee-less fill at `price`: a reduction realizes
        PnL against the local avg cost, an increase is re-averaged at `price`, a flip
        starts at `price`. With price = current mark the adoption leaves equity
        unchanged (no phantom PnL). Returns the realized amount booked to cash."""
        delta = new_qty - self.position_qty
        if abs(delta) < 1e-15:
            return 0.0
        side = Side.BUY if delta > 0 else Side.SELL
        fill = self._book(Order(0, "", side, abs(delta), ""), abs(delta), price, 0.0)
        return fill.realized_pnl

    # ---- perp-specific ----

    def apply_funding(self, rate: float, mark_price: float) -> float:
        """Settle one funding period (longs pay positive rates). Returns amount paid (negative = received)."""
        pay = self.position_qty * mark_price * rate
        self.cash -= pay
        self.funding_paid += pay
        return pay

    def check_liquidation(self, mark_price: float) -> bool:
        """If equity <= maintenance margin, liquidate fully and charge the fee. Returns True if it happened."""
        notional = abs(self.position_qty) * mark_price
        if notional == 0 or self.equity(mark_price) > notional * MAINT_MARGIN_RATE:
            return False
        direction = 1.0 if self.position_qty > 0 else -1.0
        realized = (mark_price - self.avg_cost) * abs(self.position_qty) * direction
        penalty = notional * LIQUIDATION_FEE_RATE
        self.cash += realized - penalty
        self.cash = max(self.cash, 0.0)  # isolated-margin semantics: losses cannot exceed the wallet
        self.realized_pnl += realized - penalty
        self.fees_paid += penalty
        self.position_qty, self.avg_cost = 0.0, 0.0
        self.liquidated = True
        return True
