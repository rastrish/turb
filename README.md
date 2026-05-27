# F&O Options Trading System

A complete rule-based trading assistant for Indian F&O markets, built for June expiry CE trades with live data from Groww MCP.

---

## System Overview

| Component | Purpose |
|-----------|---------|
| `config/rules.json` | All 24 trading rules |
| `config/checklist.json` | 11-point entry checklist |
| `config/filters.json` | Auto-reject filters + daily loss warnings |
| `config/scan_criteria.json` | Groww MCP data sources and scoring weights |
| `scripts/scanner.py` | Live scanner — fetches, filters, ranks, and generates trade cards |
| `scripts/pnl_tracker.py` | P&L tracker, position manager, and violation reporter |
| `tracker/positions.json` | Open positions with SL/T1/T2 and rule flags |
| `tracker/trade_history.json` | Closed trade log with weekly summary |

**Expiry**: June 30, 2026 | **Capital per trade**: ₹25,000 max | **Daily loss halt**: ₹5,000

---

## Claude Code Commands

Use these natural language commands when chatting with Claude Code in this repo.

### Scanning

```
Scan all FNO stocks
```
Fetches GAINERS + TOP_TRADED from `fetch_curated_fno` and TOP_GAINERS + HIGH_MOMENTUM + VOLUME_SHOCKERS from `fetch_market_movers`, runs all filters and the 11-point checklist, and returns ranked trade cards.

```
Generate trade card for CIPLA 1440 CE
Generate trade card for JSWENERGY 620 CE
```
Fetches live OI, LTP, and technical indicators from Groww MCP for that specific option and outputs a full trade card with entry, SL, T1, T2, and capital.

### Checklist

```
Check if HINDALCO passes all 11 rules
Check if NTPC 410 CE passes the checklist
```
Runs the full 11-point checklist against live data and shows which checks pass/fail.

### P&L and Positions

```
Show today's P&L
```
Shows realized P&L from closed trades today, open positions summary, and how much of the ₹5,000 daily loss limit remains.

```
Show this week's trade history
```
Full history for the current week — winners, losers, biggest trades, win rate, profit factor, and rule violations.

```
Show open positions
```
Lists all open positions with entry, SL, T1, T2, capital deployed, and any rule violation flags.

### Position Management

```
Update SL on CIPLA to ₹20
Update SL on VOLTAS to ₹38
```
Updates the stop-loss in `tracker/positions.json` and shows new max loss.

```
Close CIPLA at ₹45
Close JSWENERGY at ₹28
```
Marks a position closed, calculates P&L, and moves it to trade history.

```
Add position: TATASTEEL 1400 CE, entry ₹18, SL ₹12, T1 ₹32, lot 550
```
Adds a new open position after validating capital limit.

### Rule Checks

```
Am I breaking any rules right now?
```
Checks all open positions and current time/date against all 24 rules and flags any violations or warnings.

```
What rules apply before I enter a trade?
```
Summarizes the mandatory entry rules and the 11-point checklist.

---

## Entry Checklist (all 11 must pass)

| # | Check | Threshold |
|---|-------|-----------|
| 1 | New contracts | >= 200 |
| 2 | OI direction | Building |
| 3 | Volume vs avg | >= 2x |
| 4 | Price direction | Up |
| 5 | PCR | >= 0.75 |
| 6 | Time | 10:00–11:30 AM |
| 7 | Not at day high | True |
| 8 | Capital | <= ₹25,000 |
| 9 | Open positions | < 3 |
| 10 | SuperTrend | UP |
| 11 | RSI | 45–70 |

---

## Auto-Reject Filters (any one = skip)

- PCR < 0.75
- SuperTrend DOWN
- New contracts < 200
- Premium up > 30% today (wait for pullback)
- Lot size > 2,500
- Gap down before 9:45 AM
- Daily loss >= ₹5,000
- Time before 9:30 AM or after 2:30 PM

---

## Key Rules Summary

**When rules conflict → capital preservation always wins.**

1. OI + Price + PCR must ALL align
2. Min 200 new contracts (absolute, not %)
3. No entry before 9:30 AM (ideal: 10:00–11:30 AM)
4. Never enter at day high — wait for pullback
5. Gap down = wait 30 mins before CE entries
6. June expiry preferred; avoid May last week
7. SL hit = exit immediately, no hope trading
8. 1 lot = book full at T1
9. Max 3 positions at once
10. Daily max loss ₹5,000 = halt all trading
11. Don't exit June positions on early PCR (wait 30 mins)
12. Fresh OI + price up = bullish signal ✅
13. PCR AND CE OI must both confirm
14. Check sector OI correlation
15. Never carry deep OTM to expiry day
16. Max loss per trade = ₹5,000
17. Lot size > 2,000 = proceed carefully
18. Wait 30 mins after SL before redeploying
19. Best window 10:00–11:30 AM
20. No new trades after 2:30 PM
21. Never carry weekly options over weekend
22. Exit losing May positions by Thursday
23. Geopolitical tension = reduce overnight exposure
24. June + 30 days + OI > 500 = trail SL, don't exit on first profit

---

## Groww MCP Tools Used

| Tool | Purpose |
|------|---------|
| `get_open_interest_analysis` | CE OI, PCR, new contracts |
| `get_ltp` | Live premium and spot price |
| `fetch_curated_fno` | GAINERS + TOP_TRADED FNO list |
| `fetch_market_movers_and_trending_stocks_funds` | TOP_GAINERS, HIGH_MOMENTUM, VOLUME_SHOCKERS |
| `get_historical_technical_indicators` | SuperTrend, RSI, ADX |

---

## Current Open Positions (as of 2026-05-27)

| Instrument | Entry | SL | T1 | T2 | Capital | Flags |
|------------|-------|----|----|----|---------|-------|
| CIPLA Jun 1440 CE | ₹25.55 | ₹17 | ₹45 | ₹60 | ₹9,581 | — |
| JSWENERGY Jun 620 CE | ₹15.55 | ₹10 | ₹28 | ₹40 | ₹15,550 | — |
| VOLTAS Jun 1300 CE | ₹51.35 | ₹32 | ₹65 | ₹80 | ₹19,256 | — |
| NTPC Jun 410 CE | ₹6.50 | ₹3.50 | ₹12 | — | ₹9,750 | ⚠️ Pre-10AM entry |

> **Note**: 4 positions open exceeds the 3-position max. Review before adding new trades.

---

## This Week's Performance (May 19–26)

| Metric | Value |
|--------|-------|
| Net P&L | -₹15,282 |
| Win rate | 54.5% (6W / 5L) |
| Avg winner | ₹2,785 |
| Avg loser | ₹6,398 |
| Rule violations | 3 trades breached ₹5,000/trade max |

**Key insight**: Average loser is 2.3x the average winner. The 3 trades that exceeded the ₹5,000 per-trade loss rule (BDL, SOLARINDS, BAJAJ AUTO) caused ₹26,037 in losses combined. Strict SL placement is the #1 priority.
