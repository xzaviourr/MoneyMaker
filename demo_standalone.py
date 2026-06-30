"""
MoneyMaker - Standalone Demo
============================
Runs with ONLY: numpy, pandas, yfinance (already installed).
No credentials or extra packages needed.

Demonstrates:
  1. Regime Classification (ADX + VIX proxy)
  2. Momentum Pod (EMA-9/21 crossover signals)
  3. Mean Reversion Pod (Bollinger Band signals)
  4. Paper Broker simulation with P&L tracking

Run:  python demo_standalone.py
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf


# ?????????????????????????????????????????????????????????????????????????????
# Colour output helpers
# ?????????????????????????????????????????????????????????????????????????????

class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    MAGENTA= "\033[95m"
    BLUE   = "\033[94m"

def header(text: str) -> None:
    print(f"\n{C.BOLD}{C.CYAN}{'='*60}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  {text}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'='*60}{C.RESET}")

def ok(text: str)   -> None: print(f"  {C.GREEN}[OK]{C.RESET}  {text}")
def info(text: str) -> None: print(f"  {C.BLUE}[..]{C.RESET}  {text}")
def warn(text: str) -> None: print(f"  {C.YELLOW}[!!]{C.RESET}  {text}")
def trade(text: str)-> None: print(f"  {C.MAGENTA}[TR]{C.RESET}  {text}")


# ?????????????????????????????????????????????????????????????????????????????
# 1. Fetch data
# ?????????????????????????????????????????????????????????????????????????????

UNIVERSE = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS"]
NIFTY    = "^NSEI"
VIX_INDIA= "^INDIAVIX"

def fetch_data(symbol: str, period: str = "3mo", interval: str = "1d") -> pd.DataFrame:
    try:
        df = yf.download(symbol, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        # Flatten multi-index columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        warn(f"Could not fetch {symbol}: {e}")
        return pd.DataFrame()


# ?????????????????????????????????????????????????????????????????????????????
# 2. Regime Classifier
# ?????????????????????????????????????????????????????????????????????????????

def compute_adx(df: pd.DataFrame, period: int = 14) -> float:
    """True Range / ADX calculation."""
    high = df["High"].values
    low  = df["Low"].values
    close= df["Close"].values
    n = len(close)
    if n < period + 2:
        return 20.0

    tr_list, plus_dm, minus_dm = [], [], []
    for i in range(1, n):
        h_diff = high[i] - high[i-1]
        l_diff = low[i-1] - low[i]
        tr = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
        tr_list.append(tr)
        plus_dm.append( h_diff if h_diff > l_diff and h_diff > 0 else 0.0)
        minus_dm.append(l_diff if l_diff > h_diff and l_diff > 0 else 0.0)

    def smma(vals: list, p: int) -> list:
        result = [sum(vals[:p]) / p]
        for v in vals[p:]:
            result.append((result[-1] * (p-1) + v) / p)
        return result

    atr  = smma(tr_list,   period)
    pdm  = smma(plus_dm,   period)
    ndm  = smma(minus_dm,  period)

    di_plus  = [100 * p / a if a else 0 for p, a in zip(pdm,  atr)]
    di_minus = [100 * n / a if a else 0 for n, a in zip(ndm, atr)]
    dx       = [abs(p - m) / (p + m) * 100 if (p + m) else 0
                for p, m in zip(di_plus, di_minus)]

    if len(dx) < period:
        return 20.0
    adx = sum(dx[-period:]) / period
    return round(adx, 2)


def classify_regime(nifty_df: pd.DataFrame, vix_df: pd.DataFrame) -> dict:
    if nifty_df.empty:
        return {"trend": "UNKNOWN", "risk": "RISK_OFF", "volatility": "NORMAL", "vix": None}

    closes = nifty_df["Close"].values.flatten()
    adx    = compute_adx(nifty_df)

    # SMA trend bias
    sma50  = float(np.mean(closes[-50:])) if len(closes) >= 50 else float(closes[-1])
    sma200 = float(np.mean(closes[-200:]))if len(closes) >=200 else float(closes[-1])
    bias   = "BULLISH" if sma50 > sma200 else "BEARISH"

    # VIX level
    vix_val = None
    if not vix_df.empty:
        vix_val = float(vix_df["Close"].values.flatten()[-1])

    # Classify
    if adx > 25:
        trend = "TRENDING"
    elif adx < 15:
        trend = "CHOPPY"
    else:
        trend = "MEAN_REVERTING"

    volatility = "NORMAL"
    if vix_val:
        if vix_val < 13:
            volatility = "LOW"
        elif vix_val > 20:
            volatility = "HIGH"
        if vix_val > 30:
            volatility = "CRISIS"

    risk = "RISK_ON" if bias == "BULLISH" and (not vix_val or vix_val < 20) else "RISK_OFF"

    return {
        "trend":      trend,
        "risk":       risk,
        "volatility": volatility,
        "bias":       bias,
        "adx":        adx,
        "vix":        vix_val,
        "sma50":      round(sma50, 2),
        "sma200":     round(sma200, 2),
    }


# ?????????????????????????????????????????????????????????????????????????????
# 3. Signal Generation
# ?????????????????????????????????????????????????????????????????????????????

def ema(prices: np.ndarray, period: int) -> np.ndarray:
    k  = 2 / (period + 1)
    em = np.zeros_like(prices, dtype=float)
    em[0] = prices[0]
    for i in range(1, len(prices)):
        em[i] = prices[i] * k + em[i-1] * (1 - k)
    return em


def bollinger(prices: np.ndarray, period: int = 20, k: float = 2.0):
    if len(prices) < period:
        return prices[-1], prices[-1], prices[-1]
    window = prices[-period:]
    mean   = window.mean()
    std    = window.std()
    return mean, mean + k * std, mean - k * std


def rsi(prices: np.ndarray, period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = np.diff(prices[-period-1:])
    gains  = deltas[deltas > 0]
    losses = -deltas[deltas < 0]
    avg_g  = gains.mean()  if len(gains)  > 0 else 0.0
    avg_l  = losses.mean() if len(losses) > 0 else 0.0
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - 100 / (1 + rs)


@dataclass
class Signal:
    symbol:    str
    direction: str   # LONG | SHORT | EXIT
    strategy:  str
    conviction: float
    entry_price: float
    stop_loss:   float
    take_profit: float
    date:        str


def momentum_signals(symbol: str, df: pd.DataFrame) -> list[Signal]:
    """EMA-9/21 crossover signals."""
    if df.empty or len(df) < 25:
        return []
    closes  = df["Close"].values.flatten().astype(float)
    volumes = df["Volume"].values.flatten().astype(float)
    fast = ema(closes, 9)
    slow = ema(closes, 21)
    signals = []
    for i in range(22, len(closes)):
        prev_fast, prev_slow = fast[i-1], slow[i-1]
        curr_fast, curr_slow = fast[i],   slow[i]
        avg_vol = volumes[max(0,i-10):i].mean()
        vol_ok  = volumes[i] > avg_vol * 1.3

        if prev_fast <= prev_slow and curr_fast > curr_slow and vol_ok:
            entry = closes[i]
            signals.append(Signal(
                symbol=symbol, direction="LONG", strategy="EMA_Crossover",
                conviction=0.65, entry_price=entry,
                stop_loss=entry * 0.985, take_profit=entry * 1.02,
                date=str(df.index[i].date() if hasattr(df.index[i], 'date') else df.index[i])[:10],
            ))
        elif prev_fast >= prev_slow and curr_fast < curr_slow and vol_ok:
            entry = closes[i]
            signals.append(Signal(
                symbol=symbol, direction="EXIT", strategy="EMA_Crossover",
                conviction=0.60, entry_price=entry,
                stop_loss=entry * 1.015, take_profit=entry * 0.98,
                date=str(df.index[i].date() if hasattr(df.index[i], 'date') else df.index[i])[:10],
            ))
    return signals


def mean_reversion_signals(symbol: str, df: pd.DataFrame) -> list[Signal]:
    """Bollinger Band mean-reversion signals."""
    if df.empty or len(df) < 25:
        return []
    closes = df["Close"].values.flatten().astype(float)
    signals = []
    for i in range(21, len(closes)):
        window = closes[max(0,i-20):i+1]
        mid, upper, lower = bollinger(window)
        r = rsi(closes[:i+1])
        price = closes[i]
        date  = str(df.index[i].date() if hasattr(df.index[i], 'date') else df.index[i])[:10]

        if price <= lower * 1.001 and r < 35:
            signals.append(Signal(
                symbol=symbol, direction="LONG", strategy="Bollinger_MeanRev",
                conviction=0.60, entry_price=price,
                stop_loss=price * 0.98, take_profit=mid,
                date=date,
            ))
        elif price >= upper * 0.999 and r > 65:
            signals.append(Signal(
                symbol=symbol, direction="SHORT", strategy="Bollinger_MeanRev",
                conviction=0.58, entry_price=price,
                stop_loss=price * 1.02, take_profit=mid,
                date=date,
            ))
    return signals


# ?????????????????????????????????????????????????????????????????????????????
# 4. Paper Broker + Backtest
# ?????????????????????????????????????????????????????????????????????????????

@dataclass
class PaperTrade:
    symbol:     str
    direction:  str
    strategy:   str
    entry_date: str
    entry_price:float
    quantity:   int
    stop_loss:  float
    take_profit:float
    exit_date:  Optional[str] = None
    exit_price: Optional[float] = None
    pnl:        float = 0.0
    status:     str = "OPEN"   # OPEN | CLOSED_TP | CLOSED_SL


@dataclass
class PaperBroker:
    capital: float = 200_000.0
    position_pct: float = 0.05   # 5% of capital per trade
    trades: list[PaperTrade] = field(default_factory=list)
    open_positions: dict[str, PaperTrade] = field(default_factory=dict)

    def process_signal(self, sig: Signal) -> Optional[PaperTrade]:
        if sig.symbol in self.open_positions:
            return None  # already in a position
        if sig.direction not in ("LONG", "SHORT"):
            return None
        # STT + brokerage ~0.15% round-trip cost estimate
        qty = max(1, int(self.capital * self.position_pct / sig.entry_price))
        t   = PaperTrade(
            symbol=sig.symbol, direction=sig.direction, strategy=sig.strategy,
            entry_date=sig.date, entry_price=sig.entry_price,
            quantity=qty, stop_loss=sig.stop_loss, take_profit=sig.take_profit,
        )
        self.open_positions[sig.symbol] = t
        self.trades.append(t)
        return t

    def update_prices(self, symbol: str, current_price: float, date: str) -> None:
        if symbol not in self.open_positions:
            return
        t = self.open_positions[symbol]
        hit_tp = current_price >= t.take_profit if t.direction == "LONG" else current_price <= t.take_profit
        hit_sl = current_price <= t.stop_loss   if t.direction == "LONG" else current_price >= t.stop_loss
        if hit_tp or hit_sl:
            exit_px = t.take_profit if hit_tp else t.stop_loss
            pnl_raw = (exit_px - t.entry_price) * t.quantity
            if t.direction == "SHORT":
                pnl_raw = -pnl_raw
            # deduct 0.1% trading cost
            cost = exit_px * t.quantity * 0.001
            t.pnl        = round(pnl_raw - cost, 2)
            t.exit_date  = date
            t.exit_price = round(exit_px, 2)
            t.status     = "CLOSED_TP" if hit_tp else "CLOSED_SL"
            self.capital += t.pnl
            del self.open_positions[symbol]

    def summary(self) -> dict:
        closed = [t for t in self.trades if t.status != "OPEN"]
        winners = [t for t in closed if t.pnl > 0]
        total_pnl = sum(t.pnl for t in closed)
        win_rate  = len(winners) / len(closed) * 100 if closed else 0
        return {
            "total_trades": len(closed),
            "winners":      len(winners),
            "win_rate_pct": round(win_rate, 1),
            "total_pnl":    round(total_pnl, 2),
            "final_capital":round(self.capital, 2),
            "return_pct":   round(total_pnl / 200_000 * 100, 2),
        }


def run_backtest(symbol: str, df: pd.DataFrame, broker: PaperBroker, signals: list[Signal]) -> None:
    """Feed daily closes through paper broker to process TP/SL hits."""
    closes   = df["Close"].values.flatten().astype(float)
    sig_map  = {s.date: s for s in signals}

    for i, row in enumerate(df.itertuples(), 0):
        date  = str(row.Index.date() if hasattr(row.Index, 'date') else row.Index)[:10]
        close = float(closes[i])

        # Check existing positions for TP/SL
        broker.update_prices(symbol, close, date)

        # Enter new signal if today has one
        if date in sig_map:
            t = broker.process_signal(sig_map[date])
            if t:
                trade(f"[{date}] {symbol} {t.direction:5s} @ ?{t.entry_price:.2f}  "
                      f"qty={t.quantity}  TP=?{t.take_profit:.2f}  SL=?{t.stop_loss:.2f}")


# ?????????????????????????????????????????????????????????????????????????????
# 5. Main
# ?????????????????????????????????????????????????????????????????????????????

def main() -> None:
    print(f"\n{C.BOLD}{'='*60}")
    print("  MoneyMaker - Demo Mode")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}{C.RESET}")

    # ?? Step 1: Regime classification ????????????????????????????????????????
    header("STEP 1 - Market Regime Classification")
    info("Fetching Nifty 50 + India VIX from Yahoo Finance...")
    nifty_df = fetch_data(NIFTY, period="1y")
    vix_df   = fetch_data(VIX_INDIA, period="1mo")
    regime   = classify_regime(nifty_df, vix_df)

    ok(f"Regime -> Trend: {C.BOLD}{regime['trend']}{C.RESET}  |  "
       f"Risk: {regime['risk']}  |  Volatility: {regime['volatility']}")
    ok(f"ADX = {regime['adx']}  |  VIX = {regime['vix']}  |  Bias = {regime['bias']}")
    ok(f"SMA-50 = ?{regime['sma50']:,.0f}  |  SMA-200 = ?{regime['sma200']:,.0f}")

    # ?? Step 2: Pod signal generation ????????????????????????????????????????
    header("STEP 2 - Pod Signal Generation (3-month backtest data)")
    all_signals: list[Signal] = []
    for sym_ns in UNIVERSE:
        sym_short = sym_ns.replace(".NS", "")
        info(f"Fetching {sym_short} data...")
        df = fetch_data(sym_ns, period="3mo")
        if df.empty:
            warn(f"No data for {sym_short}, skipping.")
            continue

        mom_sigs = momentum_signals(sym_ns, df)
        mr_sigs  = mean_reversion_signals(sym_ns, df)
        total    = len(mom_sigs) + len(mr_sigs)
        ok(f"{sym_short}: EMA crossover={len(mom_sigs)} signals  |  "
           f"Bollinger={len(mr_sigs)} signals  |  total={total}")
        all_signals.extend(mom_sigs)
        all_signals.extend(mr_sigs)

    print(f"\n  {C.BOLD}Total signals across universe: {len(all_signals)}{C.RESET}")

    # ?? Step 3: Paper broker backtest ?????????????????????????????????????????
    header("STEP 3 - Paper Broker Simulation (?2,00,000 starting capital)")
    broker = PaperBroker(capital=200_000.0)

    for sym_ns in UNIVERSE:
        sym_signals = [s for s in all_signals if s.symbol == sym_ns]
        if not sym_signals:
            continue
        df = fetch_data(sym_ns, period="3mo")
        if df.empty:
            continue
        info(f"Replaying {len(sym_signals)} signals for {sym_ns.replace('.NS','')}...")
        run_backtest(sym_ns, df, broker, sym_signals)

    # ?? Step 4: Results ???????????????????????????????????????????????????????
    header("STEP 4 - Backtest Results")
    s = broker.summary()

    status_color = C.GREEN if s["total_pnl"] >= 0 else C.RED
    print(f"""
  {C.BOLD}Capital:        {C.RESET}?2,00,000  ->  {status_color}?{s['final_capital']:,.2f}{C.RESET}
  {C.BOLD}Total trades:   {C.RESET}{s['total_trades']}
  {C.BOLD}Winners:        {C.RESET}{C.GREEN}{s['winners']}{C.RESET}  /  {s['total_trades']}
  {C.BOLD}Win rate:       {C.RESET}{s['win_rate_pct']}%
  {C.BOLD}Net P&L:        {C.RESET}{status_color}?{s['total_pnl']:,.2f}{C.RESET}
  {C.BOLD}Return:         {C.RESET}{status_color}{s['return_pct']}%{C.RESET}
""")

    # ?? Step 5: Show what the full system adds ?????????????????????????????????
    header("STEP 5 - Full System Architecture (not active in demo)")
    layers = [
        ("Layer 0 - Data Foundation",   "DataSentinel, RegimeClassifier, CircuitBreaker"),
        ("Layer 1 - Pillar 1 (Pods)",    "MomentumPod, BreakoutPod, MeanReversionPod (+ 5 more)"),
        ("Layer 2 - Pillar 2 (LT Desk)", "15 strategy agents -> 3-Room deliberation -> LLM approval"),
        ("Layer 3 - Portfolio Guardian", "Hedging, liquidation, correlation risk"),
        ("Supervisor",                   "FirmCIO, PodSupervisor, AlphaDecayMonitor"),
        ("Feedback Loop",                "TradeAttribution, Bayesian calibration, LLM review"),
        ("API + UI",                     "FastAPI + WebSocket + React dashboard"),
    ]
    for name, detail in layers:
        print(f"  {C.CYAN}*{C.RESET}  {C.BOLD}{name}{C.RESET}")
        print(f"       {detail}")

    print(f"\n{C.BOLD}{C.GREEN}Demo complete! The architecture is fully implemented.{C.RESET}")
    print(f"  To run the full system: {C.YELLOW}pip install -r requirements.txt{C.RESET}")
    print(f"  Then:                   {C.YELLOW}python main.py --demo{C.RESET}")
    print()


if __name__ == "__main__":
    main()
