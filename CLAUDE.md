# F&O Trading System — Claude Code Instructions

This repo belongs to an F&O options trader on Indian markets.
Expiry: **June 30, 2026** | Option type: **CE only** | Capital per trade: **₹25,000 max** | Daily halt: **₹5,000 loss**

---

## TRIGGER: "Fresh scan"

Whenever the user says **"Fresh scan"** (any case, with or without punctuation), execute the full scan sequence below **immediately and completely** — do not ask for clarification first.

### Step 1 — Pre-scan checks
Read `tracker/positions.json` and `tracker/trade_history.json`.
- Count open positions. If >= 3: show warning but continue scan (they may be closing one soon).
- Calculate today's realized loss. If >= ₹5,000: print halt message and **stop** — do not show trade cards.
- Note the current time and call `get_time_window()` logic:
  - before 9:30 AM → print "Outside trading hours" and stop
  - after 3:15 PM → print "Outside trading hours" and stop

### Step 2 — Fetch FNO universe (run in parallel)
Call both sources simultaneously:

**Source A — Groww MCP `fetch_curated_fno`**
- categories: `GAINERS` and `TOP_TRADED`

**Source B — Groww MCP `fetch_market_movers_and_trending_stocks_funds`**
- categories: `TOP_GAINERS`, `HIGH_MOMENTUM`, `VOLUME_SHOCKERS`

Deduplicate the combined list by symbol.

### Step 3 — Shortlist
Keep only stocks with **price move >= +1.5%** intraday.
If fewer than 3 stocks pass, lower threshold to +0.8% and note this.

### Step 4 — Enrich each shortlisted stock (run in parallel per stock)
For every shortlisted stock, call these 3 Groww MCP tools simultaneously:
1. `get_open_interest_analysis` — get CE OI, OI change, new contracts, PCR
2. `get_ltp` — get current premium, day high, day low, prev close, volume
3. `get_historical_technical_indicators` — get SuperTrend direction, RSI (14), ADX

### Step 5 — Apply auto-reject filters (from `config/filters.json`)
Reject immediately if ANY of these are true:
- PCR < 0.75
- SuperTrend = DOWN
- New contracts < 200
- Premium change > 30% today
- Lot size > 2,500
- Daily loss >= ₹5,000
- Time before 9:30 AM or after 3:15 PM

For 9:30–10:00 AM window: also reject if new contracts < 500 OR PCR < 0.85.

### Step 6 — Run 11-point checklist (from `config/checklist.json`)
Score each remaining stock out of 11:
1. New contracts >= 200
2. OI direction: Building
3. Volume > 2x average
4. Price direction: Up
5. PCR >= 0.75
6. Time window: valid per Rule 3
7. Not at day high
8. Capital (premium × lot size) < ₹25,000
9. Open positions < 3
10. SuperTrend: UP
11. RSI: 45–70

### Step 7 — Output ranked trade cards
Sort by score descending. Show top 5 (or all if fewer).
For each stock, print in this exact format:

```
=======================================================
  STOCK Jun STRIKE CE
  Score: XX/100  |  ✅ ALL CHECKS PASS / ❌ FAILED N CHECK(S)
=======================================================
✅/❌ Contracts : X new
✅/❌ OI        : +X%
✅/❌ Volume    : Xx average
✅/❌ Price     : +X%
✅/❌ PCR       : X.XX
✅/❌ SuperTrend: UP/DOWN
✅/❌ RSI       : XX
-------------------------------------------------------
  Current premium : ₹XX
  🎯 Ideal entry  : ₹XX  (spot ₹XXX)
  ⏰ Wait pullback: YES — at day high / NO — can enter
  SL              : ₹XX
  T1              : ₹XX
  T2              : ₹XX
  Capital         : ₹XX,XXX
=======================================================
```

After all cards, print a one-line summary:
`Scan complete — X stocks passed all 11 checks, Y partial (≥8/11). Daily loss: ₹X,XXX / ₹5,000.`

### SL / T1 / T2 calculation defaults (use if position-specific levels unknown)
- SL = premium × 0.65 (35% below entry)
- T1 = premium × 1.75 (75% above entry)
- T2 = premium × 2.30 (130% above entry)
- Ideal entry = premium × 0.95 (wait for 5% pullback if near day high)

---

## Other trigger phrases

| User says | Action |
|-----------|--------|
| `Show today's P&L` | Read `tracker/positions.json` + `trade_history.json`, call `get_ltp` for each open position, compute unrealized P&L, print full P&L summary |
| `Generate trade card for STOCK STRIKE CE` | Fetch OI + LTP + technicals for that specific option, run full 11-point checklist, output single trade card |
| `Check if STOCK passes all 11 rules` | Same as above |
| `Update SL on STOCK to ₹X` | Edit `tracker/positions.json` directly, confirm new max loss |
| `Close STOCK at ₹X` | Move position to `tracker/trade_history.json` with P&L, update positions.json |
| `Show this week's trade history` | Read `tracker/trade_history.json`, format full summary |
| `Am I breaking any rules right now?` | Check open positions + time + daily loss against all 24 rules |

---

## Config file locations
- 24 rules: `config/rules.json`
- 11-point checklist: `config/checklist.json`
- Auto-reject filters: `config/filters.json`
- Scan sources + scoring: `config/scan_criteria.json`
- Open positions: `tracker/positions.json`
- Trade history: `tracker/trade_history.json`

## Capital preservation rule
**When any rules conflict, capital preservation always wins.**
