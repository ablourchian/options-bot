"""
Daily Morning Newsletter — delivered to Discord at 7:00 AM.

Covers:
  • Interest rates (Fed funds, 2yr, 10yr, 30yr Treasury, yield curve)
  • Economy (inflation, jobs, GDP — latest readings + trend)
  • Markets (SPY/QQQ/DIA/IWM pre-market, VIX, futures direction)
  • Top movers in S&P 500 (biggest gap ups/downs pre-market)
  • Key economic events today (earnings, Fed speakers, data releases)
  • Top news headlines from CNBC, Reuters, MarketWatch

Usage:
    python newsletter.py          # send now
    python newsletter.py --html   # also save HTML version
    python newsletter.py --test   # print without sending to Discord
"""

import os
import json
import urllib.request
import urllib.parse
import argparse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
ET           = ZoneInfo("America/New_York")
WEBHOOK_URL  = os.getenv("DISCORD_WEBHOOK")
API_KEY      = os.getenv("ALPACA_API_KEY")
SECRET_KEY   = os.getenv("ALPACA_SECRET_KEY")
RESULTS_DIR  = os.path.join(os.path.dirname(__file__), "results")


# ── Rates & macro via yfinance ────────────────────────────────────────────────

RATE_TICKERS = {
    "^IRX":  "3-Month T-Bill",
    "^FVX":  "5-Year Treasury",
    "^TNX":  "10-Year Treasury",
    "^TYX":  "30-Year Treasury",
    "^VIX":  "VIX",
    "UUP":   "US Dollar ETF",
}

MARKET_TICKERS = {
    "SPY":  "S&P 500 ETF",
    "QQQ":  "Nasdaq 100 ETF",
    "DIA":  "Dow Jones ETF",
    "IWM":  "Russell 2000 ETF",
    "GLD":  "Gold",
    "USO":  "Oil",
    "TLT":  "20-Year Bonds",
}


def fetch_yfinance(symbols: list) -> dict:
    """Fetch latest price + 1-day change for a list of symbols via yfinance."""
    import yfinance as yf
    result = {}
    for sym in symbols:
        try:
            t = yf.Ticker(sym)
            info = t.fast_info
            price  = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
            prev   = getattr(info, "previous_close", None)
            if price and prev:
                chg     = price - prev
                chg_pct = chg / prev * 100
                result[sym] = {
                    "price":   round(price, 3),
                    "prev":    round(prev, 3),
                    "chg":     round(chg, 3),
                    "chg_pct": round(chg_pct, 2),
                }
        except Exception:
            pass
    return result


def fetch_alpaca_quotes(symbols: list) -> dict:
    """Fetch live quotes from Alpaca for ETFs/stocks."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(API_KEY, SECRET_KEY)
    result = {}
    try:
        quotes = client.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=symbols)
        )
        # Also get yesterday's close for change %
        bars = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,
            start=datetime.now(ET) - timedelta(days=5),
            limit=2 * len(symbols),
        ))
        prev_closes = {}
        for sym in symbols:
            try:
                sym_bars = bars[sym]
                if len(sym_bars) >= 2:
                    prev_closes[sym] = sym_bars[-2].close
                elif sym_bars:
                    prev_closes[sym] = sym_bars[-1].close
            except Exception:
                pass

        for sym, q in quotes.items():
            price = (q.bid_price + q.ask_price) / 2 if q.bid_price and q.ask_price else None
            prev  = prev_closes.get(sym)
            if price:
                chg     = price - prev if prev else 0
                chg_pct = chg / prev * 100 if prev else 0
                result[sym] = {
                    "price":   round(price, 2),
                    "prev":    round(prev, 2) if prev else None,
                    "chg":     round(chg, 2),
                    "chg_pct": round(chg_pct, 2),
                }
    except Exception as e:
        pass
    return result


# ── Top pre-market movers ─────────────────────────────────────────────────────

def get_top_movers(n=10) -> dict:
    """Get biggest gap-up and gap-down stocks from S&P 500."""
    from universe import get_sp500
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client  = StockHistoricalDataClient(API_KEY, SECRET_KEY)
    symbols = get_sp500()[:150]  # sample top 150 for speed

    try:
        bars = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,
            start=datetime.now(ET) - timedelta(days=5),
            limit=2 * len(symbols),
        ))
        prev_closes = {}
        for sym in symbols:
            try:
                b = bars[sym]
                if len(b) >= 2:
                    prev_closes[sym] = b[-2].close
            except Exception:
                pass

        quotes = client.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=list(prev_closes.keys()))
        )

        movers = []
        for sym, q in quotes.items():
            if sym not in prev_closes:
                continue
            price = (q.bid_price + q.ask_price) / 2 if q.bid_price and q.ask_price else None
            if not price:
                continue
            prev    = prev_closes[sym]
            chg_pct = (price - prev) / prev * 100
            movers.append({"sym": sym, "price": round(price, 2),
                           "chg_pct": round(chg_pct, 2)})

        movers.sort(key=lambda x: x["chg_pct"], reverse=True)
        return {
            "gainers": movers[:n],
            "losers":  movers[-n:][::-1],
        }
    except Exception:
        return {"gainers": [], "losers": []}


# ── Economic calendar ──────────────────────────────────────────────────────────

def fetch_economic_events() -> list:
    """Fetch today's economic events from Investing.com RSS / FRED."""
    events = []
    try:
        import feedparser
        req = urllib.request.Request(
            "https://www.investing.com/economic-calendar/rss/US",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            feed = feedparser.parse(r.read())
        today = str(date.today())
        for e in feed.entries[:20]:
            pub = getattr(e, "published", "")
            if today in pub or not pub:
                events.append({
                    "title":  e.get("title", ""),
                    "time":   pub[:16] if pub else "",
                })
    except Exception:
        pass

    # Fallback: scrape upcoming events from RSS
    if not events:
        try:
            import feedparser
            req = urllib.request.Request(
                "https://feeds.marketwatch.com/marketwatch/marketpulse/",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                feed = feedparser.parse(r.read())
            for e in feed.entries[:5]:
                events.append({
                    "title": e.get("title","")[:80],
                    "time":  getattr(e, "published","")[:16],
                })
        except Exception:
            pass
    return events[:8]


# ── News headlines ────────────────────────────────────────────────────────────

NEWS_FEEDS = {
    "CNBC Markets":     "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "Reuters Business": "https://feeds.reuters.com/reuters/businessNews",
    "MarketWatch":      "https://feeds.marketwatch.com/marketwatch/topstories/",
    "WSJ Markets":      "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
}


def fetch_headlines(max_per_source=3) -> list:
    import feedparser
    headlines = []
    for source, url in NEWS_FEEDS.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                feed = feedparser.parse(r.read())
            for e in feed.entries[:max_per_source]:
                headlines.append({
                    "title":  e.get("title","")[:100],
                    "source": source,
                    "url":    e.get("link","#"),
                    "pub":    getattr(e,"published","")[:16],
                })
        except Exception:
            pass
    return headlines[:20]


# ── Fear & Greed (CNN) ────────────────────────────────────────────────────────

def fetch_fear_greed() -> dict:
    try:
        url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                                    "Referer": "https://www.cnn.com/"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        score = data["fear_and_greed"]["score"]
        label = data["fear_and_greed"]["rating"]
        prev  = data["fear_and_greed"]["previous_close"]
        return {"score": round(score, 1), "label": label, "prev": round(prev, 1)}
    except Exception:
        return {"score": None, "label": "Unknown", "prev": None}


# ── Yield curve ───────────────────────────────────────────────────────────────

def yield_curve_status(rates: dict) -> str:
    t2  = rates.get("^FVX", {}).get("price")   # using 5yr as proxy
    t10 = rates.get("^TNX", {}).get("price")
    t30 = rates.get("^TYX", {}).get("price")
    if not t2 or not t10:
        return "N/A"
    spread = t10 - t2
    if spread > 0.5:
        return f"Normal (+{spread:.2f}%)"
    elif spread > 0:
        return f"Flat (+{spread:.2f}%)"
    else:
        return f"INVERTED ({spread:.2f}%) ⚠"


# ── Discord sender ────────────────────────────────────────────────────────────

def send_discord(content: str, embeds: list = None):
    if not WEBHOOK_URL:
        print("  DISCORD_WEBHOOK not set")
        return
    payload = {"content": content}
    if embeds:
        payload["embeds"] = embeds
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        WEBHOOK_URL, data=data,
        headers={"Content-Type": "application/json",
                 "User-Agent":   "DiscordBot (options-bot, 1.0)"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print(f"  Discord: HTTP {r.status}")
    except Exception as e:
        print(f"  Discord error: {e}")


def arrow(chg_pct):
    return "▲" if chg_pct >= 0 else "▼"


def pct_str(v):
    if v is None: return "—"
    return f"{'+' if v >= 0 else ''}{v:.2f}%"


# ── HTML generator ────────────────────────────────────────────────────────────

def generate_html(data: dict, today: str) -> str:
    now = datetime.now(ET).strftime("%I:%M %p ET")
    rates   = data.get("rates", {})
    markets = data.get("markets", {})
    gainers = data.get("movers", {}).get("gainers", [])
    losers  = data.get("movers", {}).get("losers", [])
    headlines = data.get("headlines", [])
    fg = data.get("fear_greed", {})
    yc = data.get("yield_curve", "N/A")

    def mrow(sym, name, d):
        if not d: return ""
        color = "#10b981" if d["chg_pct"] >= 0 else "#ef4444"
        return (f"<tr><td style='color:#94a3b8;padding:5px 8px'>{name}</td>"
                f"<td style='color:#f9fafb;font-weight:600;padding:5px 8px'>${d['price']:,.2f}</td>"
                f"<td style='color:{color};padding:5px 8px'>{arrow(d['chg_pct'])} {pct_str(d['chg_pct'])}</td></tr>")

    def rrow(sym, name, d):
        if not d: return ""
        color = "#ef4444" if d["chg_pct"] >= 0 else "#10b981"  # rates up = bad for bonds
        return (f"<tr><td style='color:#94a3b8;padding:5px 8px'>{name}</td>"
                f"<td style='color:#f9fafb;font-weight:600;padding:5px 8px'>{d['price']:.3f}%</td>"
                f"<td style='color:{color};padding:5px 8px'>{arrow(d['chg_pct'])} {pct_str(d['chg_pct'])}</td></tr>")

    market_rows = "".join(mrow(s, MARKET_TICKERS.get(s, s), markets.get(s)) for s in MARKET_TICKERS)
    rate_rows   = "".join(rrow(s, RATE_TICKERS.get(s, s), rates.get(s)) for s in RATE_TICKERS if s != "DXY=F")

    gainer_rows = "".join(
        f"<tr><td style='color:#10b981;font-weight:700;padding:4px 8px'>{g['sym']}</td>"
        f"<td style='color:#f9fafb;padding:4px 8px'>${g['price']:,.2f}</td>"
        f"<td style='color:#10b981;padding:4px 8px'>+{g['chg_pct']:.1f}%</td></tr>"
        for g in gainers[:8]
    )
    loser_rows = "".join(
        f"<tr><td style='color:#ef4444;font-weight:700;padding:4px 8px'>{l['sym']}</td>"
        f"<td style='color:#f9fafb;padding:4px 8px'>${l['price']:,.2f}</td>"
        f"<td style='color:#ef4444;padding:4px 8px'>{l['chg_pct']:.1f}%</td></tr>"
        for l in losers[:8]
    )

    news_rows = "".join(
        f"<tr><td style='padding:6px 8px;border-bottom:1px solid #1e293b'>"
        f"<a href='{h['url']}' target='_blank' style='color:#e2e8f0;text-decoration:none'>{h['title']}</a>"
        f"<div style='color:#475569;font-size:.7rem;margin-top:2px'>{h['source']}  {h['pub']}</div></td></tr>"
        for h in headlines
    )

    fg_score_val = fg.get("score") or 50
    fg_color = "#10b981" if fg_score_val > 55 else "#ef4444" if fg_score_val < 45 else "#f59e0b"
    vix = rates.get("^VIX", {})
    vix_color = "#ef4444" if (vix.get("price") or 0) > 25 else "#f59e0b" if (vix.get("price") or 0) > 18 else "#10b981"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Morning Brief · {today}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0a0a0f;color:#e5e7eb;font-family:'Inter',system-ui,sans-serif;font-size:14px}}
.topbar{{background:linear-gradient(135deg,#0f172a,#1e1b4b,#0f172a);
  border-bottom:1px solid rgba(139,92,246,.25);padding:16px 32px;
  display:flex;justify-content:space-between;align-items:center}}
.title{{font-size:1.2rem;font-weight:800;color:#f9fafb}}
.title span{{color:#8b5cf6}}
.page{{max-width:1300px;margin:0 auto;padding:24px 32px;
  display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px}}
.full{{grid-column:1/-1}}
.half{{grid-column:span 1}}
.card{{background:#111827;border:1px solid #1f2937;border-radius:12px;padding:18px}}
.card h2{{font-size:.72rem;font-weight:700;color:#6b7280;text-transform:uppercase;
  letter-spacing:.1em;margin-bottom:14px;display:flex;align-items:center;gap:8px}}
.card h2::after{{content:'';flex:1;height:1px;background:#1f2937}}
table{{width:100%;border-collapse:collapse}}
.stat-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:10px}}
.stat{{background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:10px 12px;text-align:center}}
.stat .v{{font-size:1.3rem;font-weight:800;line-height:1;margin-bottom:3px}}
.stat .l{{font-size:.65rem;color:#6b7280}}
</style>
</head>
<body>
<div class="topbar">
  <div>
    <div class="title">Morning<span>Brief</span></div>
    <div style="font-size:.76rem;color:#6b7280;margin-top:2px">{today}  ·  {now}  ·  Your daily market update</div>
  </div>
  <div style="display:flex;gap:12px;align-items:center">
    <div style="text-align:center">
      <div style="font-size:1.4rem;font-weight:800;color:{fg_color}">{fg.get('score','—')}</div>
      <div style="font-size:.65rem;color:#6b7280">Fear & Greed</div>
    </div>
    <div style="text-align:center">
      <div style="font-size:1.4rem;font-weight:800;color:{vix_color}">{vix.get('price','—')}</div>
      <div style="font-size:.65rem;color:#6b7280">VIX</div>
    </div>
  </div>
</div>

<div class="page">

  <div class="card half">
    <h2>Interest Rates</h2>
    <table>{rate_rows}</table>
    <div style="margin-top:12px;background:#0f172a;border-radius:6px;padding:8px 10px;font-size:.75rem">
      <span style="color:#6b7280">Yield Curve: </span>
      <span style="color:#e2e8f0;font-weight:600">{yc}</span>
    </div>
  </div>

  <div class="card half">
    <h2>Markets</h2>
    <table>{market_rows}</table>
  </div>

  <div class="card half">
    <h2>Pre-Market Gainers</h2>
    <table>{gainer_rows if gainer_rows else '<tr><td style="color:#6b7280;padding:8px">Loading...</td></tr>'}</table>
  </div>

  <div class="card half">
    <h2>Pre-Market Losers</h2>
    <table>{loser_rows if loser_rows else '<tr><td style="color:#6b7280;padding:8px">Loading...</td></tr>'}</table>
  </div>

  <div class="card full">
    <h2>Top News</h2>
    <table>{news_rows if news_rows else '<tr><td style="color:#6b7280;padding:8px">No headlines</td></tr>'}</table>
  </div>

</div>
</body></html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def build_newsletter() -> dict:
    today = str(date.today())
    now   = datetime.now(ET).strftime("%I:%M %p ET")
    print(f"  [{now}] Fetching rates...")
    rates = fetch_yfinance(list(RATE_TICKERS.keys()))

    print(f"  Fetching market prices...")
    markets = fetch_alpaca_quotes(list(MARKET_TICKERS.keys()))
    # Fallback to yfinance for any missing
    missing = [s for s in MARKET_TICKERS if s not in markets]
    if missing:
        yf_data = fetch_yfinance(missing)
        markets.update(yf_data)

    print(f"  Fetching top movers...")
    movers = get_top_movers(n=8)

    print(f"  Fetching headlines...")
    headlines = fetch_headlines(max_per_source=4)

    print(f"  Fetching Fear & Greed...")
    fg = fetch_fear_greed()

    yc = yield_curve_status(rates)

    return {
        "date":        today,
        "rates":       rates,
        "markets":     markets,
        "movers":      movers,
        "headlines":   headlines,
        "fear_greed":  fg,
        "yield_curve": yc,
    }


def send_discord_newsletter(data: dict):
    today    = data["date"]
    rates    = data["rates"]
    markets  = data["markets"]
    fg       = data["fear_greed"]
    gainers  = data["movers"].get("gainers", [])
    losers   = data["movers"].get("losers", [])
    headlines = data["headlines"]
    yc       = data["yield_curve"]

    def mline(sym, name):
        d = markets.get(sym)
        if not d: return f"`{name:<16}` —"
        clr = "📈" if d["chg_pct"] >= 0 else "📉"
        return f"`{name:<16}` **${d['price']:,.2f}** {clr} {pct_str(d['chg_pct'])}"

    def rline(sym, name):
        d = rates.get(sym)
        if not d: return f"`{name:<18}` —"
        clr = "🔴" if d["chg_pct"] >= 0 else "🟢"
        return f"`{name:<18}` **{d['price']:.3f}%** {clr} {pct_str(d['chg_pct'])}"

    vix = rates.get("^VIX", {})
    fg_score = fg.get("score","—")
    fg_label = fg.get("label","")

    def mshort(sym, label):
        d = markets.get(sym)
        if not d: return f"**{label}** —"
        icon = "▲" if d["chg_pct"] >= 0 else "▼"
        col = "+" if d["chg_pct"] >= 0 else ""
        return f"**{label}** ${d['price']:,.0f}  {icon}{col}{d['chg_pct']:.1f}%"

    def rshort(sym, label):
        d = rates.get(sym)
        if not d: return f"**{label}** —"
        icon = "▲" if d["chg_pct"] >= 0 else "▼"
        return f"**{label}** {d['price']:.2f}%  {icon}"

    markets_text = "\n".join([
        mshort("SPY", "SPY"), mshort("QQQ", "QQQ"), mshort("DIA", "DIA"),
        mshort("IWM", "IWM"), mshort("GLD", "Gold"), mshort("TLT", "TLT"),
    ])

    rates_text = "\n".join([
        rshort("^IRX", "3M"), rshort("^FVX", "5Y"),
        rshort("^TNX", "10Y"), rshort("^TYX", "30Y"),
        f"**Curve** {yc[:30]}",
    ])

    gainers_text = "\n".join(
        f"**{g['sym']}** +{g['chg_pct']:.1f}%  ${g['price']:,.0f}"
        for g in gainers[:5]
    ) or "No data"

    losers_text = "\n".join(
        f"**{l['sym']}** {l['chg_pct']:.1f}%  ${l['price']:,.0f}"
        for l in losers[:5]
    ) or "No data"

    news_text = "\n".join(
        f"• {h['title'][:80]}  —  *{h['source']}*"
        for h in headlines[:6]
    ) or "No headlines"

    embeds = [
        {
            "title":       f"🌅 Morning Brief — {today}",
            "description": (
                f"**Fear & Greed:** {fg_score} — {fg_label}   |   "
                f"**VIX:** {vix.get('price','—')} ({pct_str(vix.get('chg_pct'))})\n"
                f"[**View Full Dashboard →**](https://ablourchian.github.io/options-bot/)"
            ),
            "color": 0x8b5cf6,
            "fields": [
                {"name": "📊 Markets",        "value": markets_text, "inline": True},
                {"name": "💰 Interest Rates", "value": rates_text,   "inline": True},
                {"name": "🚀 Pre-Mkt Gainers","value": gainers_text, "inline": True},
                {"name": "📉 Pre-Mkt Losers", "value": losers_text,  "inline": True},
                {"name": "📰 Top Headlines",  "value": news_text,    "inline": False},
            ],
            "footer":    {"text": "Options Bot Morning Brief · Rates via Yahoo Finance · News via Reuters/CNBC/MarketWatch"},
            "timestamp": f"{today}T07:00:00.000Z",
        }
    ]
    send_discord("", embeds=embeds)


def main():
    parser = argparse.ArgumentParser(description="Daily morning newsletter")
    parser.add_argument("--html",  action="store_true", help="Save HTML version")
    parser.add_argument("--test",  action="store_true", help="Print only, no Discord")
    parser.add_argument("--no-movers", action="store_true", help="Skip slow movers scan")
    args = parser.parse_args()

    today = str(date.today())
    print(f"\n{'='*55}")
    print(f"  MORNING BRIEF  [{today}]")
    print(f"{'='*55}\n")

    data = build_newsletter()
    if args.no_movers:
        data["movers"] = {"gainers": [], "losers": []}

    # Print summary
    rates   = data["rates"]
    markets = data["markets"]
    fg      = data["fear_greed"]
    print(f"\n  Fear & Greed: {fg.get('score','—')} ({fg.get('label','')})")
    print(f"  Yield Curve:  {data['yield_curve']}")
    vix = rates.get("^VIX",{})
    print(f"  VIX:          {vix.get('price','—')}  ({pct_str(vix.get('chg_pct'))})")
    print(f"\n  Markets:")
    for sym, name in MARKET_TICKERS.items():
        d = markets.get(sym)
        if d:
            print(f"    {name:<20} ${d['price']:>9,.2f}   {pct_str(d['chg_pct']):>8}")
    print(f"\n  Top gainers: {[g['sym'] for g in data['movers'].get('gainers',[])[:5]]}")
    print(f"  Top losers:  {[l['sym'] for l in data['movers'].get('losers',[])[:5]]}")
    print(f"\n  Headlines: {len(data['headlines'])} fetched")

    if args.html:
        os.makedirs(RESULTS_DIR, exist_ok=True)
        html = generate_html(data, today)
        path = os.path.join(RESULTS_DIR, f"brief_{today}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\n  HTML saved → {path}")
        import webbrowser
        webbrowser.open(f"file:///{path.replace(os.sep, '/')}")

    if not args.test:
        print(f"\n  Sending to Discord...")
        send_discord_newsletter(data)
    else:
        print(f"\n  [test mode] Discord send skipped")

    print(f"\n  Done.\n")


if __name__ == "__main__":
    main()
