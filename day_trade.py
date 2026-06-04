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


def generate_dashboard(results, scan_date):
    """Generate an HTML dashboard focused on day trading setups."""
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

    def card(r):
        c = r.get("contract") or {}
        mid    = c.get("mid", 0) or 0
        target = round(mid * 1.75, 2)
        stop   = round(mid * 0.50, 2)
        cat    = r.get("catalyst","") or ""
        earn   = r.get("days_to_earnings")
        earn_html = ""
        if earn is not None:
            color = "#ef4444" if earn <= 1 else "#f59e0b" if earn <= 7 else "#9ca3af"
            warn  = " ⚠ EARNINGS RISK" if earn <= 1 else ""
            earn_html = f'<div style="color:{color};font-size:.72rem;margin-top:4px">Earnings in {earn}d{warn}</div>'

        contract_html = ""
        if c:
            contract_html = f"""
            <div style="background:#0f172a;border-radius:8px;padding:12px;margin-top:12px">
              <div style="font-size:.72rem;color:#6b7280;margin-bottom:6px;text-transform:uppercase;letter-spacing:.06em">Best Contract</div>
              <div style="font-weight:700;color:#e5e7eb;font-size:.85rem">{c.get('symbol','')}</div>
              <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-top:8px">
                <div style="background:#1e293b;border-radius:6px;padding:6px;text-align:center">
                  <div style="font-size:.62rem;color:#6b7280">Strike</div>
                  <div style="font-weight:700;color:#f9fafb">${c.get('strike','')}</div>
                </div>
                <div style="background:#1e293b;border-radius:6px;padding:6px;text-align:center">
                  <div style="font-size:.62rem;color:#6b7280">DTE</div>
                  <div style="font-weight:700;color:#f9fafb">{c.get('dte','')}d</div>
                </div>
                <div style="background:#1e293b;border-radius:6px;padding:6px;text-align:center">
                  <div style="font-size:.62rem;color:#6b7280">Spread</div>
                  <div style="font-weight:700;color:#{'10b981' if (c.get('spread_pct') or 99) < 5 else 'f59e0b'}">{c.get('spread_pct','')}%</div>
                </div>
              </div>
              <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-top:6px">
                <div style="background:#1e293b;border-radius:6px;padding:6px;text-align:center">
                  <div style="font-size:.62rem;color:#6b7280">Entry</div>
                  <div style="font-weight:700;color:#60a5fa">${mid:.2f}</div>
                </div>
                <div style="background:#1e293b;border-radius:6px;padding:6px;text-align:center">
                  <div style="font-size:.62rem;color:#6b7280">Target</div>
                  <div style="font-weight:700;color:#10b981">${target:.2f} <span style="font-size:.65rem">(+75%)</span></div>
                </div>
                <div style="background:#1e293b;border-radius:6px;padding:6px;text-align:center">
                  <div style="font-size:.62rem;color:#6b7280">Stop</div>
                  <div style="font-weight:700;color:#ef4444">${stop:.2f} <span style="font-size:.65rem">(-50%)</span></div>
                </div>
              </div>
            </div>"""

        border = "#10b981" if r["direction"] == "CALL" else "#ef4444"
        sc = r["score"]
        return f"""
        <div style="background:#111827;border:1px solid #1f2937;border-left:3px solid {border};
             border-radius:12px;padding:18px;transition:all .2s"
             onmouseover="this.style.borderColor='{border}';this.style.background='#131d2e'"
             onmouseout="this.style.borderColor='#1f2937';this.style.borderLeftColor='{border}';this.style.background='#111827'">
          <div style="display:flex;justify-content:space-between;align-items:flex-start">
            <div>
              <div style="font-size:1.5rem;font-weight:800;color:#f9fafb">{r['symbol']}</div>
              <div style="margin-top:4px">{dir_badge(r['direction'])}</div>
            </div>
            <div style="text-align:right">
              <div style="font-size:1.8rem;font-weight:800;color:{score_color(sc)}">{sc:.0f}</div>
              <div style="font-size:.65rem;color:#6b7280">score</div>
            </div>
          </div>
          <div style="margin-top:10px">{conf_bar(r['confidence'])}</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:10px;font-size:.78rem">
            <div style="color:#9ca3af">Spot  <span style="color:#f9fafb;font-weight:600">${r['spot']:.2f}</span></div>
            <div style="color:#9ca3af">ATR  <span style="color:#f9fafb;font-weight:600">{r['atr_pct']:.1f}%</span></div>
            <div style="color:#9ca3af">Gap  <span style="color:{'#10b981' if (r['gap_pct'] or 0)>0 else '#ef4444'};font-weight:600">{r['gap_pct']:+.1f}%</span></div>
            <div style="color:#9ca3af">RSI  <span style="color:#f9fafb;font-weight:600">{r.get('rsi') or '—'}</span></div>
          </div>
          {f'<div style="margin-top:8px;font-size:.72rem;color:#9ca3af;line-height:1.4">{cat[:100]}</div>' if cat else ''}
          {earn_html}
          {contract_html}
        </div>"""

    call_cards = "".join(card(r) for r in calls[:6])
    put_cards  = "".join(card(r) for r in puts[:6])
    all_cards  = "".join(card(r) for r in results[:12])

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
  display:flex;justify-content:space-between;align-items:center}}
.title{{font-size:1.2rem;font-weight:800;color:#f9fafb}}
.title span{{color:#8b5cf6}}
.badge{{display:inline-flex;align-items:center;gap:6px;background:#1f2937;
  border:1px solid #374151;border-radius:20px;padding:4px 12px;font-size:.75rem}}
.page{{max-width:1400px;margin:0 auto;padding:24px 32px}}
.stats{{display:flex;gap:16px;margin-bottom:28px;flex-wrap:wrap}}
.stat{{background:#111827;border:1px solid #1f2937;border-radius:10px;padding:14px 20px;min-width:100px}}
.stat .v{{font-size:1.7rem;font-weight:800;line-height:1}}
.stat .l{{font-size:.72rem;color:#6b7280;margin-top:3px}}
.section-title{{font-size:.68rem;font-weight:700;color:#6b7280;text-transform:uppercase;
  letter-spacing:.1em;margin-bottom:16px;display:flex;align-items:center;gap:10px}}
.section-title::after{{content:'';flex:1;height:1px;background:#1f2937}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;margin-bottom:32px}}
.tabs{{display:flex;gap:4px;background:#111827;border:1px solid #1f2937;border-radius:10px;
  padding:4px;width:fit-content;margin-bottom:20px}}
.tab{{padding:7px 18px;border-radius:7px;font-size:.82rem;font-weight:600;cursor:pointer;
  color:#6b7280;border:none;background:none;transition:all .15s}}
.tab.active{{background:#1f2937;color:#f9fafb}}
.tab-pane{{display:none}}.tab-pane.active{{display:block}}
.disclaimer{{background:#1c1917;border:1px solid #292524;border-radius:8px;
  padding:10px 16px;font-size:.72rem;color:#78716c;margin-top:24px;line-height:1.6}}
</style>
</head>
<body>
<div class="topbar">
  <div>
    <div class="title">Options<span>Bot</span> · Day Trade Setups</div>
    <div style="font-size:.78rem;color:#6b7280;margin-top:3px">{scan_date} · Generated {now} · {len(results)} candidates screened</div>
  </div>
  <div style="display:flex;gap:8px">
    <span class="badge"><span style="color:#10b981">●</span> {len(calls)} CALLS</span>
    <span class="badge"><span style="color:#ef4444">●</span> {len(puts)} PUTS</span>
  </div>
</div>

<div class="page">
  <div class="stats">
    <div class="stat"><div class="v" style="color:#10b981">{len(calls)}</div><div class="l">Call Setups</div></div>
    <div class="stat"><div class="v" style="color:#ef4444">{len(puts)}</div><div class="l">Put Setups</div></div>
    <div class="stat"><div class="v" style="color:#f9fafb">{len(results)}</div><div class="l">Total Screened</div></div>
    <div class="stat"><div class="v" style="color:#f59e0b">{max((r['score'] for r in results), default=0):.0f}</div><div class="l">Top Score</div></div>
    <div class="stat"><div class="v" style="color:#8b5cf6">{sum(1 for r in results if r['score']>=60)}</div><div class="l">High Conviction</div></div>
  </div>

  <div class="tabs">
    <button class="tab active" onclick="sw('all',this)">All Setups</button>
    <button class="tab" onclick="sw('calls',this)">📈 Calls</button>
    <button class="tab" onclick="sw('puts',this)">📉 Puts</button>
  </div>

  <div id="all" class="tab-pane active">
    <div class="section-title">All Setups · Ranked by Conviction</div>
    <div class="grid">{all_cards if all_cards else '<p style="color:#6b7280">No setups found for today.</p>'}</div>
  </div>
  <div id="calls" class="tab-pane">
    <div class="section-title">Call Setups · Bullish Swings</div>
    <div class="grid">{call_cards if call_cards else '<p style="color:#6b7280">No call setups.</p>'}</div>
  </div>
  <div id="puts" class="tab-pane">
    <div class="section-title">Put Setups · Bearish Swings</div>
    <div class="grid">{put_cards if put_cards else '<p style="color:#6b7280">No put setups.</p>'}</div>
  </div>

  <div class="disclaimer">
    ⚠ For informational purposes only. Not financial advice. Day trading options involves significant risk of loss.
    Always verify entries with your own analysis. Never trade based solely on automated signals.
    Earnings dates shown are estimates — verify before trading.
  </div>
</div>
<script>
function sw(id,el){{
  document.querySelectorAll('.tab-pane').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  el.classList.add('active');
}}
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
    parser.add_argument("--dte",  type=int, default=60,
                        help="Max DTE for options (default 60, min is always 30)")
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
    dash = generate_dashboard(results, today)
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
