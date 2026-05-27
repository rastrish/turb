"""
Backtest Data Collector
Defines the exact Groww MCP calls needed to populate data/backtest/{SYMBOL}.json
and utilities to save + validate the data.

HOW TO USE (say this to Claude):
  "Fetch backtest data for HINDALCO, 6 months"
  Claude will call the two MCP tools below and save the result.

MCP CALLS REQUIRED PER SYMBOL:
─────────────────────────────────────────────────────────────────
  Tool 1: get_historical_technical_indicators
    company_name    : "{company full name e.g. Hindalco Industries}"
    start_time      : "{6 months ago} 09:15:00"
    end_time        : "{today} 15:30:00"
    interval_in_minutes : 1440   ← daily candles
    indicators      : ["supertrend", "rsi", "adx"]
    detail          : "series"   ← full time series, not just latest

  Tool 2: fetch_historical_candle_data
    symbol          : "{NSE symbol e.g. HINDALCO}"
    start_date      : "{6 months ago}"
    end_date        : "{today}"
    interval        : "1d"       ← daily
─────────────────────────────────────────────────────────────────

OUTPUT FORMAT saved to data/backtest/{SYMBOL}.json:
{
  "symbol": "HINDALCO",
  "lot_size": 700,
  "fetched_at": "2026-05-27",
  "candles": [
    {
      "date": "2025-12-01",
      "open": 1050.0,
      "high": 1082.5,
      "low": 1041.0,
      "close": 1075.0,
      "volume": 4200000
    },
    ...
  ],
  "indicators": [
    {
      "date": "2025-12-01",
      "rsi_14": 54.3,
      "supertrend_direction": 1.0,
      "adx_14": 28.1
    },
    ...
  ]
}
"""

from __future__ import annotations
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "backtest"


# Symbol → lot size mapping (update as needed)
LOT_SIZES: dict[str, int] = {
    "HINDALCO": 700,
    "CIPLA": 375,
    "JSWENERGY": 1000,
    "VOLTAS": 375,
    "NTPC": 1500,
    "SIEMENS": 175,
    "CUMMINSIND": 200,
    "CGPOWER": 850,
    "EXIDEIND": 1800,
    "TATASTEEL": 550,
    "VEDL": 2000,
    "ADANIENSOL": 275,
    "POWERGRID": 2400,
    "INDIGO": 75,
    "ETERNAL": 2250,
    "BAJAJ-AUTO": 15,
    "NATIONALUM": 2700,
    "OFSS": 100,
}

# Symbol → full company name for MCP calls
COMPANY_NAMES: dict[str, str] = {
    "HINDALCO": "Hindalco Industries",
    "CIPLA": "Cipla",
    "JSWENERGY": "JSW Energy",
    "VOLTAS": "Voltas",
    "NTPC": "NTPC",
    "SIEMENS": "Siemens",
    "CUMMINSIND": "Cummins India",
    "CGPOWER": "CG Power Industrial Solutions",
    "EXIDEIND": "Exide Industries",
    "TATASTEEL": "Tata Steel",
    "VEDL": "Vedanta",
    "ADANIENSOL": "Adani Energy Solutions",
    "POWERGRID": "Power Grid Corporation of India",
    "INDIGO": "Interglobe Aviation",
    "ETERNAL": "Eternal",
    "BAJAJ-AUTO": "Bajaj Auto",
    "NATIONALUM": "National Aluminium Company",
    "OFSS": "Oracle Financial Services Software",
}


def get_fetch_instructions(symbol: str, months: int = 6) -> dict:
    """
    Returns the exact MCP call parameters Claude needs to fetch backtest data.
    Call this function and follow the instructions to populate the data file.
    """
    symbol = symbol.upper()
    end_date = date.today()
    start_date = end_date - timedelta(days=months * 30)

    return {
        "symbol": symbol,
        "company_name": COMPANY_NAMES.get(symbol, symbol),
        "lot_size": LOT_SIZES.get(symbol, 0),
        "mcp_calls": {
            "technical_indicators": {
                "tool": "get_historical_technical_indicators",
                "params": {
                    "company_name": COMPANY_NAMES.get(symbol, symbol),
                    "start_time": f"{start_date} 09:15:00",
                    "end_time": f"{end_date} 15:30:00",
                    "interval_in_minutes": 1440,
                    "indicators": ["supertrend", "rsi", "adx"],
                    "detail": "series",
                }
            },
            "candle_data": {
                "tool": "fetch_historical_candle_data",
                "params": {
                    "symbol": symbol,
                    "start_date": str(start_date),
                    "end_date": str(end_date),
                    "interval": "1d",
                }
            }
        }
    }


def save_backtest_data(
    symbol: str,
    lot_size: int,
    candles: list[dict],
    indicators: list[dict],
) -> Path:
    """
    Save fetched data to data/backtest/{SYMBOL}.json in standard format.
    Called by Claude after MCP data is fetched.

    candles: list of {date, open, high, low, close, volume}
    indicators: list of {date, rsi_14, supertrend_direction, adx_14}
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{symbol.upper()}.json"

    # normalise dates to ISO string
    for c in candles:
        if isinstance(c.get("date"), date):
            c["date"] = c["date"].isoformat()
    for i in indicators:
        if isinstance(i.get("date"), date):
            i["date"] = i["date"].isoformat()

    payload = {
        "symbol": symbol.upper(),
        "lot_size": lot_size,
        "fetched_at": date.today().isoformat(),
        "candles": sorted(candles, key=lambda x: x["date"]),
        "indicators": sorted(indicators, key=lambda x: x["date"]),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"✅ Saved {len(candles)} candles + {len(indicators)} indicator rows → {path}")
    return path


def parse_indicator_series(mcp_response: dict) -> list[dict]:
    """
    Parse the series response from get_historical_technical_indicators.
    MCP returns a nested structure — this flattens it to our standard format.
    """
    rows = []
    result = mcp_response.get("result", mcp_response)
    series = result.get("series", result.get("data", []))

    for entry in series:
        # Handle both flat and nested formats
        row_date = entry.get("date") or entry.get("timestamp", "")
        if not row_date:
            continue
        # Normalise date string
        if "T" in str(row_date):
            row_date = str(row_date).split("T")[0]
        elif " " in str(row_date):
            row_date = str(row_date).split(" ")[0]

        indicators_data = entry.get("indicators", entry)
        rows.append({
            "date": row_date,
            "rsi_14": indicators_data.get("rsi_14", indicators_data.get("rsi", 50.0)),
            "supertrend_direction": indicators_data.get(
                "st_direction_10_3.0",
                indicators_data.get("supertrend_direction", 1.0)
            ),
            "adx_14": indicators_data.get("adx_14", indicators_data.get("adx", 0.0)),
        })
    return rows


def parse_candle_series(mcp_response: dict) -> list[dict]:
    """
    Parse candle data from fetch_historical_candle_data MCP response.
    """
    rows = []
    result = mcp_response.get("result", mcp_response)
    candles = result.get("candles", result.get("data", result.get("ohlcv", [])))

    for c in candles:
        row_date = c.get("date") or c.get("timestamp") or c.get("time", "")
        if "T" in str(row_date):
            row_date = str(row_date).split("T")[0]
        elif " " in str(row_date):
            row_date = str(row_date).split(" ")[0]
        rows.append({
            "date": str(row_date),
            "open": float(c.get("open", 0)),
            "high": float(c.get("high", 0)),
            "low": float(c.get("low", 0)),
            "close": float(c.get("close", c.get("ltp", 0))),
            "volume": float(c.get("volume", 0)),
        })
    return rows


def validate_data(symbol: str) -> dict:
    """Check data quality before running backtest."""
    path = DATA_DIR / f"{symbol.upper()}.json"
    if not path.exists():
        return {"ok": False, "error": "File not found"}

    data = json.loads(path.read_text())
    candles = data.get("candles", [])
    indicators = data.get("indicators", [])
    issues = []

    if len(candles) < 20:
        issues.append(f"Only {len(candles)} candles — need at least 20")
    if len(indicators) < 20:
        issues.append(f"Only {len(indicators)} indicator rows — need at least 20")
    if not data.get("lot_size"):
        issues.append("lot_size missing — backtester needs this")

    # Check date alignment
    candle_dates = {c["date"] for c in candles}
    ind_dates = {i["date"] for i in indicators}
    overlap = candle_dates & ind_dates
    if len(overlap) < 10:
        issues.append(f"Low date overlap between candles ({len(candle_dates)}) "
                      f"and indicators ({len(ind_dates)}) — only {len(overlap)} matching dates")

    return {
        "ok": len(issues) == 0,
        "symbol": symbol.upper(),
        "candles": len(candles),
        "indicators": len(indicators),
        "date_range": f"{min(candle_dates)} → {max(candle_dates)}" if candle_dates else "N/A",
        "lot_size": data.get("lot_size"),
        "issues": issues,
    }


def list_available_data() -> str:
    """Show all symbols with data available for backtesting."""
    files = sorted(DATA_DIR.glob("*.json"))
    if not files:
        return "No backtest data found. Say 'Fetch backtest data for SYMBOL' to get started."

    lines = [f"\n{'='*50}", "  AVAILABLE BACKTEST DATA", f"{'='*50}"]
    for f in files:
        if f.name == ".gitkeep":
            continue
        v = validate_data(f.stem)
        status = "✅" if v["ok"] else "⚠️"
        lines.append(
            f"  {status} {v['symbol']:15s} {v.get('candles', 0):4d} days | "
            f"lot {v.get('lot_size', '?'):>5} | {v.get('date_range', 'N/A')}"
        )
        for issue in v.get("issues", []):
            lines.append(f"       ⚠️  {issue}")
    lines.append(f"{'='*50}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(list_available_data())
    print("\nFetch instructions for HINDALCO:")
    print(json.dumps(get_fetch_instructions("HINDALCO", months=6), indent=2))
