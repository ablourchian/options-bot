"""
Day trading dashboard — runs BEFORE market open, screens the most volatile
stocks across SP500 / Dow / Nasdaq for intraday swing setups.

For each candidate it shows:
  • Direction: BUY CALL or BUY PUT
  • Best contract (ATM weekly, tightest spread)
  • Entry zone, target (+50-100%), stop (-30-50%)
  • Catalyst / reason

Usage:
    python day_trade.py                          # all 3 indices, top 25 movers
    python day_trade.py --index nasdaq           # Nasdaq only
    python day_trade.py --top 30                 # top 30 candidates
    python day_trade.py --dte 3                  # use options expiring within 3 days
    python day_trade.py --min-score 50           # only show high-confidence setups
"""
import os
import argparse
import csv
import webbrowser
from datetime import date, datetime

from dotenv import load_dotenv

from universe import get_sp500, get_nasdaq100, get_dow30, get_universe
from swing_screener import screen
from news import get_news_for_symbols

load_dotenv()

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


def save_results(results, scan_date):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"daytrade_{scan_date}.csv")
    if not results:
        return path
    fields = [k for k in results[0].keys() if k != "contract"]
    contract_fields = ["c_symbol","c_strike","c_dte","c_expiry","c_bid","c_ask","c_mid","c_spread"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields + contract_fields, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            row = {k: v for k, v in r.items() if k != "contract"}
            c = r.get("contract") or {}
            row["c_symbol"]  = c.get("symbol","")
            row["c_strike"]  = c.get("strike","")
            row["c_dte"]     = c.get("dte","")
            row["c_expiry"]  = c.get("expiry","")
            row["c_bid"]     = c.get("bid","")
            row["c_ask"]     = c.get("ask","")
            row["c_mid"]     = c.get("mid","")
            row["c_spread"]  = c.get("spread_pct","")
            writer.writerow(row)
    print(f"\n  Saved {len(results)} setups -> {path}")
    return path


def print_results(results, min_score=0):
    filtered = [r for r in results if r["score"] >= min_score]
    if not filtered:
        print("  No setups above minimum score.")
        return

    print(f"\n{'='*100}")
    print(f"  DAY TRADING SETUPS — {date.today()}  ({len(filtered)} plays)")
    print(f"{'='*100}")

    calls = [r for r in filtered if r["direction"] == "CALL"]
    puts  = [r for r in filtered if r["direction"] == "PUT"]

    for label, group in [("BUY CALL (Bullish)", calls), ("BUY PUT (Bearish)", puts)]:
        if not group:
            continue
        print(f"\n  {'─'*40}")
        print(f"  {label}")
        print(f"  {'─'*40}")
        for r in group:
            c = r.get("contract")
            conf_bar = "=" * r["confidence"] + "." * (10 - r["confidence"])
            print(f"\n  {r['direction']:>4}  {r['symbol']:<6}  Score: {r['score']:>5.1f}  "
                  f"Confidence: [{conf_bar}] {r['confidence']}/10")
            print(f"  Stock: ${r['spot']:.2f}  ATR: {r['atr_pct']:.1f}%/day  "
                  f"Gap: {r['gap_pct']:+.1f}%  RSI: {r.get('rsi') or '—'}")

            if r.get("catalyst"):
                print(f"  Catalyst: {r['catalyst'][:90]}")

            if r.get("days_to_earnings") is not None:
                dte_earn = r["days_to_earnings"]
                warn = "  *** EARNINGS RISK ***" if dte_earn <= 1 else ""
                print(f"  Earnings: {dte_earn}d away{warn}")

            if c:
                entry   = c["mid"]
                target  = round(entry * 1.75, 2)   # +75% target
                stop    = round(entry * 0.50, 2)    # -50% stop
                print(f"  Contract: {c['symbol']}")
                print(f"  Strike: ${c['strike']}  Expiry: {c['expiry']}  ({c['dte']}d DTE)")
                print(f"  Entry:  ${c['bid']:.2f}–${c['ask']:.2f}  (mid ${entry:.2f})")
                print(f"  Target: ${target:.2f} (+75%)   Stop: ${stop:.2f} (-50%)")
                print(f"  Spread: {c['spread_pct']:.1f}%")
            else:
                print(f"  Contract: none found (check options chain manually)")

    print(f"\n{'='*100}\n")


def generate_dashboard(results, scan_date, news_map=None):
    """Generate an HTML dashboard with clickable news feed dropdowns per card."""
    import json as _json
    news_map = news_map or {}
    calls = [r for r in results if r["direction"] == "CALL"]
    puts  = [r for r in results if r["direction"] == "PUT"]

    def conf_bar(n):
        filled = "█" * n + "░" * (10 - n)
        color  = "#10b981" if n >= 7 else "#f59e0b" if n >= 4 else "#ef4444"
        return f'<span style="color:{color};font-family:monospace;font-size:.8rem">{filled} {n}/10</span>'

    def dir_badge(d):
        if d == "CALL":
            return '<span style="background:#10b981;color:#fff;padding:3px 12px;border-radius:20px;font-weight:700;font-size:.8rem">CALL ▲</span>'
        return '<span style="background:#ef4444;color:#fff;padding:3px 12px;border-radius:20px;font-weight:700;font-size:.8rem">PUT ▼</span>'

    def score_color(s):
        if s >= 70: return "#10b981"
        if s >= 50: return "#f59e0b"
        return "#ef4444"

    def sparkline_svg(bars, direction, width=280, height=56):
        """Render 30-day OHLC as a candlestick SVG sparkline."""
        # bars may come back as a string if loaded from CSV — skip gracefully
        if not bars or not isinstance(bars, list) or len(bars) < 2:
            return '<div style="height:56px;background:#0f172a;border-radius:6px;margin-top:10px"></div>'
        if not isinstance(bars[0], dict):
            return '<div style="height:56px;background:#0f172a;border-radius:6px;margin-top:10px"></div>'
        closes = [b["c"] for b in bars]
        highs  = [b["h"] for b in bars]
        lows   = [b["l"] for b in bars]
        mn = min(lows);  mx = max(highs)
        rng = mx - mn if mx != mn else 1
        pad = 4
        uw  = (width - pad*2) / len(bars)

        def y(v): return pad + (1 - (v - mn) / rng) * (height - pad*2)

        candles = ""
        for i, b in enumerate(bars):
            x   = pad + i * uw + uw * 0.1
            cw  = max(uw * 0.8, 1)
            o, c, h, l = b["o"], b["c"], b["h"], b["l"]
            green = c >= o
            fill  = "#10b981" if green else "#ef4444"
            body_top = min(y(o), y(c))
            body_h   = max(abs(y(o) - y(c)), 1)
            candles += (
                f'<line x1="{x+cw/2:.1f}" y1="{y(h):.1f}" x2="{x+cw/2:.1f}" y2="{y(l):.1f}" stroke="{fill}" stroke-width="1" opacity=".6"/>'
                f'<rect x="{x:.1f}" y="{body_top:.1f}" width="{cw:.1f}" height="{body_h:.1f}" fill="{fill}" rx="0.5"/>'
            )

        # Current price line
        cur_y = y(closes[-1])
        trend_color = "#10b981" if closes[-1] >= closes[0] else "#ef4444"
        price_line = f'<line x1="{pad}" y1="{cur_y:.1f}" x2="{width-pad}" y2="{cur_y:.1f}" stroke="{trend_color}" stroke-width=".8" stroke-dasharray="3,2" opacity=".5"/>'

        return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
                f'style="display:block;margin-top:10px;border-radius:6px;background:#0f172a">'
                f'{candles}{price_line}</svg>')

    def sent_badge(label):
        colors = {"bullish":"#10b981","leaning bullish":"#34d399",
                  "neutral":"#6b7280","leaning bearish":"#f87171","bearish":"#ef4444"}
        c = colors.get(label, "#6b7280")
        return f'<span style="color:{c};font-size:.68rem;font-weight:600;text-transform:uppercase">{label}</span>'

    def news_feed_html(sym, idx):
        nd = news_map.get(sym, {})
        articles = nd.get("articles", [])
        sent     = nd.get("sentiment", {})
        earn     = nd.get("earnings", {})
        if not articles:
            return f'<div id="feed-{idx}" style="display:none;margin-top:12px;padding:10px;background:#0f172a;border-radius:8px;font-size:.75rem;color:#6b7280">No news found for {sym}.</div>'

        items_html = ""
        for a in articles[:8]:
            title   = (a.get("title") or "")[:95]
            source  = a.get("source","")[:30]
            pub     = (a.get("published") or "")[:16]
            url     = a.get("url","#")
            # Simple sentiment on headline
            from news import sentiment_score
            s = sentiment_score(title)
            dot_color = "#10b981" if s["score"] > 0 else "#ef4444" if s["score"] < 0 else "#6b7280"
            hi_badge = '<span style="background:#7c3aed;color:#fff;padding:1px 5px;border-radius:4px;font-size:.6rem;margin-left:4px">!</span>' if s["high_impact"] else ""
            items_html += f"""
            <a href="{url}" target="_blank" style="display:block;text-decoration:none;padding:8px 10px;
               border-bottom:1px solid #1e293b;transition:background .15s"
               onmouseover="this.style.background='#1e293b'" onmouseout="this.style.background='transparent'">
              <div style="display:flex;gap:6px;align-items:flex-start">
                <span style="color:{dot_color};margin-top:3px;font-size:.6rem">●</span>
                <div style="flex:1">
                  <div style="color:#e2e8f0;line-height:1.4">{title}{hi_badge}</div>
                  <div style="color:#475569;font-size:.65rem;margin-top:3px">{source}  {pub}</div>
                </div>
              </div>
            </a>"""

        earn_str = ""
        dte_earn = earn.get("days_to_earnings")
        if dte_earn is not None and dte_earn >= 0:
            earn_str = f'<div style="padding:6px 10px;font-size:.68rem;color:#f59e0b">Earnings in {dte_earn}d · Last EPS {earn.get("earnings_trend","?").upper()} {("("+str(earn.get("eps_surprise_pct",""))+"%)") if earn.get("eps_surprise_pct") else ""}</div>'

        sent_label = sent.get("label","neutral")
        return f"""<div id="feed-{idx}" style="display:none;margin-top:10px;background:#0d1117;
            border:1px solid #1e293b;border-radius:10px;overflow:hidden">
          <div style="display:flex;justify-content:space-between;align-items:center;
               padding:8px 12px;border-bottom:1px solid #1e293b;background:#111827">
            <span style="font-size:.72rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.06em">
              {sym} News Feed
            </span>
            <div style="display:flex;gap:8px;align-items:center">
              {sent_badge(sent_label)}
              <span style="color:#475569;font-size:.65rem">{len(articles)} articles</span>
            </div>
          </div>
          {earn_str}
          {items_html}
        </div>"""

    def card(r, idx):
        c      = r.get("contract") or {}
        mid    = c.get("mid", 0) or 0
        target = round(mid * 1.75, 2)
        stop   = round(mid * 0.50, 2)
        cat    = r.get("catalyst","") or ""
        earn   = r.get("days_to_earnings")
        sym    = r["symbol"]

        earn_html = ""
        if earn is not None:
            color = "#ef4444" if earn <= 1 else "#f59e0b" if earn <= 7 else "#9ca3af"
            warn  = " ⚠ EARNINGS RISK" if earn <= 1 else ""
            earn_html = f'<div style="color:{color};font-size:.72rem;margin-top:4px">Earnings in {earn}d{warn}</div>'

        contract_html = ""
        if c.get("symbol"):
            sp_pct  = c.get("spread_pct") or 0
            sp_dol  = c.get("spread_dollar") or 0
            iv_val  = c.get("iv")
            delta   = c.get("delta")
            gamma   = c.get("gamma")
            theta   = c.get("theta")
            vega    = c.get("vega")
            sp_color = "#10b981" if sp_pct < 5 else "#f59e0b" if sp_pct < 12 else "#ef4444"
            delta_color = "#10b981" if r["direction"]=="CALL" else "#ef4444"

            greeks_html = ""
            if delta is not None:
                def gbox(lbl, val, color="#e5e7eb", tip=""):
                    return (f'<div style="background:#0a0f1a;border:1px solid #1e293b;border-radius:6px;'
                            f'padding:6px 8px;text-align:center" title="{tip}">'
                            f'<div style="font-size:.58rem;color:#6b7280;margin-bottom:2px">{lbl}</div>'
                            f'<div style="font-weight:700;color:{color};font-size:.82rem">{val}</div>'
                            f'</div>')
                greeks_html = f"""
                <div style="margin-top:8px">
                  <div style="font-size:.6rem;color:#475569;text-transform:uppercase;letter-spacing:.08em;margin-bottom:5px">Greeks</div>
                  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:4px">
                    {gbox("Delta", f"{delta:+.3f}", delta_color, "Price sensitivity per $1 move")}
                    {gbox("Gamma", f"{gamma:.4f}", "#a78bfa", "Delta change per $1 move")}
                    {gbox("Theta", f"{theta:.3f}", "#f87171", "Daily time decay")}
                    {gbox("Vega",  f"{vega:.3f}",  "#60a5fa", "Value change per 1% IV move")}
                  </div>
                </div>"""

            iv_html = f'<span style="color:#8b5cf6;font-weight:700">{iv_val:.1f}% IV</span>' if iv_val else ""

            contract_html = f"""
            <div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:12px;margin-top:12px">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                <div style="font-size:.68rem;color:#6b7280;text-transform:uppercase;letter-spacing:.06em">Best Contract</div>
                {iv_html}
              </div>
              <div style="font-weight:700;color:#e2e8f0;font-size:.8rem;font-family:monospace;margin-bottom:8px">{c.get('symbol','')}</div>

              <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:4px">
                {"".join(f'<div style="background:#1e293b;border-radius:6px;padding:5px;text-align:center"><div style="font-size:.58rem;color:#6b7280">{lbl}</div><div style="font-weight:700;color:{vc};font-size:.82rem">{val}</div></div>'
                  for lbl,val,vc in [
                    ("Strike", f"${c.get('strike','')}", "#f9fafb"),
                    ("DTE",    f"{c.get('dte','')}d",   "#f9fafb"),
                    ("Entry",  f"${mid:.2f}",            "#60a5fa"),
                    ("Target", f"${target:.2f}",         "#10b981"),
                    ("Stop",   f"${stop:.2f}",           "#ef4444"),
                    ("Bid/Ask", f"${c.get('bid',0):.2f}/{c.get('ask',0):.2f}", "#9ca3af"),
                  ])}
              </div>

              <div style="margin-top:8px;display:flex;justify-content:space-between;align-items:center;
                   background:#0a0f1a;border:1px solid #1e293b;border-radius:6px;padding:6px 10px">
                <div style="font-size:.7rem;color:#6b7280">Spread</div>
                <div style="display:flex;gap:10px;align-items:center">
                  <span style="color:{sp_color};font-weight:700;font-size:.82rem">{sp_pct:.1f}%</span>
                  <span style="color:#475569;font-size:.72rem">${sp_dol:.2f} per contract</span>
                  <span style="color:#334155;font-size:.65rem">(${sp_dol*100:.0f} / 100 shares)</span>
                </div>
              </div>

              {greeks_html}
            </div>"""

        spark = sparkline_svg(r.get("bars", []), r["direction"])

        nd      = news_map.get(sym, {})
        n_count = len(nd.get("articles", []))
        news_btn = f"""<button onclick="toggleFeed('{idx}',this)"
          style="width:100%;margin-top:10px;padding:7px 10px;background:#1e293b;border:1px solid #334155;
                 border-radius:8px;color:#94a3b8;font-size:.75rem;font-weight:600;cursor:pointer;
                 display:flex;justify-content:space-between;align-items:center;transition:all .15s"
          onmouseover="this.style.background='#263548';this.style.borderColor='#475569'"
          onmouseout="this.style.background='#1e293b';this.style.borderColor='#334155'">
          <span>📰 News Feed ({n_count} articles)</span>
          <span id="arrow-{idx}" style="transition:transform .2s">▼</span>
        </button>"""

        border = "#10b981" if r["direction"] == "CALL" else "#ef4444"
        sc = r["score"]
        return f"""
        <div style="background:#111827;border:1px solid #1f2937;border-left:3px solid {border};
             border-radius:12px;padding:18px;transition:border-color .2s">
          <div style="display:flex;justify-content:space-between;align-items:flex-start">
            <div>
              <div style="font-size:1.5rem;font-weight:800;color:#f9fafb">{sym}</div>
              <div style="margin-top:4px">{dir_badge(r['direction'])}</div>
            </div>
            <div style="text-align:right">
              <div style="font-size:1.8rem;font-weight:800;color:{score_color(sc)}">{sc:.0f}</div>
              <div style="font-size:.62rem;color:#6b7280">score</div>
            </div>
          </div>
          {spark}
          <div style="margin-top:8px;display:flex;justify-content:space-between;align-items:center">
            {conf_bar(r['confidence'])}
            <button onclick="openChart('{sym}')"
              style="padding:3px 10px;background:transparent;border:1px solid #334155;
                     border-radius:6px;color:#94a3b8;font-size:.68rem;cursor:pointer;
                     transition:all .15s;font-weight:600"
              onmouseover="this.style.background='#1e293b';this.style.color='#f9fafb'"
              onmouseout="this.style.background='transparent';this.style.color='#94a3b8'">
              Full Chart
            </button>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-top:10px;font-size:.78rem">
            <div style="color:#9ca3af">Spot  <span style="color:#f9fafb;font-weight:600">${r['spot']:.2f}</span></div>
            <div style="color:#9ca3af">ATR  <span style="color:#f9fafb;font-weight:600">{r['atr_pct']:.1f}%</span></div>
            <div style="color:#9ca3af">Gap  <span style="color:{'#10b981' if (r['gap_pct'] or 0)>0 else '#ef4444'};font-weight:600">{r['gap_pct']:+.1f}%</span></div>
            <div style="color:#9ca3af">RSI  <span style="color:#f9fafb;font-weight:600">{r.get('rsi') or '—'}</span></div>
          </div>
          {f'<div style="margin-top:6px;font-size:.70rem;color:#9ca3af;line-height:1.4">{cat[:100]}</div>' if cat else ''}
          {earn_html}
          {contract_html}
          {news_btn}
          {news_feed_html(sym, idx)}
        </div>"""

    all_cards  = "".join(card(r, f"a{i}") for i, r in enumerate(results[:12]))
    call_cards = "".join(card(r, f"c{i}") for i, r in enumerate(calls[:6]))
    put_cards  = "".join(card(r, f"p{i}") for i, r in enumerate(puts[:6]))

    now = datetime.now().strftime("%I:%M %p ET")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Day Trade Setups · {scan_date}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0a0a0f;color:#e5e7eb;font-family:'Inter',system-ui,sans-serif}}
::-webkit-scrollbar{{width:5px;height:5px}}
::-webkit-scrollbar-thumb{{background:#374151;border-radius:3px}}
.topbar{{background:linear-gradient(135deg,#0f172a,#1e1b4b 50%,#0f172a);
  border-bottom:1px solid rgba(139,92,246,.25);padding:18px 32px;
  display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}}
.title{{font-size:1.2rem;font-weight:800;color:#f9fafb}}
.title span{{color:#8b5cf6}}
.badge{{display:inline-flex;align-items:center;gap:6px;background:#1f2937;
  border:1px solid #374151;border-radius:20px;padding:4px 12px;font-size:.75rem}}
.page{{max-width:1400px;margin:0 auto;padding:24px 32px}}
.stats{{display:flex;gap:12px;margin-bottom:28px;flex-wrap:wrap}}
.stat{{background:#111827;border:1px solid #1f2937;border-radius:10px;padding:14px 20px;min-width:90px}}
.stat .v{{font-size:1.6rem;font-weight:800;line-height:1}}
.stat .l{{font-size:.7rem;color:#6b7280;margin-top:3px}}
.sec{{font-size:.66rem;font-weight:700;color:#6b7280;text-transform:uppercase;
  letter-spacing:.1em;margin-bottom:16px;display:flex;align-items:center;gap:10px}}
.sec::after{{content:'';flex:1;height:1px;background:#1f2937}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:16px;margin-bottom:32px}}
.tabs{{display:flex;gap:4px;background:#111827;border:1px solid #1f2937;border-radius:10px;
  padding:4px;width:fit-content;margin-bottom:20px}}
.tab{{padding:7px 18px;border-radius:7px;font-size:.82rem;font-weight:600;cursor:pointer;
  color:#6b7280;border:none;background:none;transition:all .15s}}
.tab.active{{background:#1f2937;color:#f9fafb}}
.tp{{display:none}}.tp.active{{display:block}}
.disc{{background:#1c1917;border:1px solid #292524;border-radius:8px;
  padding:10px 16px;font-size:.7rem;color:#78716c;margin-top:24px;line-height:1.6}}
</style>
</head>
<body>
<div class="topbar">
  <div>
    <div class="title">Options<span>Bot</span> · Day Trade Setups</div>
    <div style="font-size:.76rem;color:#6b7280;margin-top:3px">{scan_date} · {now} · {len(results)} candidates · click any card for news feed</div>
  </div>
  <div style="display:flex;gap:8px">
    <span class="badge"><span style="color:#10b981">●</span> {len(calls)} CALLS</span>
    <span class="badge"><span style="color:#ef4444">●</span> {len(puts)} PUTS</span>
  </div>
</div>

<div class="page">
  <div class="stats">
    <div class="stat"><div class="v" style="color:#10b981">{len(calls)}</div><div class="l">Calls</div></div>
    <div class="stat"><div class="v" style="color:#ef4444">{len(puts)}</div><div class="l">Puts</div></div>
    <div class="stat"><div class="v" style="color:#f9fafb">{len(results)}</div><div class="l">Screened</div></div>
    <div class="stat"><div class="v" style="color:#f59e0b">{max((r['score'] for r in results), default=0):.0f}</div><div class="l">Top Score</div></div>
    <div class="stat"><div class="v" style="color:#8b5cf6">{sum(1 for r in results if r['score']>=60)}</div><div class="l">High Conv.</div></div>
  </div>

  <div class="tabs">
    <button class="tab active" onclick="sw('all',this)">All Setups</button>
    <button class="tab" onclick="sw('calls',this)">📈 Calls</button>
    <button class="tab" onclick="sw('puts',this)">📉 Puts</button>
  </div>

  <div id="all" class="tp active">
    <div class="sec">All Setups · Ranked by Conviction</div>
    <div class="grid">{all_cards or '<p style="color:#6b7280">No setups found.</p>'}</div>
  </div>
  <div id="calls" class="tp">
    <div class="sec">Call Setups · Bullish Swings</div>
    <div class="grid">{call_cards or '<p style="color:#6b7280">No call setups.</p>'}</div>
  </div>
  <div id="puts" class="tp">
    <div class="sec">Put Setups · Bearish Swings</div>
    <div class="grid">{put_cards or '<p style="color:#6b7280">No put setups.</p>'}</div>
  </div>

  <div class="disc">
    For informational purposes only. Not financial advice. Day trading options involves significant risk of loss.
    Always verify entries with your own analysis. Earnings dates are estimates — verify before trading.
  </div>
</div>

<!-- TradingView Chart Modal -->
<div id="chartModal" style="display:none;position:fixed;inset:0;z-index:1000;
     background:rgba(0,0,0,.85);backdrop-filter:blur(4px);align-items:center;justify-content:center">
  <div style="background:#0d1117;border:1px solid #30363d;border-radius:14px;
       width:min(95vw,1100px);height:min(90vh,680px);display:flex;flex-direction:column;overflow:hidden">
    <div style="display:flex;justify-content:space-between;align-items:center;
         padding:14px 20px;border-bottom:1px solid #21262d;background:#161b22">
      <div style="display:flex;align-items:center;gap:12px">
        <span id="chartSymbolLabel" style="font-size:1.1rem;font-weight:800;color:#f0f6fc"></span>
        <div style="display:flex;gap:4px">
          <button onclick="setInterval2('1')"  class="ivbtn" data-iv="1">1m</button>
          <button onclick="setInterval2('5')"  class="ivbtn active" data-iv="5">5m</button>
          <button onclick="setInterval2('15')" class="ivbtn" data-iv="15">15m</button>
          <button onclick="setInterval2('D')"  class="ivbtn" data-iv="D">1D</button>
        </div>
      </div>
      <button onclick="closeChart()"
        style="background:#21262d;border:1px solid #30363d;border-radius:8px;
               color:#8b949e;padding:6px 14px;cursor:pointer;font-size:.82rem;font-weight:600"
        onmouseover="this.style.background='#30363d'" onmouseout="this.style.background='#21262d'">
        Close ✕
      </button>
    </div>
    <div id="tvContainer" style="flex:1;min-height:0"></div>
  </div>
</div>

<style>
.ivbtn{{background:transparent;border:1px solid #30363d;border-radius:6px;color:#8b949e;
  padding:4px 10px;font-size:.72rem;font-weight:600;cursor:pointer;transition:all .15s}}
.ivbtn:hover,.ivbtn.active{{background:#1f6feb;border-color:#1f6feb;color:#fff}}
</style>

<script>
let _currentSym = '';
let _currentIv  = '5';

function sw(id,el){{
  document.querySelectorAll('.tp').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  el.classList.add('active');
}}

function toggleFeed(idx, btn) {{
  const feed  = document.getElementById('feed-' + idx);
  const arrow = document.getElementById('arrow-' + idx);
  const open  = feed.style.display === 'block';
  feed.style.display  = open ? 'none' : 'block';
  arrow.style.transform = open ? 'rotate(0deg)' : 'rotate(180deg)';
  btn.style.background  = open ? '#1e293b' : '#263548';
  btn.style.borderColor = open ? '#334155' : '#475569';
}}

function openChart(sym) {{
  _currentSym = sym;
  document.getElementById('chartSymbolLabel').textContent = sym;
  document.getElementById('chartModal').style.display = 'flex';
  document.body.style.overflow = 'hidden';
  renderTV(sym, _currentIv);
}}

function closeChart() {{
  document.getElementById('chartModal').style.display = 'none';
  document.getElementById('tvContainer').innerHTML = '';
  document.body.style.overflow = '';
}}

function setInterval2(iv) {{
  _currentIv = iv;
  document.querySelectorAll('.ivbtn').forEach(b => {{
    b.classList.toggle('active', b.dataset.iv === iv);
  }});
  renderTV(_currentSym, iv);
}}

function renderTV(sym, interval) {{
  const container = document.getElementById('tvContainer');
  container.innerHTML = '<div id="tv_widget_div" style="width:100%;height:100%"></div>';
  const script = document.createElement('script');
  script.src = 'https://s3.tradingview.com/tv.js';
  script.onload = function() {{
    new TradingView.widget({{
      autosize: true,
      symbol: sym,
      interval: interval,
      timezone: 'America/New_York',
      theme: 'dark',
      style: '1',
      locale: 'en',
      toolbar_bg: '#161b22',
      enable_publishing: false,
      hide_side_toolbar: false,
      allow_symbol_change: true,
      studies: ['RSI@tv-basicstudies','MACD@tv-basicstudies','Volume@tv-basicstudies'],
      container_id: 'tv_widget_div',
    }});
  }};
  container.appendChild(script);
}}

document.getElementById('chartModal').addEventListener('click', function(e) {{
  if(e.target === this) closeChart();
}});
document.addEventListener('keydown', function(e) {{
  if(e.key === 'Escape') closeChart();
}});
</script>
</body></html>"""

    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"daytrade_dashboard_{scan_date}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def publish_to_github(html_path, scan_date):
    """Push dashboard to GitHub Pages."""
    import shutil, subprocess
    docs_dir = os.path.join(os.path.dirname(__file__), "docs")
    os.makedirs(docs_dir, exist_ok=True)
    shutil.copy(html_path, os.path.join(docs_dir, "index.html"))
    try:
        subprocess.run(["git","add","docs/index.html"], cwd=os.path.dirname(__file__), check=True)
        subprocess.run(["git","commit","-m",f"Day trade setups {scan_date}"],
                       cwd=os.path.dirname(__file__), check=True)
        subprocess.run(["git","push"], cwd=os.path.dirname(__file__), check=True)
        return "https://ablourchian.github.io/options-bot/"
    except Exception as e:
        print(f"  [!] GitHub push failed: {e}")
        return None


def notify_discord(results, scan_date, pages_url=None):
    try:
        from notifier import WEBHOOK_URL, _send_json, _grade_color
        import json, urllib.request

        calls = [r for r in results if r["direction"] == "CALL"]
        puts  = [r for r in results if r["direction"] == "PUT"]

        fields = []
        for r in results[:6]:
            c = r.get("contract") or {}
            mid = c.get("mid", 0) or 0
            fields.append({
                "name": f"{'📈' if r['direction']=='CALL' else '📉'} {r['symbol']} — {r['direction']}",
                "value": (
                    f"```"
                    f"Score  {r['score']:>5.0f}   Conf  {r['confidence']}/10\n"
                    f"Spot   ${r['spot']:>7.2f}   ATR   {r['atr_pct']:.1f}%\n"
                    f"Gap    {r['gap_pct']:>+6.1f}%   RSI   {r.get('rsi') or '—'}\n"
                    + (f"Entry  ${mid:.2f}   Target ${mid*1.75:.2f} (+75%)\n"
                       f"Contract: {c.get('symbol','—')}" if c else "No liquid contract found")
                    + "```"
                ),
                "inline": False,
            })

        desc = f"**{len(calls)}** call setups · **{len(puts)}** put setups · **{len(results)}** screened"
        if pages_url:
            desc += f"\n\n[**View Dashboard →**]({pages_url})"

        payload = {"embeds": [{"title": f"📊 Day Trade Setups — {scan_date}",
                                "description": desc, "color": 0x8b5cf6, "fields": fields,
                                "footer": {"text": "Options Bot · Day Trading · Alpaca"}}]}
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(WEBHOOK_URL, data=data,
                   headers={"Content-Type":"application/json",
                            "User-Agent":"DiscordBot (options-bot, 1.0)"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"  Discord notified (HTTP {resp.status})")
    except Exception as e:
        print(f"  [!] Discord failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="Day trade swing screener")
    parser.add_argument("--index", choices=["sp500","nasdaq","dow","all"], default="all")
    parser.add_argument("--top-stocks", type=int, default=60,
                        help="Volatile stocks to screen (default 60)")
    parser.add_argument("--top", type=int, default=20,
                        help="Top setups to show (default 20)")
    parser.add_argument("--dte",  type=int, default=45,
                        help="Max DTE for options (default 45)")
    parser.add_argument("--min-score", type=float, default=40)
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()

    today = str(date.today())
    print(f"\n{'='*60}")
    print(f"  DAY TRADE SCREENER  [{today}]")
    print(f"{'='*60}")

    # 1. Universe
    if args.index == "sp500":   symbols = get_sp500()
    elif args.index == "nasdaq": symbols = get_nasdaq100()
    elif args.index == "dow":    symbols = get_dow30()
    else:                        symbols = get_universe()
    print(f"\n  Universe: {len(symbols)} symbols")

    # 2. Get most volatile by HV
    from historical_vol import historical_volatility
    from concurrent.futures import ThreadPoolExecutor, as_completed
    print(f"  Ranking by 30d HV...")
    hv_scores = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = {pool.submit(historical_volatility, sym,
                            __import__('alpaca.data.historical', fromlist=['StockHistoricalDataClient']).StockHistoricalDataClient(
                                os.getenv('ALPACA_API_KEY'), os.getenv('ALPACA_SECRET_KEY'))): sym
                for sym in symbols}
        done = 0
        for fut in as_completed(futs):
            sym = futs[fut]
            hv  = fut.result()
            done += 1
            if hv:
                hv_scores.append((sym, hv["hv_current"]))
            if done % 50 == 0:
                print(f"    {done}/{len(symbols)}...")

    hv_scores.sort(key=lambda x: x[1], reverse=True)
    top_syms = [s for s, _ in hv_scores[:args.top_stocks]]
    print(f"  Top {len(top_syms)} most volatile: {top_syms[:10]}...")

    # 3. News
    print(f"\n  Fetching news...")
    try:
        news_map = get_news_for_symbols(top_syms, hours=24, include_rss=False)
        print(f"    {len(news_map)} tickers with news")
    except Exception as e:
        print(f"    [!] {e}")
        news_map = {}

    # 4. Screen
    print(f"\n  Screening for swing setups...")
    results = screen(top_syms, news_map=news_map, top_n=args.top, dte_max=args.dte)

    # 5. Print
    print_results(results, min_score=args.min_score)

    # 6. Save
    save_results(results, today)

    # 7. Dashboard
    dash = generate_dashboard(results, today, news_map=news_map)
    print(f"  Dashboard -> {dash}")
    webbrowser.open(f"file:///{dash.replace(os.sep, '/')}")

    # 8. Push to GitHub Pages + Discord
    pages_url = None
    if not args.no_push:
        pages_url = publish_to_github(dash, today)
        if pages_url:
            print(f"  Published -> {pages_url}")

    notify_discord(results, today, pages_url)


if __name__ == "__main__":
    main()
