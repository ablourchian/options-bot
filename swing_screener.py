"""
Intraday swing screener for day trading options.

Ranks stocks by their swing potential BEFORE market open, then identifies
direction (CALL or PUT) and the best contract to trade.

Scoring (100 pts):
  ATR%           25 pts  — avg daily range as % of price (higher = better for swings)
  Pre-mkt gap    20 pts  — pre-market move size + direction alignment
  Volume surge   20 pts  — today's early volume vs 20-day avg
  News catalyst  20 pts  — fresh news + earnings proximity
  Tech momentum  15 pts  — RSI, MACD direction from prior day closes

Direction: CALL if bullish signals dominate, PUT if bearish, SKIP if mixed.
"""

import os
import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest, StockLatestBarRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.trading.enums import AssetStatus, ContractType
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionLatestQuoteRequest

from indicators import rsi, macd

load_dotenv()

ET = ZoneInfo("America/New_York")
API_KEY    = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

_stock  = StockHistoricalDataClient(API_KEY, SECRET_KEY)
_trade  = TradingClient(API_KEY, SECRET_KEY, paper=True)
_option = OptionHistoricalDataClient(API_KEY, SECRET_KEY)


# ── Data fetchers ─────────────────────────────────────────────────────────────

def get_daily_bars(symbol, n=30):
    try:
        bars = _stock.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=datetime.now(ET) - timedelta(days=n * 2),
            limit=n,
        ))[symbol]
        return [{"open": b.open, "high": b.high, "low": b.low,
                 "close": b.close, "volume": b.volume, "timestamp": b.timestamp}
                for b in bars]
    except Exception:
        return []


def get_premarket_quote(symbol):
    """Returns (premarket_price, prev_close) or (None, None)."""
    try:
        q = _stock.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=symbol)
        )[symbol]
        price = (q.bid_price + q.ask_price) / 2 if q.bid_price and q.ask_price else None
        return price
    except Exception:
        return None


def get_current_bar(symbol):
    try:
        bar = _stock.get_stock_latest_bar(
            StockLatestBarRequest(symbol_or_symbols=symbol)
        )[symbol]
        return {"open": bar.open, "high": bar.high, "low": bar.low,
                "close": bar.close, "volume": bar.volume}
    except Exception:
        return None


# ── Metrics ───────────────────────────────────────────────────────────────────

def calc_atr(bars, period=14):
    """Average True Range as % of last close."""
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        hl = bars[i]["high"] - bars[i]["low"]
        hc = abs(bars[i]["high"] - bars[i-1]["close"])
        lc = abs(bars[i]["low"]  - bars[i-1]["close"])
        trs.append(max(hl, hc, lc))
    atr = sum(trs[-period:]) / period
    pct = atr / bars[-1]["close"] * 100
    return round(pct, 3)


def calc_avg_volume(bars, period=20):
    vols = [b["volume"] for b in bars[-period:]]
    return sum(vols) / len(vols) if vols else 0


def calc_gap(bars, current_price):
    """Pre-market gap % vs prior close."""
    if not bars or current_price is None:
        return None
    prev_close = bars[-1]["close"]
    return round((current_price - prev_close) / prev_close * 100, 3)


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_atr(atr_pct):
    """25 pts. >3% daily range is great for day trading."""
    if atr_pct is None: return 0
    return min(atr_pct / 3.0, 1.0) * 25


def score_gap(gap_pct, direction):
    """
    20 pts. Gap aligning with direction = full score.
    Large gap (1%+) = more conviction.
    """
    if gap_pct is None: return 10
    aligned = (gap_pct > 0 and direction == "CALL") or \
              (gap_pct < 0 and direction == "PUT")
    magnitude = min(abs(gap_pct) / 2.0, 1.0) * 20
    return magnitude if aligned else magnitude * 0.2


def score_volume(vol_ratio):
    """20 pts. 2x average volume = great. 0.5x = weak."""
    if vol_ratio is None: return 0
    return min((vol_ratio - 0.5) / 1.5, 1.0) * 20


def score_news(news_data, direction):
    """20 pts. News sentiment aligned with direction."""
    if not news_data: return 10
    sent  = news_data.get("sentiment", {}).get("score", 0)
    earn  = news_data.get("earnings", {})
    dte   = earn.get("days_to_earnings")
    pts   = 10

    if direction == "CALL":
        pts += min(sent * 3, 8)
    else:
        pts += min(-sent * 3, 8)

    # Earnings today/tomorrow = avoid (binary risk)
    if dte is not None and 0 <= dte <= 1:
        pts = 0

    return max(0, min(20, pts))


def score_momentum(bars, direction):
    """15 pts. RSI + MACD from daily bars aligned with direction."""
    if len(bars) < 30: return 7
    r  = rsi(bars, 14)
    mc = macd(bars)
    pts = 7

    if direction == "CALL":
        if r and 40 < r < 70:   pts += 4  # not overbought
        if r and r < 40:        pts += 2  # oversold bounce setup
        if mc.get("histogram") and mc["histogram"] > 0: pts += 4
    else:
        if r and 30 < r < 60:   pts += 4  # not oversold
        if r and r > 60:        pts += 2  # overbought breakdown
        if mc.get("histogram") and mc["histogram"] < 0: pts += 4

    return min(pts, 15)


def determine_direction(bars, gap_pct, news_data):
    """
    Returns "CALL", "PUT", or "SKIP" with a confidence score 0-10.
    """
    votes = 0

    # Gap direction
    if gap_pct is not None:
        if gap_pct > 0.5:  votes += 2
        elif gap_pct < -0.5: votes -= 2
        elif gap_pct > 0.1: votes += 1
        elif gap_pct < -0.1: votes -= 1

    # Technical momentum (daily)
    if len(bars) >= 26:
        r  = rsi(bars, 14)
        mc = macd(bars)
        if r:
            if r < 35:   votes += 2   # oversold → bounce → CALL
            elif r > 65: votes -= 2   # overbought → fade → PUT
            elif r > 55: votes -= 1
            elif r < 45: votes += 1
        if mc.get("histogram"):
            votes += 1 if mc["histogram"] > 0 else -1

    # News sentiment
    if news_data:
        sent = news_data.get("sentiment", {}).get("score", 0)
        votes += max(-2, min(2, sent))

    # Trend (last 5 days)
    if len(bars) >= 5:
        trend = bars[-1]["close"] - bars[-5]["close"]
        if trend > 0: votes += 1
        else: votes -= 1

    confidence = min(abs(votes), 10)
    if votes >= 2:   return "CALL", confidence
    if votes <= -2:  return "PUT",  confidence
    return "SKIP",   confidence


# ── Options finder ────────────────────────────────────────────────────────────

def find_best_contract(symbol, spot, direction, dte_min=0, dte_max=45):
    """Find the best ATM/slightly OTM weekly option for a day trade."""
    today = date.today()
    exp_start = today + timedelta(days=dte_min)
    exp_end   = today + timedelta(days=dte_max)
    ctype = ContractType.CALL if direction == "CALL" else ContractType.PUT

    # Strike range: ATM ±5%
    low  = spot * 0.95
    high = spot * 1.05

    try:
        contracts = _trade.get_option_contracts(GetOptionContractsRequest(
            underlying_symbols=[symbol],
            status=AssetStatus.ACTIVE,
            expiration_date_gte=exp_start,
            expiration_date_lte=exp_end,
            type=ctype,
            strike_price_gte=str(int(low)),
            strike_price_lte=str(int(high) + 1),
            limit=20,
        )).option_contracts
    except Exception:
        return None

    if not contracts:
        return None

    # Get quotes
    syms = [c.symbol for c in contracts]
    try:
        quotes = _option.get_option_latest_quote(
            OptionLatestQuoteRequest(symbol_or_symbols=syms)
        )
    except Exception:
        return None

    best = None
    best_score = -1

    for c in contracts:
        q = quotes.get(c.symbol)
        if not q or not q.bid_price or not q.ask_price or q.bid_price == 0:
            continue

        mid = (q.bid_price + q.ask_price) / 2
        if mid <= 0:
            continue

        spread_pct = (q.ask_price - q.bid_price) / mid
        strike = float(c.strike_price)
        dte = (c.expiration_date - today).days

        # Score: tight spread, close to ATM, some DTE left
        moneyness = abs(strike - spot) / spot  # 0 = ATM
        score = (1 - spread_pct) * 40 + (1 - moneyness * 20) * 40 + min(dte / 5, 1) * 20

        if score > best_score and spread_pct < 0.20 and mid > 0.05:
            best_score = score
            best = {
                "symbol":     c.symbol,
                "strike":     strike,
                "dte":        dte,
                "expiry":     str(c.expiration_date),
                "bid":        q.bid_price,
                "ask":        q.ask_price,
                "mid":        round(mid, 2),
                "spread_pct": round(spread_pct * 100, 1),
                "spread_dollar": round(q.ask_price - q.bid_price, 2),
                "_spot":      spot,
                "_otype":     "call" if direction == "CALL" else "put",
            }

    if best:
        # Compute IV + Greeks for the best contract
        try:
            from implied_vol import implied_volatility
            from greeks import greeks as compute_greeks
            T = best["dte"] / 365.0
            iv = implied_volatility(best["mid"], best["_spot"], best["strike"], T, 0.045, best["_otype"])
            if iv:
                g = compute_greeks(best["_spot"], best["strike"], T, 0.045, iv, best["_otype"])
                best["iv"]    = round(iv * 100, 1)
                best["delta"] = g["delta"]
                best["gamma"] = g["gamma"]
                best["theta"] = g["theta"]
                best["vega"]  = g["vega"]
        except Exception:
            pass

    return best


# ── Main screener ─────────────────────────────────────────────────────────────

def screen(symbols, news_map=None, top_n=20, dte_max=5):
    """
    Screen a list of symbols and return ranked swing candidates.
    Returns list of result dicts sorted by score descending.
    """
    results = []
    total = len(symbols)

    for i, sym in enumerate(symbols, 1):
        print(f"  [{i}/{total}] {sym}", end="  ", flush=True)

        bars = get_daily_bars(sym, n=30)
        if len(bars) < 10:
            print("(no data)")
            continue

        spot = get_premarket_quote(sym)
        if spot is None:
            spot = bars[-1]["close"]

        atr_pct   = calc_atr(bars)
        avg_vol   = calc_avg_volume(bars)
        gap_pct   = calc_gap(bars, spot)
        news_data = (news_map or {}).get(sym)

        # Direction
        direction, confidence = determine_direction(bars, gap_pct, news_data)

        if direction == "SKIP":
            print(f"skip (no direction)")
            continue

        # Scores
        s_atr  = score_atr(atr_pct)
        s_gap  = score_gap(gap_pct, direction)
        s_vol  = score_volume(None)   # volume not available pre-market
        s_news = score_news(news_data, direction)
        s_mom  = score_momentum(bars, direction)
        total_score = s_atr + s_gap + s_vol + s_news + s_mom

        # RSI for display
        r  = rsi(bars, 14)
        mc = macd(bars)

        print(f"{direction}  score={total_score:.0f}  ATR={atr_pct:.1f}%  gap={gap_pct:+.1f}%")

        results.append({
            "symbol":     sym,
            "direction":  direction,
            "confidence": confidence,
            "score":      round(total_score, 1),
            "spot":       round(spot, 2),
            "atr_pct":    atr_pct,
            "gap_pct":    gap_pct,
            "avg_vol":    avg_vol,
            "rsi":        round(r, 1) if r else None,
            "macd_hist":  round(mc.get("histogram") or 0, 4),
            "news_sentiment": (news_data or {}).get("sentiment", {}).get("label", "neutral"),
            "catalyst":   (news_data or {}).get("catalyst", ""),
            "days_to_earnings": (news_data or {}).get("earnings", {}).get("days_to_earnings"),
            "s_atr":  round(s_atr, 1),
            "s_gap":  round(s_gap, 1),
            "s_news": round(s_news, 1),
            "s_mom":  round(s_mom, 1),
            "contract": None,  # filled below
            "bars": [{"c": b["close"], "h": b["high"], "l": b["low"], "o": b["open"]} for b in bars[-30:]],
        })

    # Sort by score
    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:top_n]

    # Find best contracts for top picks only
    print(f"\n  Finding best contracts for top {len(top)} picks...")
    for r in top:
        if r["direction"] != "SKIP":
            r["contract"] = find_best_contract(
                r["symbol"], r["spot"], r["direction"],
                dte_min=30, dte_max=dte_max
            )
            tag = r["contract"]["symbol"] if r["contract"] else "no contract"
            print(f"    {r['symbol']} {r['direction']}: {tag}")

    return top
