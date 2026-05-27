"""
P&L Tracker — manages open positions, trade history, and rule violation reporting.
All monetary values in Indian Rupees (₹).
Usage: Run interactively via Claude Code commands listed in README.md
"""

from __future__ import annotations
import json
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
from typing import Optional

ROOT = Path(__file__).parent.parent
TRACKER_DIR = ROOT / "tracker"
POSITIONS_FILE = TRACKER_DIR / "positions.json"
HISTORY_FILE = TRACKER_DIR / "trade_history.json"

MAX_DAILY_LOSS = 5_000
MAX_CAPITAL_PER_TRADE = 25_000


def _load(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def load_positions() -> dict:
    return _load(POSITIONS_FILE)


def load_history() -> dict:
    return _load(HISTORY_FILE)


def get_today_str() -> str:
    return datetime.now(IST).date().isoformat()


def show_today_pnl() -> str:
    """'Show today's P&L'"""
    positions = load_positions()
    history = load_history()
    today = get_today_str()
    lines = [f"\n{'='*55}", f"  TODAY'S P&L — {datetime.now(IST).strftime('%d %b %Y %H:%M')}", f"{'='*55}"]

    # Realized P&L from today's closed trades
    realized = 0.0
    today_trades = [t for t in history.get("closed_trades", []) if t.get("date") == today]
    if today_trades:
        lines.append("\n  CLOSED TRADES TODAY:")
        for t in today_trades:
            pnl = t.get("pnl", 0)
            realized += pnl
            icon = "✅" if pnl >= 0 else "❌"
            lines.append(f"  {icon} {t['instrument']:25s} ₹{pnl:+,.0f}")

    lines.append(f"\n  Realized P&L     : ₹{realized:+,.0f}")

    # Open positions (unrealized — MTP must be fetched live for accurate values)
    open_pos = [p for p in positions.get("open_positions", []) if p.get("status") == "OPEN"]
    if open_pos:
        lines.append(f"\n  OPEN POSITIONS ({len(open_pos)}):")
        total_capital = 0.0
        total_max_loss = 0.0
        for p in open_pos:
            cap = p.get("entry_capital", 0)
            max_loss = p.get("max_loss", 0)
            total_capital += cap
            total_max_loss += max_loss
            violation_flag = " ⚠️" if p.get("rule_violations") else ""
            lines.append(f"  • {p['instrument']:30s} entry ₹{p['entry_price']:.2f}{violation_flag}")
            lines.append(f"    SL ₹{p['sl']:.2f}  T1 ₹{p['t1']:.2f}  Capital ₹{cap:,.0f}")
        lines.append(f"\n  Total capital deployed : ₹{total_capital:,.0f}")
        lines.append(f"  Max possible loss      : ₹{total_max_loss:,.0f}")
        lines.append(f"  (Unrealized P&L requires live LTP from Groww MCP)")

    # Daily loss warning
    daily_loss = sum(abs(t.get("pnl", 0)) for t in today_trades if t.get("pnl", 0) < 0)
    remaining = MAX_DAILY_LOSS - daily_loss
    lines.append(f"\n  Daily loss used        : ₹{daily_loss:,.0f} / ₹{MAX_DAILY_LOSS:,.0f}")
    if daily_loss >= MAX_DAILY_LOSS:
        lines.append("  🚨 DAILY LOSS LIMIT HIT — STOP ALL TRADING")
    elif daily_loss >= MAX_DAILY_LOSS * 0.70:
        lines.append(f"  ⚠️  WARNING: Only ₹{remaining:,.0f} remaining before halt")
    else:
        lines.append(f"  Remaining before halt  : ₹{remaining:,.0f}")

    lines.append(f"{'='*55}")
    return "\n".join(lines)


def show_week_history() -> str:
    """'Show this week's trade history'"""
    history = load_history()
    trades = history.get("closed_trades", [])
    summary = history.get("weekly_summary", {})
    week_label = history.get("week", "This week")

    lines = [f"\n{'='*55}", f"  TRADE HISTORY — {week_label}", f"{'='*55}"]

    winners = [t for t in trades if t.get("pnl", 0) >= 0]
    losers = [t for t in trades if t.get("pnl", 0) < 0]

    lines.append(f"\n  WINNERS ({len(winners)}):")
    for t in sorted(winners, key=lambda x: x.get("pnl", 0), reverse=True):
        lines.append(f"  ✅ {t['instrument']:30s} +₹{t['pnl']:,.0f}")

    lines.append(f"\n  LOSERS ({len(losers)}):")
    for t in sorted(losers, key=lambda x: x.get("pnl", 0)):
        vflag = " ⚠️ RULE VIOLATION" if t.get("rule_violations") else ""
        lines.append(f"  ❌ {t['instrument']:30s} -₹{abs(t['pnl']):,.0f}{vflag}")

    lines.append(f"\n{'─'*55}")
    lines.append(f"  Total trades   : {summary.get('total_trades', len(trades))}")
    lines.append(f"  Win rate       : {summary.get('win_rate_pct', 0):.1f}%")
    lines.append(f"  Gross profit   : ₹{summary.get('gross_profit', 0):,.0f}")
    lines.append(f"  Gross loss     : ₹{abs(summary.get('gross_loss', 0)):,.0f}")
    lines.append(f"  Net P&L        : ₹{summary.get('net_pnl', 0):+,.0f}")
    lines.append(f"  Avg winner     : ₹{summary.get('average_winner', 0):,.0f}")
    lines.append(f"  Avg loser      : ₹{abs(summary.get('average_loser', 0)):,.0f}")
    lines.append(f"  Largest winner : ₹{summary.get('largest_winner', 0):,.0f}")
    lines.append(f"  Largest loser  : ₹{abs(summary.get('largest_loser', 0)):,.0f}")

    violations = [t for t in trades if t.get("rule_violations")]
    if violations:
        lines.append(f"\n  ⚠️  RULE VIOLATIONS THIS WEEK ({len(violations)} trades):")
        for t in violations:
            for v in t["rule_violations"]:
                lines.append(f"     • {t['instrument']}: {v['description']}")

    if summary.get("observation"):
        lines.append(f"\n  💡 {summary['observation']}")

    lines.append(f"{'='*55}")
    return "\n".join(lines)


def update_sl(symbol: str, new_sl: float) -> str:
    """'Update SL on STOCK to ₹X'"""
    positions = load_positions()
    updated = False
    for pos in positions.get("open_positions", []):
        if pos["stock"].upper() == symbol.upper() and pos["status"] == "OPEN":
            old_sl = pos["sl"]
            pos["sl"] = new_sl
            positions["last_updated"] = get_today_str()
            _save(POSITIONS_FILE, positions)
            updated = True
            return (
                f"\n✅ SL updated for {pos['instrument']}\n"
                f"   Old SL: ₹{old_sl:.2f}  →  New SL: ₹{new_sl:.2f}\n"
                f"   Max loss now: ₹{(pos['entry_price'] - new_sl) * pos['lot_size']:,.0f}"
            )
    if not updated:
        return f"\n❌ No open position found for {symbol.upper()}"


def close_position(symbol: str, exit_price: float, exit_date: Optional[str] = None) -> str:
    """Close a position and move it to trade history."""
    positions = load_positions()
    history = load_history()
    exit_date = exit_date or get_today_str()
    closed = None

    for pos in positions.get("open_positions", []):
        if pos["stock"].upper() == symbol.upper() and pos["status"] == "OPEN":
            pnl = (exit_price - pos["entry_price"]) * pos["lot_size"]
            pos["status"] = "CLOSED"
            pos["exit_price"] = exit_price
            pos["exit_date"] = exit_date
            pos["pnl"] = round(pnl, 2)
            closed = pos
            break

    if not closed:
        return f"\n❌ No open position found for {symbol.upper()}"

    # Add to history
    trade_record = {
        "id": f"T{len(history.get('closed_trades', [])) + 1:03d}",
        "stock": closed["stock"],
        "instrument": closed["instrument"],
        "strike": closed["strike"],
        "option_type": closed["option_type"],
        "entry_price": closed["entry_price"],
        "exit_price": exit_price,
        "pnl": closed["pnl"],
        "date": exit_date,
        "outcome": "WIN" if closed["pnl"] >= 0 else "LOSS",
        "sector": closed.get("sector", ""),
        "rule_violations": closed.get("rule_violations", []),
    }

    if "closed_trades" not in history:
        history["closed_trades"] = []
    history["closed_trades"].append(trade_record)

    # Remove from open positions
    positions["open_positions"] = [
        p for p in positions["open_positions"] if not (p["stock"] == closed["stock"] and p["status"] == "CLOSED")
    ]
    positions["last_updated"] = exit_date
    _save(POSITIONS_FILE, positions)
    _save(HISTORY_FILE, history)

    icon = "✅" if closed["pnl"] >= 0 else "❌"
    return (
        f"\n{icon} Position closed: {closed['instrument']}\n"
        f"   Entry: ₹{closed['entry_price']:.2f}  Exit: ₹{exit_price:.2f}\n"
        f"   P&L: ₹{closed['pnl']:+,.0f}"
    )


def add_position(
    symbol: str,
    strike: int,
    premium: float,
    sl: float,
    t1: float,
    lot_size: int,
    t2: Optional[float] = None,
    sector: str = "",
    rule_violations: Optional[list] = None,
    notes: str = "",
) -> str:
    """Add a new open position."""
    positions = load_positions()
    pos_id = f"POS{len(positions.get('open_positions', [])) + 1:03d}"
    capital = premium * lot_size
    max_loss = (premium - sl) * lot_size
    target_t1 = (t1 - premium) * lot_size
    target_t2 = (t2 - premium) * lot_size if t2 else None

    if capital > MAX_CAPITAL_PER_TRADE:
        return f"\n❌ Capital ₹{capital:,.0f} exceeds ₹{MAX_CAPITAL_PER_TRADE:,.0f} max per trade"

    new_pos = {
        "id": pos_id,
        "stock": symbol.upper(),
        "instrument": f"{symbol.upper()} Jun {strike} CE",
        "strike": strike,
        "option_type": "CE",
        "expiry": "2026-06-30",
        "entry_price": premium,
        "entry_date": get_today_str(),
        "lot_size": lot_size,
        "lots": 1,
        "total_contracts": lot_size,
        "entry_capital": round(capital, 2),
        "sl": sl,
        "t1": t1,
        "t2": t2,
        "max_loss": round(max_loss, 2),
        "target_profit_t1": round(target_t1, 2),
        "target_profit_t2": round(target_t2, 2) if target_t2 else None,
        "sector": sector,
        "status": "OPEN",
        "rule_violations": rule_violations or [],
        "notes": notes,
    }

    if "open_positions" not in positions:
        positions["open_positions"] = []
    positions["open_positions"].append(new_pos)
    positions["last_updated"] = get_today_str()
    _save(POSITIONS_FILE, positions)

    return (
        f"\n✅ Position added: {new_pos['instrument']}\n"
        f"   Entry: ₹{premium:.2f}  SL: ₹{sl:.2f}  T1: ₹{t1:.2f}\n"
        f"   Capital: ₹{capital:,.0f}  Max loss: ₹{max_loss:,.0f}"
    )


def check_rule_violations() -> str:
    """'Am I breaking any rules right now?'"""
    positions = load_positions()
    history = load_history()
    today = get_today_str()
    open_pos = [p for p in positions.get("open_positions", []) if p.get("status") == "OPEN"]
    open_count = len(open_pos)
    daily_loss = sum(
        abs(t.get("pnl", 0))
        for t in history.get("closed_trades", [])
        if t.get("date") == today and t.get("pnl", 0) < 0
    )

    violations = []
    warnings = []

    # System-level checks
    if daily_loss >= MAX_DAILY_LOSS:
        violations.append(f"🚨 CRITICAL — Daily loss ₹{daily_loss:,.0f} >= ₹5,000. STOP ALL TRADING.")
    elif daily_loss >= MAX_DAILY_LOSS * 0.70:
        warnings.append(f"⚠️ Daily loss ₹{daily_loss:,.0f} — only ₹{MAX_DAILY_LOSS - daily_loss:,.0f} left")

    if open_count > 3:
        violations.append(f"🚨 CRITICAL — {open_count} positions open (max 3). Close one before new entry.")
    elif open_count == 3:
        warnings.append("⚠️ At max 3 positions — no new entries until one closes")

    # Per-position checks
    for pos in open_pos:
        sym = pos["stock"]
        max_loss = pos.get("max_loss", 0)
        capital = pos.get("entry_capital", 0)

        if max_loss > MAX_DAILY_LOSS:
            violations.append(
                f"🚨 {sym} — Max loss ₹{max_loss:,.0f} exceeds ₹5,000 per-trade limit"
            )
        if capital > MAX_CAPITAL_PER_TRADE:
            violations.append(
                f"🚨 {sym} — Capital ₹{capital:,.0f} exceeds ₹25,000 per-trade limit"
            )
        for v in pos.get("rule_violations", []):
            icon = "🚨 CRITICAL" if v.get("severity") == "CRITICAL" else "⚠️ WARNING"
            violations.append(f"{icon} {sym} — {v['description']}")

    lines = [f"\n{'='*55}", f"  RULE VIOLATION CHECK — {datetime.now(IST).strftime('%d %b %Y %H:%M')}", f"{'='*55}"]
    if not violations and not warnings:
        lines.append("  ✅ All clear — no rule violations detected")
    else:
        for v in violations:
            lines.append(f"  {v}")
        for w in warnings:
            lines.append(f"  {w}")
    lines.append(f"\n  Open positions : {open_count} / 3")
    lines.append(f"  Daily loss     : ₹{daily_loss:,.0f} / ₹{MAX_DAILY_LOSS:,.0f}")
    lines.append(f"{'='*55}")
    return "\n".join(lines)


def weekly_summary() -> str:
    """Full weekly summary with biggest winners/losers."""
    history = load_history()
    trades = history.get("closed_trades", [])
    if not trades:
        return "\n  No trade history found."

    by_pnl = sorted(trades, key=lambda t: t.get("pnl", 0))
    losers_top3 = by_pnl[:3]
    winners_top3 = by_pnl[-3:][::-1]

    lines = [
        f"\n{'='*55}",
        f"  WEEKLY SUMMARY — {history.get('week', '')}",
        f"{'='*55}",
        f"\n  🏆 BIGGEST WINNERS:",
    ]
    for t in winners_top3:
        lines.append(f"     ✅ {t['instrument']:30s} +₹{t.get('pnl', 0):,.0f}")

    lines.append(f"\n  💀 BIGGEST LOSERS:")
    for t in losers_top3:
        vflag = " ⚠️" if t.get("rule_violations") else ""
        lines.append(f"     ❌ {t['instrument']:30s} -₹{abs(t.get('pnl', 0)):,.0f}{vflag}")

    summary = history.get("weekly_summary", {})
    lines += [
        f"\n{'─'*55}",
        f"  Win rate      : {summary.get('win_rate_pct', 0):.1f}%",
        f"  Net P&L       : ₹{summary.get('net_pnl', 0):+,.0f}",
        f"  Profit factor : {summary.get('gross_profit', 0) / max(abs(summary.get('gross_loss', 1)), 1):.2f}",
        f"  Avg W / Avg L : ₹{summary.get('average_winner', 0):,.0f} / ₹{abs(summary.get('average_loser', 0)):,.0f}",
    ]

    violations = [t for t in trades if t.get("rule_violations")]
    if violations:
        lines.append(f"\n  ⚠️  RULE VIOLATIONS ({len(violations)} trades impacted P&L):")
        for t in violations:
            for v in t["rule_violations"]:
                lines.append(f"     • {t['instrument']}: {v['description']}")

    if summary.get("observation"):
        lines.append(f"\n  💡 INSIGHT: {summary['observation']}")

    lines.append(f"{'='*55}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(show_today_pnl())
    print(weekly_summary())
    print(check_rule_violations())
