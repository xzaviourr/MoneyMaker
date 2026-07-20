"""
Realistic Indian equity trade-cost model (NSE/BSE), modelled on Zerodha's
published charge structure. Every pod strategy calls estimate_trade_cost()
before declaring a trade viable, and PaperBroker calls it on every real fill
so the simulated P&L reflects true broker + statutory costs, not a flat fee.
"""
from __future__ import annotations

from decimal import Decimal

from .schemas import Exchange, Order, OrderSide, OrderType, TradeCostEstimate

# ── Zerodha brokerage (FY 2025-26) ────────────────────────────────────────
_BROKERAGE_INTRADAY_PCT = Decimal("0.0003")   # 0.03%
_BROKERAGE_INTRADAY_CAP = Decimal("20")       # ...or ₹20, whichever is LOWER
_BROKERAGE_FO_FLAT      = Decimal("20")       # flat ₹20 per executed F&O order
_BROKERAGE_DELIVERY     = Decimal("0")        # Zerodha: zero brokerage on equity delivery

# Securities Transaction Tax — charged on one leg only, never both
_STT_DELIVERY       = Decimal("0.001")     # 0.1%, buy AND sell (delivery)
_STT_INTRADAY_SELL  = Decimal("0.00025")   # 0.025%, sell only (intraday)
_STT_FO_SELL        = Decimal("0.001")     # 0.1% of premium, sell only (options)

# Exchange transaction charges (both legs)
_NSE_EQ_TXN   = Decimal("0.0000297")      # ₹2.97 per lakh
_BSE_EQ_TXN   = Decimal("0.0000375")

# Other statutory charges
_GST_RATE       = Decimal("0.18")
_SEBI_CHARGES   = Decimal("0.000001")     # ₹10 per crore, both legs
_STAMP_INTRADAY = Decimal("0.00003")      # 0.003%, buy side only
_STAMP_DELIVERY = Decimal("0.00015")      # 0.015%, buy side only
_DP_CHARGES     = Decimal("15.93")        # ₹13.5 + 18% GST — flat per scrip, delivery SELL only

# ── Capital gains tax (Indian equity, individual investor) ────────────────
_INTRADAY_TAX_RATE = Decimal("0.30")      # speculative business income — assumes highest slab
_STCG_RATE          = Decimal("0.20")     # delivery, held < 12 months (Budget 2024 rate)
_LTCG_RATE          = Decimal("0.125")    # delivery, held >= 12 months (Budget 2024 rate)
_LTCG_EXEMPTION_PER_FY = Decimal("125000")  # ₹1.25L LTCG exemption per financial year


def estimate_trade_cost(
    symbol: str,
    exchange: Exchange,
    quantity: int,
    price: Decimal,
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.LIMIT,
    is_intraday: bool = True,
    is_short: bool = False,
    holding_days: int = 0,
    spread_bps: float = 5.0,
    volume_participation: float = 0.01,
) -> TradeCostEstimate:
    """
    Returns a full cost breakdown for ONE order leg (buy or sell), including
    Zerodha-style commissions, statutory charges, impact & slippage.
    STT and stamp duty are side-aware — a single buy or sell leg is only
    ever charged the rate that applies to that side, never both.
    """
    trade_value = price * Decimal(str(quantity))
    is_buy = side == OrderSide.BUY

    # ── Brokerage ──────────────────────────────────────────────────────────
    if exchange == Exchange.NFO:
        brokerage = _BROKERAGE_FO_FLAT
    elif is_intraday:
        brokerage = min(_BROKERAGE_INTRADAY_CAP, trade_value * _BROKERAGE_INTRADAY_PCT)
    else:
        brokerage = _BROKERAGE_DELIVERY

    # ── STT ────────────────────────────────────────────────────────────────
    if exchange == Exchange.NFO:
        stt = trade_value * _STT_FO_SELL if not is_buy else Decimal("0")
    elif is_intraday:
        stt = trade_value * _STT_INTRADAY_SELL if not is_buy else Decimal("0")
    else:
        stt = trade_value * _STT_DELIVERY

    # ── Exchange transaction charge (both legs) ───────────────────────────
    if exchange == Exchange.BSE:
        txn = trade_value * _BSE_EQ_TXN
    else:
        txn = trade_value * _NSE_EQ_TXN

    # ── SEBI charges (both legs) ───────────────────────────────────────────
    sebi = trade_value * _SEBI_CHARGES

    # ── GST on brokerage + txn + sebi ──────────────────────────────────────
    gst = (brokerage + txn + sebi) * _GST_RATE

    # ── Stamp duty (buy side only) ─────────────────────────────────────────
    if not is_buy:
        stamp = Decimal("0")
    elif is_intraday:
        stamp = trade_value * _STAMP_INTRADAY
    else:
        stamp = trade_value * _STAMP_DELIVERY

    # ── DP charges (delivery sell only, flat per scrip regardless of qty) ──
    dp_charges = _DP_CHARGES if (not is_intraday and not is_buy and exchange != Exchange.NFO) else Decimal("0")

    commission = brokerage + stt + txn + gst + sebi + stamp + dp_charges

    # ── Spread cost (half-spread on entry) ─────────────────────────────────
    spread_cost = trade_value * Decimal(str(spread_bps / 10_000 / 2))

    # ── Market impact: empirical square-root law ────────────────────────────
    # impact_bps ≈ 5 * sqrt(participation_rate) * 10
    impact_bps = 5.0 * (volume_participation ** 0.5) * 10
    market_impact = trade_value * Decimal(str(impact_bps / 10_000))

    # ── Slippage (execution timing, 2 bps average) ─────────────────────────
    slippage = trade_value * Decimal("0.0002")

    # ── Short borrow cost ──────────────────────────────────────────────────
    borrow_cost = Decimal("0")
    if is_short and holding_days > 0:
        borrow_cost = trade_value * Decimal("0.15") * Decimal(str(holding_days / 365))

    # ── Overnight financing (leveraged delivery) ───────────────────────────
    overnight_financing = Decimal("0")

    total_cost = (
        commission + spread_cost + market_impact + slippage
        + borrow_cost + overnight_financing
    )
    breakeven_move_pct = float(total_cost / trade_value * 100) if trade_value else 0.0

    return TradeCostEstimate(
        symbol=symbol,
        exchange=exchange,
        quantity=quantity,
        order_type=order_type,
        commission=commission,
        brokerage=brokerage,
        stt=stt,
        dp_charges=dp_charges,
        spread_cost=spread_cost,
        market_impact=market_impact,
        slippage=slippage,
        borrow_cost=borrow_cost,
        overnight_financing=overnight_financing,
        total_cost=total_cost,
        breakeven_move_pct=breakeven_move_pct,
    )


def trade_has_edge(
    expected_edge_pct: float,
    order: Order,
    price: Decimal,
    is_intraday: bool = True,
) -> bool:
    """Returns True only when expected edge > round-trip estimated cost.
    Entry + exit both cost money, so breakeven requires the entry leg's cost
    (this call) plus a matching exit leg — approximated as 2x this estimate."""
    estimate = estimate_trade_cost(
        symbol=order.symbol,
        exchange=order.exchange,
        quantity=order.quantity,
        price=price,
        side=order.side,
        order_type=order.order_type,
        is_intraday=is_intraday,
    )
    round_trip_breakeven = estimate.breakeven_move_pct * 2
    return expected_edge_pct > round_trip_breakeven


def estimate_capital_gains_tax(
    realized_pnl: Decimal,
    is_intraday: bool,
    holding_days: int,
    ltcg_realized_this_fy: Decimal = Decimal("0"),
) -> tuple[Decimal, Decimal]:
    """
    Tax due on a closed position's realized (post-charges) P&L, Indian equity
    rules. Losses aren't taxed here — in reality they offset other gains at
    ITR-filing time, which is outside what a single trade can know.

    Returns (tax_for_this_trade, updated_ltcg_realized_this_fy) — the second
    value must be fed back in as ltcg_realized_this_fy for the next LTCG
    trade this financial year so the ₹1.25L exemption is applied cumulatively
    rather than per-trade.
    """
    if realized_pnl <= 0:
        return Decimal("0"), ltcg_realized_this_fy

    if is_intraday:
        # Speculative business income — taxed at slab rate, not a flat capital
        # gains rate. Assumes the highest slab as a conservative simulation.
        return realized_pnl * _INTRADAY_TAX_RATE, ltcg_realized_this_fy

    if holding_days < 365:
        return realized_pnl * _STCG_RATE, ltcg_realized_this_fy

    # LTCG: ₹1.25L exemption per financial year, applied cumulatively across
    # all LTCG trades booked so far this FY (not reset per trade).
    new_total = ltcg_realized_this_fy + realized_pnl
    taxable_before = max(Decimal("0"), ltcg_realized_this_fy - _LTCG_EXEMPTION_PER_FY)
    taxable_after = max(Decimal("0"), new_total - _LTCG_EXEMPTION_PER_FY)
    tax = (taxable_after - taxable_before) * _LTCG_RATE
    return tax, new_total


def current_financial_year_label(dt) -> str:
    """Indian FY runs April 1 - March 31, e.g. '2026-27'."""
    y = dt.year
    if dt.month >= 4:
        return f"{y}-{(y + 1) % 100:02d}"
    return f"{y - 1}-{y % 100:02d}"
