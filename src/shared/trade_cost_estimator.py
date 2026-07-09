"""
Realistic Indian equity trade-cost model (NSE/BSE).
Every pod strategy calls estimate_trade_cost() before declaring a trade viable.
A trade with expected edge < total cost is rejected automatically.
"""
from __future__ import annotations

from decimal import Decimal

from .schemas import Exchange, Order, OrderType, TradeCostEstimate

# ── NSE/BSE statutory charges (approximate, FY 2025-26) ──────────────────────
_BROKERAGE_FLAT   = Decimal("20")          # ₹20 flat (discount broker)
_BROKERAGE_MAX_PCT = Decimal("0.0003")     # 0.03% cap

# Securities Transaction Tax
_STT_DELIVERY_BUY   = Decimal("0.001")    # 0.1% on buy (delivery)
_STT_DELIVERY_SELL  = Decimal("0.001")    # 0.1% on sell (delivery)
_STT_INTRADAY_SELL  = Decimal("0.00025")  # 0.025% on sell only (intraday)
_STT_FO_SELL        = Decimal("0.0125")   # 1.25% on sell premium (F&O)

# Exchange transaction charges
_NSE_EQ_TXN   = Decimal("0.0000297")      # ₹2.97 per lakh
_BSE_EQ_TXN   = Decimal("0.0000375")

# Other
_GST_RATE     = Decimal("0.18")
_SEBI_CHARGES = Decimal("0.000001")       # ₹10 per crore
_STAMP_INTRADAY = Decimal("0.00003")      # 0.003% on buy side
_STAMP_DELIVERY = Decimal("0.00015")      # 0.015% on buy side


def estimate_trade_cost(
    symbol: str,
    exchange: Exchange,
    quantity: int,
    price: Decimal,
    order_type: OrderType = OrderType.LIMIT,
    is_intraday: bool = True,
    is_short: bool = False,
    holding_days: int = 0,
    spread_bps: float = 5.0,
    volume_participation: float = 0.01,
) -> TradeCostEstimate:
    """
    Returns a full cost breakdown including commissions, STT, impact & slippage.
    """
    trade_value = price * Decimal(str(quantity))

    # ── Brokerage ──────────────────────────────────────────────────────────
    # Paper broker charges flat ₹20 per order regardless of trade size.
    # For live trading (5Paisa/Zerodha) the cap kicks in on large orders.
    brokerage = _BROKERAGE_FLAT

    # ── STT ────────────────────────────────────────────────────────────────
    if exchange == Exchange.NFO:
        stt = trade_value * _STT_FO_SELL
    elif is_intraday:
        stt = trade_value * _STT_INTRADAY_SELL
    else:
        stt = trade_value * (_STT_DELIVERY_BUY + _STT_DELIVERY_SELL)

    # ── Exchange transaction charge ────────────────────────────────────────
    if exchange == Exchange.BSE:
        txn = trade_value * _BSE_EQ_TXN
    else:
        txn = trade_value * _NSE_EQ_TXN

    # ── GST on brokerage + txn ─────────────────────────────────────────────
    gst = (brokerage + txn) * _GST_RATE

    # ── SEBI charges ───────────────────────────────────────────────────────
    sebi = trade_value * _SEBI_CHARGES

    # ── Stamp duty ─────────────────────────────────────────────────────────
    if is_intraday:
        stamp = trade_value * _STAMP_INTRADAY
    else:
        stamp = trade_value * _STAMP_DELIVERY

    commission = brokerage + stt + txn + gst + sebi + stamp

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
    if not is_intraday and holding_days > 0:
        # assumed leverage financing rate ~12% p.a.
        overnight_financing = Decimal("0")   # broker-specific; placeholder

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
    Entry + exit both cost money, so breakeven requires 2× the one-way cost."""
    estimate = estimate_trade_cost(
        symbol=order.symbol,
        exchange=order.exchange,
        quantity=order.quantity,
        price=price,
        order_type=order.order_type,
        is_intraday=is_intraday,
    )
    round_trip_breakeven = estimate.breakeven_move_pct * 2
    return expected_edge_pct > round_trip_breakeven
