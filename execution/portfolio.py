"""Spot portfolio accounting, shared by the backtest engine and the paper trader.

Realized PnL uses the average-cost method. Spot semantics: no negative position,
no negative cash.
"""
from __future__ import annotations

from core.models import AccountView, Fill, Order, Side


class Portfolio:
    def __init__(self, cash: float, fee_bps: float, slippage_bps: float):
        self.cash = cash
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps
        self.position_qty = 0.0
        self.avg_cost = 0.0
        self.fees_paid = 0.0
        self.realized_pnl = 0.0

    def view(self, last_price: float) -> AccountView:
        return AccountView(
            cash=self.cash,
            position_qty=self.position_qty,
            avg_cost=self.avg_cost,
            last_price=last_price,
        )

    def equity(self, last_price: float) -> float:
        return self.cash + self.position_qty * last_price

    def fill_price(self, side: Side, ref_price: float) -> float:
        """Market-order fill price = reference price +/- slippage."""
        slip = self.slippage_bps / 10_000
        return ref_price * (1 + slip) if side == Side.BUY else ref_price * (1 - slip)

    def execute(self, order: Order, ref_price: float) -> Fill | None:
        """Fill at market. Shrinks qty if cash/position is insufficient; None if nothing fills."""
        price = self.fill_price(order.side, ref_price)
        fee_rate = self.fee_bps / 10_000
        qty = order.qty

        if order.side == Side.BUY:
            max_affordable = self.cash / (price * (1 + fee_rate))
            qty = min(qty, max_affordable)
            if qty * price < 1e-9:
                return None
            cost = qty * price
            fee = cost * fee_rate
            # Buy fees are folded into the average cost
            total_cost_basis = self.avg_cost * self.position_qty + cost + fee
            self.position_qty += qty
            self.avg_cost = total_cost_basis / self.position_qty
            self.cash -= cost + fee
            realized = 0.0
        else:
            qty = min(qty, self.position_qty)
            if qty * price < 1e-9:
                return None
            proceeds = qty * price
            fee = proceeds * fee_rate
            realized = (price - self.avg_cost) * qty - fee
            self.position_qty -= qty
            if self.position_qty < 1e-12:
                self.position_qty = 0.0
                self.avg_cost = 0.0
            self.cash += proceeds - fee
            self.realized_pnl += realized

        self.fees_paid += fee
        return Fill(
            ts=order.ts, symbol=order.symbol, side=order.side, qty=qty,
            price=price, fee=fee, bot=order.bot, realized_pnl=realized,
        )

    def restore_avg_cost(self, fills) -> None:
        """Rebuild avg_cost from the fill history after a restart.

        The equity snapshot only persists cash/position_qty. If avg_cost stayed at 0
        after a restart, the next SELL would book its full proceeds as realized profit.
        This replays the average-cost method over stored fills (prices already include
        slippage) so accounting after a restart matches an uninterrupted run. Spot only
        (no funding/liquidation, so fills fully determine avg_cost). Must stay in sync
        with the cost accumulation in execute()."""
        pos, avg = 0.0, 0.0
        for row in fills.itertuples(index=False):
            if row.side == Side.BUY.value:
                basis = avg * pos + row.qty * row.price + row.fee
                pos += row.qty
                avg = basis / pos if pos > 0 else 0.0
            else:
                pos -= row.qty
                if pos < 1e-12:
                    pos, avg = 0.0, 0.0
        self.avg_cost = avg
