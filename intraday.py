"""
Intraday signal engine — fetches live intraday bars from Alpaca and runs
all technical indicators to produce a composite overbought/oversold signal.

Usage:
    python intraday.py SPY TSLA NVDA          # print signals for these tickers
    python intraday.py --timeframe 1Min SPY   # use 1-minute bars (default: 5Min)
    python intraday.py --bars 78 SPY          # use 78 bars (~6.5 hrs of 5min data)
"""
import os
import argparse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from indicators import composite_signal

load_dotenv()

ET = ZoneInfo("America/New_York")

API_KEY    = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

_stock_client = None

def _client():
    global _stock_client
    if _stock_client is None:
        _stock_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)
    return _stock_client


def fetch_intraday_bars(symbol: str, timeframe_str: str = "5Min",
                        n_bars: int = 78) -> list[dict]:
    """
    Fetch the most recent n_bars intraday bars for symbol.
    Returns list of dicts: {open, high, low, close, volume, timestamp}.
    """
    tf_map = {
        "1Min":  TimeFrame(1, TimeFrameUnit.Minute),
        "5Min":  TimeFrame(5, TimeFrameUnit.Minute),
        "15Min": TimeFrame(15, TimeFrameUnit.Minute),
        "30Min": TimeFrame(30, TimeFrameUnit.Minute),
        "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
    }
    tf = tf_map.get(timeframe_str, TimeFrame(5, TimeFrameUnit.Minute))

    # Go back enough calendar days to guarantee n_bars worth of market hours
    start = datetime.now(ET) - timedelta(days=5)

    try:
        bars = _client().get_stock_bars(StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start,
            limit=n_bars * 3,   # fetch extra, slice to n_bars after filtering
        ))[symbol]
    except Exception as e:
        return []

    result = []
    for b in bars:
        result.append({
            "timestamp": b.timestamp,
            "open":   b.open,
            "high":   b.high,
            "low":    b.low,
            "close":  b.close,
            "volume": b.volume,
        })

    return result[-n_bars:]  # most recent n_bars only


def get_signal(symbol: str, timeframe: str = "5Min", n_bars: int = 78) -> dict:
    """
    Fetch bars and compute composite signal for a single symbol.
    Returns signal dict (see indicators.composite_signal) plus metadata.
    Returns None on failure.
    """
    bars = fetch_intraday_bars(symbol, timeframe, n_bars)
    if len(bars) < 30:
        return None

    sig = composite_signal(bars)
    sig["symbol"]    = symbol
    sig["timeframe"] = timeframe
    sig["n_bars"]    = len(bars)
    sig["last_price"] = bars[-1]["close"]
    sig["timestamp"]  = bars[-1]["timestamp"]
    return sig


def get_signals_bulk(symbols: list[str], timeframe: str = "5Min",
                     n_bars: int = 78) -> dict[str, dict]:
    """Returns {symbol: signal_dict} for a list of symbols."""
    results = {}
    for sym in symbols:
        sig = get_signal(sym, timeframe, n_bars)
        if sig:
            results[sym] = sig
    return results


def signal_emoji(direction: str, confidence: str) -> str:
    if direction == "oversold":
        return "[++]" if confidence == "strong" else "[+]"
    elif direction == "overbought":
        return "[--]" if confidence == "strong" else "[-]"
    return "[ ]"


def signal_arrow(signal: int) -> str:
    if signal >= 3:  return "^^^ "
    if signal >= 1:  return "^   "
    if signal <= -3: return "vvv "
    if signal <= -1: return "v   "
    return "-   "


def print_signals(signals: dict):
    if not signals:
        print("  No signals computed.")
        return

    print(f"\n  {'Symbol':<8} {'Price':>8} {'Signal':>7} {'Dir':<11} {'Conf':<10} "
          f"{'RSI':>6} {'Stoch':>6} {'%VWAP':>7} {'BB%B':>6} {'MACDh':>8}")
    print(f"  {'-'*82}")

    for sym, s in sorted(signals.items(), key=lambda x: x[1]["signal"], reverse=True):
        ind = s["indicators"]
        rsi_v   = f"{ind['rsi']:.0f}" if ind.get("rsi") is not None else "—"
        stoch_v = f"{ind['stoch_k']:.0f}" if ind.get("stoch_k") is not None else "—"
        vwap_v  = f"{ind['pct_from_vwap']:+.1f}%" if ind.get("pct_from_vwap") is not None else "—"
        bb_v    = f"{ind['bb_pct_b']:.2f}" if ind.get("bb_pct_b") is not None else "—"
        macd_v  = f"{ind['macd_hist']:+.4f}" if ind.get("macd_hist") is not None else "—"

        em = signal_emoji(s["direction"], s["confidence"])
        print(
            f"  {sym:<8} ${s['last_price']:>7.2f} "
            f"{signal_arrow(s['signal']):>6}({s['signal']:+d}) "
            f"{s['direction']:<11} {s['confidence']:<10} "
            f"{rsi_v:>6} {stoch_v:>6} {vwap_v:>7} {bb_v:>6} {macd_v:>8}  {em}"
        )

    print()
    print("  Signal: ▲▲▲ strong oversold (bullish) → consider buying calls / selling puts")
    print("          ▼▼▼ strong overbought (bearish) → consider buying puts / selling calls")
    print()


def main():
    parser = argparse.ArgumentParser(description="Intraday signal engine")
    parser.add_argument("symbols", nargs="*", default=["SPY","QQQ","AAPL","TSLA","NVDA"])
    parser.add_argument("--timeframe", default="5Min",
                        choices=["1Min","5Min","15Min","30Min","1Hour"])
    parser.add_argument("--bars", type=int, default=78,
                        help="Number of bars to use (default 78 = ~6.5h of 5min bars)")
    args = parser.parse_args()

    symbols = [s.upper() for s in args.symbols]
    print(f"\n  Intraday signals — {args.timeframe} bars · {args.bars} periods")

    signals = get_signals_bulk(symbols, args.timeframe, args.bars)

    # Print reasoning for top signals
    for sym, s in signals.items():
        if s["signal"] != 0 and s["reasons"]:
            print(f"\n  [{sym}] {signal_emoji(s['direction'], s['confidence'])} "
                  f"{s['direction'].upper()} (score {s['signal']:+d})")
            for r in s["reasons"]:
                print(f"    · {r}")

    print_signals(signals)


if __name__ == "__main__":
    main()
