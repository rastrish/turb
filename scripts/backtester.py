"""
FNO Options Strategy Backtester
Replays the 11-point entry checklist on historical daily data and estimates
option P&L using Black-Scholes. OI/PCR checks (rules 1, 2, 5) cannot be
replayed without historical option chain data — those 3 are skipped and the
limitation is flagged in every report.

WORKFLOW:
  Step 1 — Claude calls data_collector.py instructions to fetch data via Groww MCP
  Step 2 — Data is saved to data/backtest/{SYMBOL}.json
  Step 3 — Run: python scripts/backtester.py --symbol HINDALCO --months 6

TESTABLE CONDITIONS (6 of 11):
  ✅  #3  Price up >= threshold
  ✅  #4  Volume > 2x 20-day average
  ✅  #7  Not at day high (close < high * 0.995)
  ✅  #10 SuperTrend: UP
  ✅  #11 RSI: 45–70
  ✅  #8  Capital < ₹25,000 (via Black-Scholes estimate)

NOT TESTABLE (historical OI/PCR data not available):
  ❌  #1  New contracts >= 200
  ❌  #2  OI direction: Building
  ❌  #5  PCR >= 0.75
  ⏭   #6  Time window (not relevant for daily backtest)
  ⏭   #9  Position count (simulated: unlimited in backtest)
"""

from __future__ import annotations
import json
import math
import argparse
import statistics
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "backtest"
CONFIG_DIR = ROOT / "config"

RISK_FREE_RATE = 0.065       # 6.5% — RBI repo rate proxy
EXPIRY_DATE = date(2026, 6, 30)
MAX_CAPITAL = 25_000
SL_MULT = 0.65               # SL = entry premium × 0.65
T1_MULT = 1.75               # T1 = entry premium × 1.75
T2_MULT = 2.30               # T2 = entry premium × 2.30
DEFAULT_IV = 0.40            # 40% IV fallback when hist vol unavailable


# ── Black-Scholes ─────────────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def black_scholes_call(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """European call price. T in years. Returns 0 if T <= 0."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def historical_vol(closes: list[float], window: int = 20) -> float:
    """Annualised volatility from log returns over last `window` days."""
    if len(closes) < window + 1:
        return DEFAULT_IV
    recent = closes[-(window + 1):]
    log_returns = [math.log(recent[i] / recent[i - 1]) for i in range(1, len(recent))]
    return statistics.stdev(log_returns) * math.sqrt(252)


def days_to_expiry(entry_date: date) -> float:
    """Calendar days remaining to expiry, converted to years."""
    delta = (EXPIRY_DATE - entry_date).days
    return max(delta / 365.0, 1 / 365.0)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Candle:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class IndicatorRow:
    date: date
    rsi: float
    supertrend_dir: float   # 1.0 = UP, -1.0 = DOWN
    adx: float


@dataclass
class BacktestConfig:
    symbol: str
    lot_size: int
    min_price_move_pct: float = 1.5
    rsi_min: float = 45.0
    rsi_max: float = 70.0
    min_adx: float = 20.0
    max_holding_days: int = 5
    strike_otm_pct: float = 0.02     # 2% OTM strike (e.g. spot 1000 → strike 1020)
    volume_multiplier: float = 2.0
    volume_avg_window: int = 20


@dataclass
class Signal:
    date: date
    spot: float
    strike: int
    entry_premium: float
    sl: float
    t1: float
    t2: float
    capital: float
    hist_vol: float
    checks_passed: list[str]
    checks_failed: list[str]


@dataclass
class BacktestTrade:
    signal: Signal
    outcome: str            # "T1_HIT", "T2_HIT", "SL_HIT", "EXPIRED", "OPEN"
    exit_premium: float
    exit_date: date
    holding_days: int
    pnl_per_lot: float
    pnl_total: float
    pnl_pct: float


@dataclass
class BacktestResults:
    symbol: str
    period_start: date
    period_end: date
    total_signals: int
    trades: list[BacktestTrade] = field(default_factory=list)

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def winners(self) -> list[BacktestTrade]:
        return [t for t in self.trades if t.pnl_total > 0]

    @property
    def losers(self) -> list[BacktestTrade]:
        return [t for t in self.trades if t.pnl_total <= 0]

    @property
    def win_rate(self) -> float:
        return len(self.winners) / self.total_trades * 100 if self.total_trades else 0

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl_total for t in self.trades)

    @property
    def avg_winner(self) -> float:
        return statistics.mean(t.pnl_total for t in self.winners) if self.winners else 0

    @property
    def avg_loser(self) -> float:
        return statistics.mean(t.pnl_total for t in self.losers) if self.losers else 0

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t.pnl_total for t in self.winners)
        gross_loss = abs(sum(t.pnl_total for t in self.losers))
        return gross_profit / gross_loss if gross_loss > 0 else float("inf")

    @property
    def max_consecutive_losses(self) -> int:
        max_streak = cur = 0
        for t in self.trades:
            cur = cur + 1 if t.pnl_total <= 0 else 0
            max_streak = max(max_streak, cur)
        return max_streak

    @property
    def avg_holding_days(self) -> float:
        return statistics.mean(t.holding_days for t in self.trades) if self.trades else 0


# ── Data loading ──────────────────────────────────────────────────────────────

def load_data(symbol: str) -> tuple[list[Candle], list[IndicatorRow]]:
    """
    Load candles and indicators from data/backtest/{SYMBOL}.json
    Expected format — see data_collector.py for how to populate.
    """
    path = DATA_DIR / f"{symbol.upper()}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No data file found: {path}\n"
            f"Run data_collector.py instructions to fetch data first.\n"
            f"Or ask Claude: 'Fetch backtest data for {symbol}'"
        )
    raw = json.loads(path.read_text())

    candles = []
    for row in raw.get("candles", []):
        candles.append(Candle(
            date=date.fromisoformat(row["date"]),
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"],
        ))

    indicators = []
    for row in raw.get("indicators", []):
        indicators.append(IndicatorRow(
            date=date.fromisoformat(row["date"]),
            rsi=row.get("rsi_14", 50.0),
            supertrend_dir=row.get("supertrend_direction", 1.0),
            adx=row.get("adx_14", 0.0),
        ))

    candles.sort(key=lambda c: c.date)
    indicators.sort(key=lambda i: i.date)
    return candles, indicators


# ── Signal detection ──────────────────────────────────────────────────────────

def detect_signals(
    candles: list[Candle],
    indicators: list[IndicatorRow],
    config: BacktestConfig,
) -> list[Signal]:
    """
    Find every day that would have triggered a valid entry per our
    testable checklist conditions.
    """
    ind_map = {i.date: i for i in indicators}
    signals = []

    for idx, candle in enumerate(candles):
        if idx < config.volume_avg_window:
            continue  # not enough history for vol average
        if candle.date >= EXPIRY_DATE:
            continue

        ind = ind_map.get(candle.date)
        if not ind:
            continue

        prev_close = candles[idx - 1].close
        price_move_pct = (candle.close - prev_close) / prev_close * 100

        avg_vol = statistics.mean(c.volume for c in candles[idx - config.volume_avg_window:idx])
        volume_ratio = candle.volume / avg_vol if avg_vol > 0 else 0

        near_day_high = candle.close >= candle.high * 0.995

        closes_so_far = [c.close for c in candles[:idx + 1]]
        hvol = historical_vol(closes_so_far)

        T = days_to_expiry(candle.date)
        strike = int(round(candle.close * (1 + config.strike_otm_pct) / 50) * 50)
        premium = black_scholes_call(candle.close, strike, T, RISK_FREE_RATE, hvol)
        capital = premium * config.lot_size

        checks_passed = []
        checks_failed = []

        def chk(name: str, passed: bool):
            (checks_passed if passed else checks_failed).append(name)
            return passed

        chk(f"price +{price_move_pct:.1f}%", price_move_pct >= config.min_price_move_pct)
        chk(f"volume {volume_ratio:.1f}x", volume_ratio >= config.volume_multiplier)
        chk("not_at_day_high", not near_day_high)
        chk(f"supertrend_UP", ind.supertrend_dir == 1.0)
        chk(f"RSI {ind.rsi:.1f}", config.rsi_min <= ind.rsi <= config.rsi_max)
        chk(f"capital ₹{capital:,.0f}", capital <= MAX_CAPITAL)

        if len(checks_failed) == 0:
            signals.append(Signal(
                date=candle.date,
                spot=candle.close,
                strike=strike,
                entry_premium=round(premium, 2),
                sl=round(premium * SL_MULT, 2),
                t1=round(premium * T1_MULT, 2),
                t2=round(premium * T2_MULT, 2),
                capital=round(capital, 2),
                hist_vol=round(hvol, 4),
                checks_passed=checks_passed,
                checks_failed=checks_failed,
            ))

    return signals


# ── Trade simulation ──────────────────────────────────────────────────────────

def simulate_trade(
    signal: Signal,
    candles: list[Candle],
    config: BacktestConfig,
) -> BacktestTrade:
    """
    Simulate holding the CE position from signal.date.
    Exit logic:
      - Check each subsequent day's high → if premium equivalent hits T2, exit
      - Check each subsequent day's low → if premium equivalent hits SL, exit
      - After max_holding_days → exit at close
    Premium is re-priced with Black-Scholes on each day.
    """
    future = [c for c in candles if c.date > signal.date]
    closes_before = [c.close for c in candles if c.date <= signal.date]

    for day_idx, candle in enumerate(future[:config.max_holding_days], 1):
        T = days_to_expiry(candle.date)
        hvol = historical_vol(closes_before + [candle.close])

        # Intraday high → check T2 and T1 hit
        p_high = black_scholes_call(candle.high, signal.strike, T, RISK_FREE_RATE, hvol)
        p_low = black_scholes_call(candle.low, signal.strike, T, RISK_FREE_RATE, hvol)
        p_close = black_scholes_call(candle.close, signal.strike, T, RISK_FREE_RATE, hvol)

        # T2 hit
        if p_high >= signal.t2:
            exit_p = signal.t2
            return _make_trade(signal, "T2_HIT", exit_p, candle.date, day_idx, config)

        # T1 hit
        if p_high >= signal.t1:
            exit_p = signal.t1
            return _make_trade(signal, "T1_HIT", exit_p, candle.date, day_idx, config)

        # SL hit (intraday low)
        if p_low <= signal.sl:
            exit_p = signal.sl
            return _make_trade(signal, "SL_HIT", exit_p, candle.date, day_idx, config)

        closes_before.append(candle.close)

    # Max holding days reached — exit at close of last day
    last = future[config.max_holding_days - 1] if len(future) >= config.max_holding_days else future[-1]
    T = days_to_expiry(last.date)
    hvol = historical_vol(closes_before)
    p_close = black_scholes_call(last.close, signal.strike, T, RISK_FREE_RATE, hvol)
    return _make_trade(signal, "EXPIRED", p_close, last.date, config.max_holding_days, config)


def _make_trade(
    signal: Signal, outcome: str, exit_p: float,
    exit_date: date, holding_days: int, config: BacktestConfig,
) -> BacktestTrade:
    pnl_per_lot = round(exit_p - signal.entry_premium, 2)
    pnl_total = round(pnl_per_lot * config.lot_size, 2)
    pnl_pct = round(pnl_per_lot / signal.entry_premium * 100, 1) if signal.entry_premium > 0 else 0
    return BacktestTrade(
        signal=signal, outcome=outcome, exit_premium=exit_p,
        exit_date=exit_date, holding_days=holding_days,
        pnl_per_lot=pnl_per_lot, pnl_total=pnl_total, pnl_pct=pnl_pct,
    )


# ── Full backtest run ─────────────────────────────────────────────────────────

def run_backtest(symbol: str, config: BacktestConfig) -> BacktestResults:
    candles, indicators = load_data(symbol)
    if not candles:
        raise ValueError(f"No candle data loaded for {symbol}")

    signals = detect_signals(candles, indicators, config)
    results = BacktestResults(
        symbol=symbol,
        period_start=candles[0].date,
        period_end=candles[-1].date,
        total_signals=len(signals),
    )

    candle_map = {c.date: c for c in candles}
    for sig in signals:
        trade = simulate_trade(sig, candles, config)
        results.trades.append(trade)

    return results


# ── Report formatting ─────────────────────────────────────────────────────────

def format_report(results: BacktestResults) -> str:
    r = results
    lines = [
        "",
        f"{'='*60}",
        f"  BACKTEST REPORT — {r.symbol}",
        f"  Period : {r.period_start} → {r.period_end}",
        f"  Expiry : {EXPIRY_DATE}",
        f"{'='*60}",
        f"  Signals detected  : {r.total_signals}",
        f"  Trades simulated  : {r.total_trades}",
        f"  ─────────────────────────────────────────────────────",
        f"  Win rate          : {r.win_rate:.1f}%",
        f"  Profit factor     : {r.profit_factor:.2f}",
        f"  Net P&L           : ₹{r.total_pnl:+,.0f}",
        f"  Avg winner        : ₹{r.avg_winner:+,.0f}",
        f"  Avg loser         : ₹{r.avg_loser:+,.0f}",
        f"  Avg hold (days)   : {r.avg_holding_days:.1f}",
        f"  Max consec losses : {r.max_consecutive_losses}",
        f"{'='*60}",
        f"  OUTCOME BREAKDOWN:",
    ]

    outcomes = {}
    for t in r.trades:
        outcomes[t.outcome] = outcomes.get(t.outcome, 0) + 1
    for k, v in sorted(outcomes.items()):
        lines.append(f"    {k:12s}: {v:3d}  ({v/r.total_trades*100:.0f}%)")

    if r.trades:
        best = max(r.trades, key=lambda t: t.pnl_total)
        worst = min(r.trades, key=lambda t: t.pnl_total)
        lines += [
            f"{'='*60}",
            f"  BEST TRADE  : {best.signal.date} | {best.signal.symbol if hasattr(best.signal,'symbol') else r.symbol} "
            f"{best.signal.strike} CE | +₹{best.pnl_total:,.0f} ({best.outcome})",
            f"  WORST TRADE : {worst.signal.date} | {r.symbol} "
            f"{worst.signal.strike} CE | ₹{worst.pnl_total:,.0f} ({worst.outcome})",
        ]

    lines += [
        f"{'='*60}",
        f"  TRADE LOG (chronological):",
        f"  {'Date':12s} {'Strike':>7} {'Entry':>7} {'Exit':>7} {'P&L/lot':>9} "
        f"{'Total P&L':>12} {'Days':>5} {'Outcome'}",
        f"  {'─'*80}",
    ]
    for t in r.trades:
        icon = "✅" if t.pnl_total > 0 else "❌"
        lines.append(
            f"  {icon} {str(t.signal.date):12s} {t.signal.strike:>7} "
            f"₹{t.signal.entry_premium:>6.1f} ₹{t.exit_premium:>6.1f} "
            f"{t.pnl_per_lot:>+8.1f} ₹{t.pnl_total:>+10,.0f} "
            f"{t.holding_days:>4}d  {t.outcome}"
        )

    lines += [
        f"{'='*60}",
        f"  ⚠️  NOTE: OI/PCR checks (#1, #2, #5) not included.",
        f"  Live results will differ — OI confirmation filters",
        f"  out low-conviction signals not captured here.",
        f"{'='*60}",
    ]
    return "\n".join(lines)


def format_condition_analysis(results: BacktestResults) -> str:
    """Shows which individual conditions have highest predictive power."""
    condition_outcomes: dict[str, list[float]] = {}

    for t in results.trades:
        for cond in t.signal.checks_passed:
            condition_outcomes.setdefault(cond, []).append(t.pnl_total)

    lines = [
        f"\n{'='*60}",
        f"  CONDITION ANALYSIS — {results.symbol}",
        f"  (all trades passed all conditions — this shows which",
        f"   conditions are hardest to satisfy on signal days)",
        f"{'='*60}",
        f"  {'Condition':30s} {'Signals':>8} {'Avg P&L':>10}",
        f"  {'─'*55}",
    ]
    for cond, pnls in sorted(condition_outcomes.items(), key=lambda x: -len(x[1])):
        avg = sum(pnls) / len(pnls) if pnls else 0
        lines.append(f"  {cond:30s} {len(pnls):>8}  ₹{avg:>+9,.0f}")
    lines.append(f"{'='*60}")
    return "\n".join(lines)


# ── Multi-symbol comparison ───────────────────────────────────────────────────

def run_multi_backtest(symbols_config: list[tuple[str, BacktestConfig]]) -> str:
    """Run backtest for multiple symbols and output comparison table."""
    rows = []
    for symbol, config in symbols_config:
        try:
            r = run_backtest(symbol, config)
            rows.append((symbol, r))
        except FileNotFoundError as e:
            rows.append((symbol, None))

    lines = [
        f"\n{'='*75}",
        f"  MULTI-SYMBOL BACKTEST COMPARISON",
        f"{'='*75}",
        f"  {'Symbol':12} {'Trades':>7} {'Win%':>6} {'PF':>6} "
        f"{'Net P&L':>12} {'Avg W':>10} {'Avg L':>10}",
        f"  {'─'*70}",
    ]
    for symbol, r in rows:
        if r is None:
            lines.append(f"  {symbol:12}  NO DATA — fetch first")
            continue
        lines.append(
            f"  {symbol:12} {r.total_trades:>7} {r.win_rate:>5.1f}% "
            f"{r.profit_factor:>6.2f} ₹{r.total_pnl:>+10,.0f} "
            f"₹{r.avg_winner:>+8,.0f} ₹{r.avg_loser:>+8,.0f}"
        )
    lines.append(f"{'='*75}")
    return "\n".join(lines)


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FNO Options Strategy Backtester")
    parser.add_argument("--symbol", required=True, help="Stock symbol e.g. HINDALCO")
    parser.add_argument("--lot-size", type=int, required=True, help="Lot size for this symbol")
    parser.add_argument("--min-move", type=float, default=1.5, help="Min price move %% to trigger (default 1.5)")
    parser.add_argument("--hold-days", type=int, default=5, help="Max holding days (default 5)")
    parser.add_argument("--otm-pct", type=float, default=0.02, help="OTM %% for strike (default 0.02)")
    parser.add_argument("--volume-mult", type=float, default=2.0, help="Volume multiplier threshold (default 2.0)")
    parser.add_argument("--rsi-min", type=float, default=45.0, help="RSI lower bound (default 45)")
    parser.add_argument("--rsi-max", type=float, default=70.0, help="RSI upper bound (default 70)")
    parser.add_argument("--compare", nargs="+", help="Compare multiple symbols (needs lot sizes too)")
    args = parser.parse_args()

    config = BacktestConfig(
        symbol=args.symbol.upper(),
        lot_size=args.lot_size,
        min_price_move_pct=args.min_move,
        max_holding_days=args.hold_days,
        strike_otm_pct=args.otm_pct,
        volume_multiplier=args.volume_mult,
        rsi_min=args.rsi_min,
        rsi_max=args.rsi_max,
    )

    results = run_backtest(args.symbol.upper(), config)
    print(format_report(results))
    print(format_condition_analysis(results))
