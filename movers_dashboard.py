"""
Movers Dashboard — top 10 gainers and losers with Greeks and news.

Usage:
    python movers_dashboard.py          # generate + open in browser
    python movers_dashboard.py --no-open
"""
import os
import argparse
import webbrowser
from datetime import date, timedelta
from dotenv import load_dotenv

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockSnapshotRequest, StockLatestQuoteRequest
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionSnapshotRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest

from news import get_news_for_symbols
from intraday import get_signal

load_dotenv()

API_KEY    = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

UNIVERSE = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","JPM","UNH","V",
    "XOM","LLY","AVGO","JNJ","PG","MA","HD","COST","MRK","ABBV",
    "CVX","AMD","PEP","KO","ADBE","WMT","MCD","CRM","BAC","TMO",
    "CSCO","ACN","ABT","TXN","NFLX","QCOM","UPS","AMGN","HON","IBM",
    "GE","CAT","BA","GS","ISRG","AXP","SYK","GILD","ADI","VRTX",
    "INTC","AMAT","LRCX","F","GM","PANW","CRWD","SNOW","PLTR","COIN",
    "HOOD","UBER","LYFT","RIVN","RBLX","DKNG","SOFI","MRVL","ARM","MU",
    "NET","SHOP","SQ","ROKU","DASH","ABNB","ZM","ON","DNUT","QCOM"
]


def get_movers():
    sc = StockHistoricalDataClient(API_KEY, SECRET_KEY)
    syms = list(dict.fromkeys(UNIVERSE))
    snap = sc.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=syms))
    results = []
    for sym, s in snap.items():
        if not s.daily_bar or not s.previous_daily_bar:
            continue
        prev = s.previous_daily_bar.close
        curr = s.daily_bar.close
        chg  = (curr - prev) / prev * 100

        # Overnight gap: previous close → today's open
        today_open   = s.daily_bar.open
        overnight_chg = (today_open - prev) / prev * 100 if prev else 0

        results.append({
            "sym":           sym,
            "price":         curr,
            "prev":          prev,
            "chg":           chg,
            "volume":        s.daily_bar.volume,
            "high":          s.daily_bar.high,
            "low":           s.daily_bar.low,
            "open":          today_open,
            "overnight_chg": overnight_chg,
        })
    results.sort(key=lambda x: x["chg"], reverse=True)
    return results[:10], results[-10:][::-1]


def rate_trade(stock, sig):
    """
    Score this stock as an intraday reversal trade candidate.
    Returns (stars: int 1-5, rating: str, reason: str, trade: str)
    """
    chg  = stock["chg"]
    vol  = stock["volume"]
    high = stock["high"]
    low  = stock["low"]
    price = stock["price"]
    score = 0
    reasons = []
    trade_dir = "PUT" if chg > 0 else "CALL"

    # Move size — bigger move = better fade candidate
    if abs(chg) >= 10:
        score += 2; reasons.append(f"{abs(chg):.1f}% extreme move")
    elif abs(chg) >= 6:
        score += 1; reasons.append(f"{abs(chg):.1f}% strong move")

    # Volume — high volume confirms conviction in the move
    if vol >= 2_000_000:
        score += 1; reasons.append("high volume")
    elif vol < 300_000:
        score -= 1; reasons.append("low volume")

    # Price near high/low of day — holding the extreme
    day_range = high - low
    if day_range > 0:
        pos = (price - low) / day_range
        if chg > 0 and pos >= 0.85:
            score += 1; reasons.append("near day high — stretched")
        elif chg < 0 and pos <= 0.15:
            score += 1; reasons.append("near day low — stretched")

    # Intraday signal confirmation
    if sig:
        direction  = sig.get("direction", "neutral")
        confidence = sig.get("confidence", "weak")
        ind        = sig.get("indicators", {})
        rsi        = ind.get("rsi")
        vwap       = ind.get("pct_from_vwap")

        if chg > 0 and direction == "overbought":
            score += 2; reasons.append(f"intraday overbought ({confidence})")
        elif chg < 0 and direction == "oversold":
            score += 2; reasons.append(f"intraday oversold ({confidence})")

        if rsi:
            if chg > 0 and rsi > 65:
                score += 1; reasons.append(f"RSI {rsi:.0f} overbought")
            elif chg < 0 and rsi < 35:
                score += 1; reasons.append(f"RSI {rsi:.0f} oversold")

        if vwap is not None:
            if chg > 0 and vwap > 1.5:
                score += 1; reasons.append(f"VWAP +{vwap:.1f}% stretched")
            elif chg < 0 and vwap < -1.5:
                score += 1; reasons.append(f"VWAP {vwap:.1f}% stretched")
    else:
        reasons.append("no intraday signal yet")

    # Clamp score to 1-5 stars
    stars = max(1, min(5, score))

    rating_map = {
        5: ("STRONG FADE", "#10b981"),
        4: ("GOOD FADE",   "#34d399"),
        3: ("WATCH",       "#f59e0b"),
        2: ("WEAK",        "#f97316"),
        1: ("SKIP",        "#ef4444"),
    }
    rating, color = rating_map[stars]
    return stars, rating, color, " · ".join(reasons) if reasons else "insufficient data", trade_dir


def get_greeks(sym, spot):
    try:
        tc = TradingClient(API_KEY, SECRET_KEY, paper=True)
        oc = OptionHistoricalDataClient(API_KEY, SECRET_KEY)
        exp_min = (date.today() + timedelta(days=5)).isoformat()
        exp_max = (date.today() + timedelta(days=21)).isoformat()

        greeks = {}
        for otype in ["call", "put"]:
            req = GetOptionContractsRequest(
                underlying_symbols=[sym],
                expiration_date_gte=exp_min,
                expiration_date_lte=exp_max,
                type=otype,
                limit=50,
            )
            contracts = tc.get_option_contracts(req).option_contracts
            if not contracts:
                continue
            atm = min(contracts, key=lambda c: abs(float(c.strike_price or 0) - spot))
            snap = oc.get_option_snapshot(OptionSnapshotRequest(symbol_or_symbols=atm.symbol))
            s = snap.get(atm.symbol)
            if s and s.greeks:
                g = s.greeks
                greeks[otype] = {
                    "symbol":  atm.symbol,
                    "strike":  float(atm.strike_price),
                    "expiry":  str(atm.expiration_date),
                    "iv":      round((s.implied_volatility or 0) * 100, 1),
                    "delta":   round(g.delta or 0, 3),
                    "gamma":   round(g.gamma or 0, 4),
                    "theta":   round(g.theta or 0, 4),
                    "vega":    round(g.vega or 0, 4),
                }
        return greeks
    except Exception:
        return {}


def get_news(symbols):
    try:
        results = get_news_for_symbols(symbols, hours=24)
        out = {}
        for sym in symbols:
            articles = results.get(sym, {}).get("articles", [])
            out[sym] = articles[:3]
        return out
    except Exception:
        return {sym: [] for sym in symbols}


def sentiment_color(title):
    pos = ["surge","jump","rally","beat","gain","soar","rise","up","high","record","strong","buy"]
    neg = ["drop","fall","crash","miss","loss","down","low","weak","sell","cut","concern","warn"]
    t = title.lower()
    if any(w in t for w in pos):
        return "#10b981"
    if any(w in t for w in neg):
        return "#ef4444"
    return "#6b7280"


def greek_row(label, call_val, put_val, color_fn=None):
    return f"""<tr>
      <td style="color:#6b7280;font-size:.75rem;padding:4px 8px">{label}</td>
      <td style="color:#60a5fa;font-weight:600;font-size:.8rem;padding:4px 8px;text-align:right">{call_val}</td>
      <td style="color:#f87171;font-weight:600;font-size:.8rem;padding:4px 8px;text-align:right">{put_val}</td>
    </tr>"""


def build_card(stock, greeks, news_items, is_gainer, sig=None):
    sym   = stock["sym"]
    price = stock["price"]
    chg   = stock["chg"]
    vol   = stock["volume"]
    high  = stock["high"]
    low   = stock["low"]

    chg_color = "#10b981" if chg >= 0 else "#ef4444"
    arrow     = "▲" if chg >= 0 else "▼"
    border    = "rgba(16,185,129,.3)" if is_gainer else "rgba(239,68,68,.3)"
    badge_bg  = "rgba(16,185,129,.1)" if is_gainer else "rgba(239,68,68,.1)"
    label     = "GAINER" if is_gainer else "LOSER"
    label_col = "#10b981" if is_gainer else "#ef4444"

    # Trade rating
    stars, rating, r_color, reason, trade_dir = rate_trade(stock, sig)
    star_html = "★" * stars + "☆" * (5 - stars)
    rating_html = f"""<div style="background:rgba(0,0,0,.3);border:1px solid {r_color};border-radius:8px;
      padding:10px 12px;margin-top:14px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div>
          <span style="color:{r_color};font-weight:800;font-size:.85rem">{star_html}</span>
          <span style="color:{r_color};font-weight:700;font-size:.8rem;margin-left:8px">{rating}</span>
        </div>
        <span style="background:{r_color};color:#000;font-size:.65rem;font-weight:800;
          padding:2px 8px;border-radius:20px">FADE → {trade_dir}</span>
      </div>
      <div style="color:#6b7280;font-size:.7rem;margin-top:4px;line-height:1.4">{reason}</div>
    </div>"""

    # Greeks section
    call = greeks.get("call", {})
    put  = greeks.get("put",  {})
    has_greeks = bool(call or put)

    def gv(d, k): return str(d.get(k, "—")) if d else "—"

    greeks_html = ""
    if has_greeks:
        greeks_html = f"""
        <div style="margin-top:14px">
          <div style="font-size:.65rem;font-weight:700;color:#6b7280;text-transform:uppercase;
            letter-spacing:.08em;margin-bottom:6px">ATM Options Greeks</div>
          <table style="width:100%;border-collapse:collapse">
            <tr>
              <td style="font-size:.7rem;color:#6b7280;padding:2px 8px"></td>
              <td style="font-size:.7rem;color:#60a5fa;font-weight:700;padding:2px 8px;text-align:right">CALL</td>
              <td style="font-size:.7rem;color:#f87171;font-weight:700;padding:2px 8px;text-align:right">PUT</td>
            </tr>
            {greek_row("Strike",  f"${gv(call,'strike')}",   f"${gv(put,'strike')}")}
            {greek_row("Expiry",  gv(call,'expiry'),          gv(put,'expiry'))}
            {greek_row("IV %",    f"{gv(call,'iv')}%",        f"{gv(put,'iv')}%")}
            {greek_row("Delta",   gv(call,'delta'),           gv(put,'delta'))}
            {greek_row("Gamma",   gv(call,'gamma'),           gv(put,'gamma'))}
            {greek_row("Theta",   gv(call,'theta'),           gv(put,'theta'))}
            {greek_row("Vega",    gv(call,'vega'),            gv(put,'vega'))}
          </table>
        </div>"""
    else:
        greeks_html = '<div style="color:#4b5563;font-size:.75rem;margin-top:12px;font-style:italic">Greeks unavailable (market closed)</div>'

    # News section
    news_html = ""
    if news_items:
        news_html = '<div style="margin-top:14px"><div style="font-size:.65rem;font-weight:700;color:#6b7280;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px">Latest News</div>'
        for n in news_items:
            title = n.get("title", n.get("headline", ""))
            url   = n.get("url", "#")
            src   = n.get("source", "")
            sc    = sentiment_color(title)
            news_html += f"""<div style="margin-bottom:8px;padding:8px;background:#0f172a;border-radius:6px;border-left:2px solid {sc}">
              <a href="{url}" target="_blank" style="color:#e5e7eb;text-decoration:none;font-size:.75rem;line-height:1.4;display:block">{title[:100]}{"..." if len(title)>100 else ""}</a>
              <div style="color:#4b5563;font-size:.65rem;margin-top:3px">{src}</div>
            </div>"""
        news_html += "</div>"
    else:
        news_html = '<div style="color:#4b5563;font-size:.75rem;margin-top:12px;font-style:italic">No recent news</div>'

    return f"""<div style="background:#111827;border:1px solid {border};border-radius:14px;padding:18px;break-inside:avoid">
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div>
          <div style="font-size:1.4rem;font-weight:800;color:#f9fafb">{sym}</div>
          <div style="font-size:1.1rem;font-weight:700;color:#e5e7eb;margin-top:2px">${price:.2f}</div>
        </div>
        <div style="text-align:right">
          <div style="background:{badge_bg};color:{label_col};font-size:.65rem;font-weight:700;
            padding:3px 8px;border-radius:20px;letter-spacing:.06em">{label}</div>
          <div style="font-size:1.3rem;font-weight:800;color:{chg_color};margin-top:4px">{arrow} {abs(chg):.2f}%</div>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-top:12px">
        <div style="background:#0f172a;border-radius:6px;padding:6px 8px">
          <div style="font-size:.62rem;color:#6b7280">High</div>
          <div style="font-size:.82rem;font-weight:600;color:#e5e7eb">${high:.2f}</div>
        </div>
        <div style="background:#0f172a;border-radius:6px;padding:6px 8px">
          <div style="font-size:.62rem;color:#6b7280">Low</div>
          <div style="font-size:.82rem;font-weight:600;color:#e5e7eb">${low:.2f}</div>
        </div>
        <div style="background:#0f172a;border-radius:6px;padding:6px 8px">
          <div style="font-size:.62rem;color:#6b7280">Volume</div>
          <div style="font-size:.82rem;font-weight:600;color:#e5e7eb">{vol/1e6:.1f}M</div>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:6px">
        <div style="background:#0f172a;border-radius:6px;padding:6px 8px">
          <div style="font-size:.62rem;color:#6b7280">Prev Close</div>
          <div style="font-size:.82rem;font-weight:600;color:#e5e7eb">${stock["prev"]:.2f}</div>
        </div>
        <div style="background:#0f172a;border-radius:6px;padding:6px 8px">
          <div style="font-size:.62rem;color:#6b7280">Overnight Gap</div>
          <div style="font-size:.82rem;font-weight:600;color:{'#10b981' if stock['overnight_chg'] >= 0 else '#ef4444'}">
            {'+' if stock['overnight_chg'] >= 0 else ''}{stock['overnight_chg']:.2f}%
            <span style="color:#6b7280;font-size:.7rem"> (open ${stock['open']:.2f})</span>
          </div>
        </div>
      </div>
      {rating_html}
      {greeks_html}
      {news_html}
    </div>"""


def build_html(gainers, losers, all_greeks, all_news, all_signals=None):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York")).strftime("%b %d, %Y  %I:%M %p ET")

    sigs = all_signals or {}
    gainer_cards = "\n".join(build_card(s, all_greeks.get(s["sym"],{}), all_news.get(s["sym"],[]), True,  sigs.get(s["sym"])) for s in gainers)
    loser_cards  = "\n".join(build_card(s, all_greeks.get(s["sym"],{}), all_news.get(s["sym"],[]), False, sigs.get(s["sym"])) for s in losers)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Movers Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0a0a0f;color:#e5e7eb;font-family:'Inter',system-ui,sans-serif;font-size:14px}}
.topbar{{background:linear-gradient(135deg,#0f172a 0%,#1e1b4b 50%,#0f172a 100%);
  border-bottom:1px solid rgba(139,92,246,.2);padding:20px 32px;
  display:flex;justify-content:space-between;align-items:center}}
.page{{max-width:1400px;margin:0 auto;padding:24px 32px}}
.section-title{{font-size:.7rem;font-weight:700;color:#6b7280;text-transform:uppercase;
  letter-spacing:.12em;margin-bottom:16px;display:flex;align-items:center;gap:8px}}
.section-title::after{{content:'';flex:1;height:1px;background:#1f2937}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px;margin-bottom:40px}}
</style>
</head>
<body>
<div class="topbar">
  <div>
    <div style="font-size:1.25rem;font-weight:800;color:#f9fafb">📈 <span style="color:#8b5cf6">Movers</span> Dashboard</div>
    <div style="font-size:.78rem;color:#6b7280;margin-top:3px">{now} · Top 10 Gainers & Losers · Greeks · News</div>
  </div>
</div>
<div class="page">
  <div style="margin-bottom:32px">
    <div class="section-title">🟢 Top 10 Gainers</div>
    <div class="grid">{gainer_cards}</div>
  </div>
  <div>
    <div class="section-title">🔴 Top 10 Losers</div>
    <div class="grid">{loser_cards}</div>
  </div>
</div>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    print("  Fetching top movers...")
    gainers, losers = get_movers()
    all_syms = [s["sym"] for s in gainers + losers]

    print(f"  Gainers: {[s['sym'] for s in gainers]}")
    print(f"  Losers:  {[s['sym'] for s in losers]}")

    print("  Fetching Greeks...")
    sc = StockHistoricalDataClient(API_KEY, SECRET_KEY)
    quotes = sc.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=all_syms))
    all_greeks = {}
    for s in gainers + losers:
        sym = s["sym"]
        q   = quotes.get(sym)
        spot = (q.ask_price + q.bid_price) / 2 if q and q.ask_price and q.bid_price else s["price"]
        print(f"    {sym}...", end=" ", flush=True)
        all_greeks[sym] = get_greeks(sym, spot)
        print("✓")

    print("  Fetching intraday signals...")
    all_signals = {}
    for sym in all_syms:
        print(f"    {sym}...", end=" ", flush=True)
        all_signals[sym] = get_signal(sym, timeframe="5Min", n_bars=78)
        print("✓")

    print("  Fetching news...")
    all_news = get_news(all_syms)

    print("  Building dashboard...")
    html = build_html(gainers, losers, all_greeks, all_news, all_signals)
    out  = os.path.join(RESULTS_DIR, f"movers_{date.today()}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  → {out}")

    if not args.no_open:
        webbrowser.open(f"file:///{out.replace(os.sep, '/')}")


if __name__ == "__main__":
    main()
