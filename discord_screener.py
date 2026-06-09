"""
Discord Pre-Market Screener — sends at 9am ET every day.
Shows top gainers/losers for S&P 500, Dow Jones, NASDAQ plus
pre-market movers so you're ready to trade at open.

Usage:
    python discord_screener.py        # send now
    python discord_screener.py --dry  # print without sending
"""
import os
import argparse
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import (
    StockSnapshotRequest, StockBarsRequest, StockLatestQuoteRequest
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

load_dotenv()

ET = ZoneInfo("America/New_York")
API_KEY    = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1513753362536071238/5aQ94nF-7423nEBamiRy88S-0s8GyAQkZ-aUh9STDL3lbC9nwzlTirA6IMG2g2qIIRs9"

# ── Index components ──────────────────────────────────────────────────────────

DOW_30 = [
    "AAPL","AMGN","AXP","BA","CAT","CRM","CSCO","CVX","DIS","DOW",
    "GS","HD","HON","IBM","INTC","JNJ","JPM","KO","MCD","MMM",
    "MRK","MSFT","NKE","PG","TRV","UNH","V","VZ","WBA","WMT"
]

NASDAQ_100 = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","AVGO","COST","NFLX",
    "AMD","ADBE","QCOM","TMUS","TXN","AMAT","INTU","CSCO","BKNG","ISRG",
    "AMGN","HON","VRTX","LRCX","REGN","MU","PANW","KLAC","MELI","SNPS",
    "CDNS","ADI","CRWD","MDLZ","ABNB","CTAS","FTNT","NXPI","MRVL","ADP",
    "ORLY","DASH","WDAY","TEAM","DXCM","MNST","PCAR","FAST","ROST","IDXX",
    "PAYX","BIIB","GEHC","KDP","TTWO","EA","DDOG","ZS","ON","XEL"
]

SP500_SAMPLE = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","JPM","UNH","V",
    "XOM","LLY","AVGO","JNJ","PG","MA","HD","COST","MRK","ABBV",
    "CVX","AMD","PEP","KO","ADBE","WMT","MCD","CRM","BAC","TMO",
    "CSCO","ACN","ABT","TXN","NFLX","QCOM","UPS","AMGN","HON","IBM",
    "GE","CAT","BA","GS","ISRG","AXP","SYK","GILD","ADI","VRTX",
    "INTC","AMAT","LRCX","F","GM","PANW","CRWD","SNOW","PLTR","COIN",
    "HOOD","UBER","LYFT","RIVN","RBLX","DKNG","SOFI","MRVL","ARM","MU",
    "NET","SHOP","SQ","ROKU","DNUT","DASH","ABNB","ZM","COIN","INTC"
]

# Watchlist for pre-market — high-volume names that move on news
PREMARKET_WATCH = list(dict.fromkeys(SP500_SAMPLE + DOW_30 + NASDAQ_100))


def get_client():
    return StockHistoricalDataClient(API_KEY, SECRET_KEY)


# ── Previous close movers (yesterday's close vs day before) ──────────────────

def get_index_movers(symbols, top_n=5):
    sc = get_client()
    symbols = list(dict.fromkeys(symbols))
    snap = sc.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=symbols))

    results = []
    for sym, s in snap.items():
        if not s.daily_bar or not s.previous_daily_bar:
            continue
        prev = s.previous_daily_bar.close
        curr = s.daily_bar.close
        chg  = (curr - prev) / prev * 100
        results.append({"sym": sym, "price": curr, "chg": chg, "vol": s.daily_bar.volume})

    results.sort(key=lambda x: x["chg"], reverse=True)
    return results[:top_n], results[-top_n:][::-1]


# ── Pre-market movers (current quote vs yesterday's close) ───────────────────

def get_premarket_movers(symbols, top_n=10):
    sc = get_client()
    symbols = list(dict.fromkeys(symbols))

    # Get yesterday's close via snapshot
    snap = sc.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=symbols))

    # Get latest quotes (includes pre-market prices)
    try:
        quotes = sc.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=symbols)
        )
    except Exception:
        return [], []

    results = []
    for sym in symbols:
        s = snap.get(sym)
        q = quotes.get(sym)
        if not s or not q or not s.daily_bar:
            continue

        prev_close = s.daily_bar.close
        # Use mid of latest quote as pre-market price
        bid = float(q.bid_price or 0)
        ask = float(q.ask_price or 0)
        if bid <= 0 or ask <= 0:
            continue
        premarket_price = (bid + ask) / 2
        chg = (premarket_price - prev_close) / prev_close * 100

        # Only include meaningful pre-market moves
        if abs(chg) < 0.5:
            continue

        results.append({
            "sym":   sym,
            "prev":  prev_close,
            "price": premarket_price,
            "chg":   chg,
        })

    results.sort(key=lambda x: x["chg"], reverse=True)
    gainers = [r for r in results if r["chg"] > 0][:top_n]
    losers  = [r for r in results if r["chg"] < 0][-top_n:][::-1]
    return gainers, losers


# ── Discord formatting ────────────────────────────────────────────────────────

def mover_rows(items, up):
    if not items:
        return "_No significant moves_"
    lines = []
    for r in items:
        emoji = "🟢" if up else "🔴"
        sign  = "+" if r["chg"] >= 0 else ""
        lines.append(f"{emoji} **{r['sym']}**  `${r['price']:.2f}`  `{sign}{r['chg']:.2f}%`")
    return "\n".join(lines)


def index_embed(name, gainers, losers, color):
    return {
        "title": f"📊 {name} — Yesterday's Close",
        "color": color,
        "fields": [
            {"name": "🏆 Top Gainers", "value": mover_rows(gainers, True),  "inline": True},
            {"name": "📉 Top Losers",  "value": mover_rows(losers,  False), "inline": True},
        ]
    }


def premarket_embed(gainers, losers):
    fields = []
    if gainers:
        fields.append({"name": "🌅 Pre-Market Gainers", "value": mover_rows(gainers, True),  "inline": True})
    if losers:
        fields.append({"name": "🌅 Pre-Market Losers",  "value": mover_rows(losers,  False), "inline": True})
    if not fields:
        fields = [{"name": "Pre-Market", "value": "_No significant pre-market moves yet_", "inline": False}]

    return {
        "title": "🌄 Pre-Market Movers",
        "color": 0xf39c12,
        "description": "Stocks moving before the open — watch these at 9:30am",
        "fields": fields,
    }


def watchlist_embed(premarket_gainers, premarket_losers):
    """Highlight the top pre-market movers as trades to watch at open."""
    watch = []
    for r in (premarket_gainers[:3] + premarket_losers[:3]):
        direction = "PUT fade" if r["chg"] > 0 else "CALL bounce"
        emoji     = "🔻" if r["chg"] > 0 else "🔺"
        watch.append(
            f"{emoji} **{r['sym']}**  {r['chg']:+.1f}%  →  _{direction} if move holds to noon_"
        )

    if not watch:
        return None

    return {
        "title": "🎯 Trade Ideas for Today",
        "color": 0xe74c3c,
        "description": "\n".join(watch),
        "footer": {"text": "Wait for noon ET confirmation before entering. Eyeball the chart first."}
    }


def send_to_discord(embeds, content, dry=False):
    payload = {"content": content, "embeds": embeds}

    if dry:
        import json
        print(json.dumps(payload, indent=2))
        return

    r = requests.post(DISCORD_WEBHOOK, json=payload)
    if r.status_code in (200, 204):
        print("  ✓ Sent to Discord")
    else:
        print(f"  ✗ Failed: {r.status_code} {r.text}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args()

    now = datetime.now(ET).strftime("%A, %b %d %Y — %I:%M %p ET")
    print(f"  Fetching market data — {now}")

    print("  → Index movers...")
    sp_g,  sp_l  = get_index_movers(SP500_SAMPLE,  args.top)
    dj_g,  dj_l  = get_index_movers(DOW_30,        args.top)
    nq_g,  nq_l  = get_index_movers(NASDAQ_100,    args.top)

    print("  → Pre-market movers...")
    pm_g, pm_l = get_premarket_movers(PREMARKET_WATCH, top_n=8)

    embeds = [
        premarket_embed(pm_g, pm_l),
        index_embed("S&P 500",    sp_g, sp_l, 0x2ecc71),
        index_embed("Dow Jones",  dj_g, dj_l, 0x3498db),
        index_embed("NASDAQ 100", nq_g, nq_l, 0x9b59b6),
    ]

    trade_ideas = watchlist_embed(pm_g, pm_l)
    if trade_ideas:
        embeds.append(trade_ideas)

    content = f"🔔 **Good morning — Pre-Market Report** | {now}"
    send_to_discord(embeds, content, dry=args.dry)


if __name__ == "__main__":
    main()
