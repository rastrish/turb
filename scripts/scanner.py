"""
FNO Options Scanner — connects to Groww MCP and runs the 11-point checklist
Usage: Run interactively via Claude Code, or call scan_all() directly
"""

from __future__ import annotations
import json
import os
from datetime import datetime, time as dtime
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

ROOT = Path(__file__).parent.parent
CONFIG_DIR = ROOT / "config"
TRACKER_DIR = ROOT / "tracker"

RULES = json.loads((CONFIG_DIR / "rules.json").read_text())
CHECKLIST = json.loads((CONFIG_DIR / "checklist.json").read_text())
FILTERS = json.loads((CONFIG_DIR / "filters.json").read_text())
SCAN_CRITERIA = json.loads((CONFIG_DIR / "scan_criteria.json").read_text())

JUNE_EXPIRY = "2026-06-30"
MAX_CAPITAL_PER_TRADE = 25_000
MAX_DAILY_LOSS = 5_000
MAX_POSITIONS = 3
MIN_NEW_CONTRACTS = 200
MIN_PCR = 0.75
# Hard boundaries — no entry ever outside these
HARD_START = dtime(9, 30)
HARD_STOP = dtime(15, 15)
# Named windows per Rule 3
EARLY_WINDOW_END = dtime(10, 0)        # 9:30–10:00 = strong signals only
IDEAL_WINDOW_START = dtime(10, 0)      # 10:00–11:30 = best window
IDEAL_WINDOW_END = dtime(11, 30)
GOOD_WINDOW_END = dtime(15, 0)         # 11:30–15:00 = good signals
BTST_WINDOW_START = dtime(15, 0)       # 15:00–15:15 = BTST only
BTST_WINDOW_END = dtime(15, 15)
# Strong-signal thresholds for the 9:30–10:00 early window
EARLY_MIN_CONTRACTS = 500
EARLY_MIN_PCR = 0.85


@dataclass
class StockData:
    symbol: str
    ltp: float = 0.0
    day_high: float = 0.0
    day_low: float = 0.0
    prev_close: float = 0.0
    volume: float = 0.0
    avg_volume: float = 0.0
    sector: str = ""
    lot_size: int = 0
    # OI data
    ce_oi: int = 0
    ce_oi_change: int = 0
    pcr: float = 0.0
    new_contracts: int = 0
    # Technical indicators
    supertrend: str = ""
    rsi: float = 0.0
    adx: float = 0.0
    # Option specific
    strike: int = 0
    premium: float = 0.0
    premium_change_pct: float = 0.0


@dataclass
class ChecklistResult:
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    score: int = 0


@dataclass
class TradeCard:
    symbol: str
    instrument: str
    strike: int
    new_contracts: int
    oi_change_pct: float
    volume_ratio: float
    price_change_pct: float
    pcr: float
    supertrend: str
    rsi: float
    premium: float
    ideal_entry: float
    spot_price: float
    wait_for_pullback: bool
    sl: float
    t1: float
    t2: float
    capital: float
    score: float
    checklist_passed: bool
    failures: list[str]
    warnings: list[str]


def load_positions() -> dict:
    path = TRACKER_DIR / "positions.json"
    return json.loads(path.read_text()) if path.exists() else {"open_positions": []}


def get_open_position_count() -> int:
    data = load_positions()
    return len([p for p in data.get("open_positions", []) if p.get("status") == "OPEN"])


def get_daily_loss() -> float:
    """Sum realized losses from today's closed trades in trade_history.json"""
    path = TRACKER_DIR / "trade_history.json"
    if not path.exists():
        return 0.0
    history = json.loads(path.read_text())
    today = datetime.now().strftime("%Y-%m-%d")
    total = 0.0
    for trade in history.get("closed_trades", []):
        if trade.get("date") == today and trade.get("pnl", 0) < 0:
            total += abs(trade["pnl"])
    return total


def now_time() -> dtime:
    return datetime.now().time().replace(second=0, microsecond=0)


def get_time_window(t: dtime | None = None) -> str:
    """Returns the current time window label per Rule 3."""
    t = t or now_time()
    if t < HARD_START:
        return "NO_ENTRY_PRE_MARKET"
    if t < EARLY_WINDOW_END:
        return "STRONG_SIGNALS_ONLY"     # 9:30–10:00
    if t <= IDEAL_WINDOW_END:
        return "IDEAL"                   # 10:00–11:30
    if t < BTST_WINDOW_START:
        return "GOOD"                    # 11:30–15:00
    if t <= BTST_WINDOW_END:
        return "BTST_ONLY"              # 15:00–15:15
    return "NO_ENTRY_AFTER_CLOSE"


def in_ideal_window() -> bool:
    return get_time_window() == "IDEAL"


def in_valid_window() -> bool:
    w = get_time_window()
    return w not in ("NO_ENTRY_PRE_MARKET", "NO_ENTRY_AFTER_CLOSE")


def run_filters(stock: StockData, daily_loss: float, open_positions: int) -> tuple[bool, list[str]]:
    """Returns (rejected, reasons). If rejected=True, skip this stock."""
    rejections = []

    if stock.pcr < MIN_PCR:
        rejections.append(f"PCR {stock.pcr:.2f} < 0.75 minimum")

    if stock.supertrend.upper() == "DOWN":
        rejections.append("SuperTrend is DOWN — CE entry invalid")

    if stock.new_contracts < MIN_NEW_CONTRACTS:
        rejections.append(f"New contracts {stock.new_contracts} < 200 minimum")

    if stock.premium_change_pct > 30:
        rejections.append(f"Premium up {stock.premium_change_pct:.1f}% today — wait for pullback")

    if stock.lot_size > 2500:
        rejections.append(f"Lot size {stock.lot_size} > 2,500 — manual review required")

    if daily_loss >= MAX_DAILY_LOSS:
        rejections.append(f"Daily loss ₹{daily_loss:,.0f} >= ₹5,000 limit — HALT ALL TRADING")

    t = now_time()
    window = get_time_window(t)
    if window == "NO_ENTRY_PRE_MARKET":
        rejections.append(f"Time {t.strftime('%H:%M')} — before 9:30 AM, no entry ever")
    elif window == "NO_ENTRY_AFTER_CLOSE":
        rejections.append(f"Time {t.strftime('%H:%M')} — after 3:15 PM, no entry ever")

    return len(rejections) > 0, rejections


def run_checklist(stock: StockData, daily_loss: float, open_positions: int) -> ChecklistResult:
    checks = {}
    failures = []
    warnings = []
    t = now_time()
    price_change_pct = ((stock.ltp - stock.prev_close) / stock.prev_close * 100) if stock.prev_close else 0
    volume_ratio = (stock.volume / stock.avg_volume) if stock.avg_volume else 0
    required_capital = stock.premium * stock.lot_size
    near_day_high = stock.ltp >= stock.day_high * 0.995

    window = get_time_window(t)
    # Time check: IDEAL and GOOD windows pass outright.
    # STRONG_SIGNALS_ONLY (9:30–10:00) passes only if OI >500 AND PCR >0.85.
    # BTST_ONLY passes only when checking separately — treated as fail here
    # (scanner handles BTST as a separate path, not part of the standard 11-point run).
    if window == "IDEAL" or window == "GOOD":
        time_check = True
    elif window == "STRONG_SIGNALS_ONLY":
        time_check = stock.new_contracts >= EARLY_MIN_CONTRACTS and stock.pcr >= EARLY_MIN_PCR
    else:
        time_check = False  # BTST_ONLY or hard boundaries

    check_map = {
        "new_contracts >= 200": stock.new_contracts >= MIN_NEW_CONTRACTS,
        "oi_direction: BUILDING": stock.ce_oi_change > 0,
        "volume > 2x avg": volume_ratio >= 2.0,
        "price direction: UP": price_change_pct > 0,
        "PCR >= 0.75": stock.pcr >= MIN_PCR,
        f"time window ({window})": time_check,
        "not at day high": not near_day_high,
        "capital < ₹25K": required_capital <= MAX_CAPITAL_PER_TRADE,
        "positions < 3": open_positions < MAX_POSITIONS,
        "supertrend: UP": stock.supertrend.upper() == "UP",
        "rsi 45-70": 45 <= stock.rsi <= 70,
    }

    score = 0
    for label, result in check_map.items():
        checks[label] = result
        if result:
            score += 1
        else:
            failures.append(label)

    if daily_loss >= MAX_DAILY_LOSS * 0.70:
        warnings.append(f"⚠️ Daily loss ₹{daily_loss:,.0f} approaching ₹5,000 limit")
    if window == "STRONG_SIGNALS_ONLY":
        warnings.append(f"⚠️ Early window (9:30–10:00) — requires OI >500 + PCR >0.85 + strong move")
    elif window == "GOOD":
        warnings.append(f"⚠️ Time {t.strftime('%H:%M')} — outside ideal 10:00–11:30 window (good signals still valid)")
    elif window == "BTST_ONLY":
        warnings.append(f"⚠️ BTST window — June expiry + OI >500 + breakout + not at day high required")
    if stock.lot_size >= 2000:
        warnings.append(f"⚠️ Lot size {stock.lot_size} >= 2,000 — verify margin carefully")
    if open_positions >= MAX_POSITIONS:
        warnings.append("⚠️ At maximum 3 positions — no new entries")

    passed = score == len(check_map)
    return ChecklistResult(passed=passed, checks=checks, failures=failures, warnings=warnings, score=score)


def compute_levels(stock: StockData) -> tuple[float, float, float, float]:
    """Returns (ideal_entry, sl, t1, t2) based on premium."""
    p = stock.premium
    sl = round(p * 0.65, 2)        # ~35% below premium
    t1 = round(p * 1.75, 2)        # ~75% above premium
    t2 = round(p * 2.30, 2)        # ~130% above premium
    pullback_target = round(p * 0.95, 2)
    return pullback_target, sl, t1, t2


def build_trade_card(stock: StockData, result: ChecklistResult) -> TradeCard:
    price_change_pct = ((stock.ltp - stock.prev_close) / stock.prev_close * 100) if stock.prev_close else 0
    volume_ratio = (stock.volume / stock.avg_volume) if stock.avg_volume else 0
    oi_change_pct = (stock.ce_oi_change / (stock.ce_oi - stock.ce_oi_change) * 100) if (stock.ce_oi - stock.ce_oi_change) > 0 else 0
    ideal_entry, sl, t1, t2 = compute_levels(stock)
    capital = stock.premium * stock.lot_size
    instrument = f"{stock.symbol} Jun {stock.strike} CE"
    score = result.score / 11 * 100
    near_high = stock.ltp >= stock.day_high * 0.995

    return TradeCard(
        symbol=stock.symbol,
        instrument=instrument,
        strike=stock.strike,
        new_contracts=stock.new_contracts,
        oi_change_pct=round(oi_change_pct, 1),
        volume_ratio=round(volume_ratio, 1),
        price_change_pct=round(price_change_pct, 2),
        pcr=stock.pcr,
        supertrend=stock.supertrend,
        rsi=stock.rsi,
        premium=stock.premium,
        ideal_entry=ideal_entry,
        spot_price=stock.ltp,
        wait_for_pullback=near_high,
        sl=sl,
        t1=t1,
        t2=t2,
        capital=capital,
        score=score,
        checklist_passed=result.passed,
        failures=result.failures,
        warnings=result.warnings,
    )


def format_trade_card(card: TradeCard) -> str:
    status = "✅ ALL CHECKS PASS — READY TO TRADE" if card.checklist_passed else f"❌ FAILED {len(card.failures)} CHECK(S)"
    lines = [
        "",
        f"{'='*55}",
        f"  {card.instrument}",
        f"  Score: {card.score:.0f}/100  |  {status}",
        f"{'='*55}",
        f"{'✅' if card.new_contracts >= MIN_NEW_CONTRACTS else '❌'} Contracts : {card.new_contracts:,} new",
        f"{'✅' if card.oi_change_pct > 0 else '❌'} OI        : +{card.oi_change_pct:.1f}%",
        f"{'✅' if card.volume_ratio >= 2.0 else '❌'} Volume    : {card.volume_ratio:.1f}x average",
        f"{'✅' if card.price_change_pct > 0 else '❌'} Price     : +{card.price_change_pct:.2f}%",
        f"{'✅' if card.pcr >= MIN_PCR else '❌'} PCR       : {card.pcr:.2f}",
        f"{'✅' if card.supertrend.upper() == 'UP' else '❌'} SuperTrend: {card.supertrend.upper()}",
        f"{'✅' if 45 <= card.rsi <= 70 else '❌'} RSI       : {card.rsi:.1f}",
        f"{'-'*55}",
        f"  Current premium : ₹{card.premium:.2f}",
        f"  🎯 Ideal entry  : ₹{card.ideal_entry:.2f}  (spot ₹{card.spot_price:.2f})",
        f"  ⏰ Wait pullback: {'YES — at day high' if card.wait_for_pullback else 'NO — can enter'}",
        f"  SL              : ₹{card.sl:.2f}",
        f"  T1              : ₹{card.t1:.2f}",
        f"  T2              : ₹{card.t2:.2f}",
        f"  Capital         : ₹{card.capital:,.0f}",
    ]
    if card.failures:
        lines.append(f"{'-'*55}")
        lines.append("  ❌ Failed checks:")
        for f in card.failures:
            lines.append(f"     • {f}")
    if card.warnings:
        lines.append(f"{'-'*55}")
        for w in card.warnings:
            lines.append(f"  {w}")
    lines.append(f"{'='*55}")
    return "\n".join(lines)


def check_single_stock(
    symbol: str,
    strike: int,
    premium: float,
    lot_size: int,
    ltp: float,
    day_high: float,
    day_low: float,
    prev_close: float,
    volume: float,
    avg_volume: float,
    ce_oi: int,
    ce_oi_change: int,
    pcr: float,
    new_contracts: int,
    supertrend: str,
    rsi: float,
    adx: float = 0.0,
    sector: str = "",
    premium_change_pct: float = 0.0,
) -> str:
    """
    Check a single stock against all 11 rules and return formatted trade card.
    Called by Claude Code when user says: 'Check if STOCK passes all 11 rules'
    or 'Generate trade card for STOCK STRIKE CE'
    """
    stock = StockData(
        symbol=symbol.upper(),
        ltp=ltp,
        day_high=day_high,
        day_low=day_low,
        prev_close=prev_close,
        volume=volume,
        avg_volume=avg_volume,
        sector=sector,
        lot_size=lot_size,
        ce_oi=ce_oi,
        ce_oi_change=ce_oi_change,
        pcr=pcr,
        new_contracts=new_contracts,
        supertrend=supertrend,
        rsi=rsi,
        adx=adx,
        strike=strike,
        premium=premium,
        premium_change_pct=premium_change_pct,
    )

    daily_loss = get_daily_loss()
    open_positions = get_open_position_count()

    rejected, rejection_reasons = run_filters(stock, daily_loss, open_positions)
    if rejected:
        lines = [f"\n{'='*55}", f"  {symbol.upper()} Jun {strike} CE  —  AUTO-REJECTED", f"{'='*55}"]
        for r in rejection_reasons:
            lines.append(f"  🚫 {r}")
        lines.append(f"{'='*55}")
        return "\n".join(lines)

    result = run_checklist(stock, daily_loss, open_positions)
    card = build_trade_card(stock, result)
    return format_trade_card(card)


def scan_all(stock_list: list[dict]) -> str:
    """
    Main scanner — takes a list of stock dicts (populated from Groww MCP)
    and returns ranked trade cards for those that pass filters.

    Each dict in stock_list should have keys matching StockData fields.
    Called by Claude Code when user says: 'Scan all FNO stocks'
    """
    daily_loss = get_daily_loss()
    open_positions = get_open_position_count()
    t = now_time()

    header_lines = [
        f"\n{'='*55}",
        f"  FNO SCANNER — {datetime.now().strftime('%d %b %Y %H:%M')}",
        f"  Daily loss so far: ₹{daily_loss:,.0f}  |  Open positions: {open_positions}",
        f"  Time window: {get_time_window(t)} ({t.strftime('%H:%M')})",
        f"{'='*55}",
    ]

    if daily_loss >= MAX_DAILY_LOSS:
        header_lines.append(f"  🚨 HALT: Daily loss ₹{daily_loss:,.0f} >= ₹5,000 — NO NEW TRADES")
        header_lines.append(f"{'='*55}")
        return "\n".join(header_lines)

    if open_positions >= MAX_POSITIONS:
        header_lines.append(f"  🚨 HALT: {open_positions} positions open (max {MAX_POSITIONS})")
        header_lines.append(f"{'='*55}")
        return "\n".join(header_lines)

    candidates: list[tuple[float, StockData, ChecklistResult]] = []

    for raw in stock_list:
        try:
            stock = StockData(**{k: raw[k] for k in StockData.__dataclass_fields__ if k in raw})
            rejected, _ = run_filters(stock, daily_loss, open_positions)
            if rejected:
                continue
            result = run_checklist(stock, daily_loss, open_positions)
            candidates.append((result.score, stock, result))
        except Exception as e:
            header_lines.append(f"  ⚠️ Error processing {raw.get('symbol', '?')}: {e}")

    candidates.sort(key=lambda x: x[0], reverse=True)
    max_cards = SCAN_CRITERIA["output"]["max_trade_cards"]

    output = "\n".join(header_lines)
    output += f"\n  Found {len(candidates)} candidates (showing top {min(max_cards, len(candidates))})\n"

    for score, stock, result in candidates[:max_cards]:
        card = build_trade_card(stock, result)
        output += format_trade_card(card)

    if not candidates:
        output += "\n  No stocks passed filters at this time.\n"

    return output


def check_rule_violations_now() -> str:
    """
    'Am I breaking any rules right now?'
    Checks open positions against all rules and returns a violation report.
    """
    positions = load_positions()
    daily_loss = get_daily_loss()
    open_count = len([p for p in positions.get("open_positions", []) if p.get("status") == "OPEN"])
    t = now_time()
    violations = []
    warnings = []

    if daily_loss >= MAX_DAILY_LOSS:
        violations.append(f"🚨 CRITICAL: Daily loss ₹{daily_loss:,.0f} >= ₹5,000 — STOP TRADING NOW")

    if open_count > MAX_POSITIONS:
        violations.append(f"🚨 CRITICAL: {open_count} positions open — max is {MAX_POSITIONS}")

    if t > HARD_STOP:
        warnings.append(f"⚠️ Time is {t.strftime('%H:%M')} — past 3:15 PM, no new entries ever")
    elif BTST_WINDOW_START <= t <= BTST_WINDOW_END:
        warnings.append(f"⚠️ BTST window — June expiry + OI >500 + breakout + not at day high required")

    for pos in positions.get("open_positions", []):
        for v in pos.get("rule_violations", []):
            label = "🚨 CRITICAL" if v.get("severity") == "CRITICAL" else "⚠️ WARNING"
            violations.append(f"{label} [{pos['stock']}]: {v['description']}")

    lines = [f"\n{'='*55}", f"  RULE VIOLATION CHECK — {datetime.now().strftime('%H:%M')}", f"{'='*55}"]
    if not violations and not warnings:
        lines.append("  ✅ All rules OK — no violations detected")
    else:
        for v in violations:
            lines.append(f"  {v}")
        for w in warnings:
            lines.append(f"  {w}")
    lines.append(f"{'='*55}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(check_rule_violations_now())
    print("\nScanner ready. Call scan_all(stock_list) with data from Groww MCP.")
    print("Or call check_single_stock() for a specific stock.")
