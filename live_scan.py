"""
Live scanner — refreshes prices, intraday signals, and Greeks every 60 seconds
during market hours. Uses the morning's top candidates from day_trade.py and
updates the dashboard + pushes to GitHub Pages each cycle.

Run this AFTER the morning scan (day_trade.py) has already identified candidates.

Usage:
    python live_scan.py               # runs until market close (4 PM ET)
    python live_scan.py --interval 60 # refresh every 60 seconds (default)
    python live_scan.py --no-push     # don't push to GitHub (local only)
    python live_scan.py --symbols COIN MRVL HOOD  # override symbols to watch
"""
import os
import csv
import time
import shutil
import subprocess
import argparse
import webbrowser
from datetime import date, datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()
ET = ZoneInfo("America/New_York")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


# ── Market hours check ────────────────────────────────────────────────────────

def market_is_open():
    now = datetime.now(ET)
    if now.weekday() >= 5:  # Saturday/Sunday
        return False
    h = now.hour + now.minute / 60
    return 9.5 <= h < 16.0   # 9:30 AM – 4:00 PM ET


def minutes_to_open():
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return None
    open_mins = 9 * 60 + 30
    cur_mins  = now.hour * 60 + now.minute
    return max(0, open_mins - cur_mins)


def minutes_to_close():
    now = datetime.now(ET)
    close_mins = 16 * 60
    cur_mins   = now.hour * 60 + now.minute
    return max(0, close_mins - cur_mins)


# ── Load morning candidates ───────────────────────────────────────────────────

def load_candidates(override_symbols=None):
    """Load today's top setups from the morning scan CSV."""
    today = str(date.today())
    path  = os.path.join(RESULTS_DIR, f"daytrade_{today}.csv")

    if not os.path.exists(path):
        print(f"  [!] No morning scan found at {path}")
        print(f"      Run day_trade.py first, then start live_scan.py")
        return []

    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            for field in ["score","spot","atr_pct","gap_pct","rsi","confidence","days_to_earnings"]:
                try:
                    r[field] = float(r[field]) if r.get(field) not in ("","None",None) else None
                except (ValueError, KeyError):
                    r[field] = None
            if r.get("confidence"):
                r["confidence"] = int(r["confidence"])
            r["contract"] = {
                "symbol":     r.get("c_symbol",""),
                "strike":     r.get("c_strike",""),
                "dte":        r.get("c_dte",""),
                "expiry":     r.get("c_expiry",""),
                "bid":        float(r["c_bid"])  if r.get("c_bid")  else 0,
                "ask":        float(r["c_ask"])  if r.get("c_ask")  else 0,
                "mid":        float(r["c_mid"])  if r.get("c_mid")  else 0,
                "spread_pct": r.get("c_spread",""),
            } if r.get("c_symbol") else {}
            rows.append(r)

    if override_symbols:
        override_symbols = [s.upper() for s in override_symbols]
        rows = [r for r in rows if r["symbol"] in override_symbols]
        missing = set(override_symbols) - {r["symbol"] for r in rows}
        for sym in missing:
            rows.append({
                "symbol": sym, "direction": "CALL", "score": 50,
                "confidence": 3, "spot": 0, "atr_pct": 0, "gap_pct": 0,
                "rsi": None, "macd_hist": 0, "news_sentiment": "neutral",
                "catalyst": "", "days_to_earnings": None,
                "s_atr":0,"s_gap":0,"s_news":0,"s_mom":0,
                "contract": {}, "bars": [],
            })

    return rows


# ── Live refresh ──────────────────────────────────────────────────────────────

def refresh_row(r):
    """
    Update a single candidate row with live spot price, intraday signal,
    refreshed contract quote, and recomputed Greeks.
    Returns updated row.
    """
    from swing_screener import get_premarket_quote, get_daily_bars, find_best_contract
    from intraday import get_signal
    from implied_vol import implied_volatility
    from greeks import greeks as compute_greeks

    sym = r["symbol"]

    # 1. Live spot price
    spot = get_premarket_quote(sym)
    if spot:
        r["spot"] = round(spot, 2)

    # 2. Intraday signal
    sig = get_signal(sym, timeframe="5Min", n_bars=78)
    if sig:
        r["intraday_signal"]    = sig.get("signal", 0)
        r["intraday_direction"] = sig.get("direction", "neutral")
        r["intraday_confidence"]= sig.get("confidence", "weak")
        r["rsi"] = round(sig["indicators"].get("rsi") or r.get("rsi") or 50, 1)
        r["pct_from_vwap"] = sig["indicators"].get("pct_from_vwap")
        r["macd_hist"]     = sig["indicators"].get("macd_hist")
        r["stoch_k"]       = sig["indicators"].get("stoch_k")

    # 3. Refresh contract quote + Greeks
    if r.get("contract") and r["contract"].get("symbol"):
        try:
            from alpaca.data.historical.option import OptionHistoricalDataClient
            from alpaca.data.requests import OptionLatestQuoteRequest
            API_KEY    = os.getenv("ALPACA_API_KEY")
            SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
            oc = OptionHistoricalDataClient(API_KEY, SECRET_KEY)
            q  = oc.get_option_latest_quote(
                OptionLatestQuoteRequest(symbol_or_symbols=[r["contract"]["symbol"]])
            ).get(r["contract"]["symbol"])
            if q and q.bid_price and q.ask_price:
                mid = round((q.bid_price + q.ask_price) / 2, 2)
                sp  = round((q.ask_price - q.bid_price) / mid * 100, 1) if mid > 0 else 0
                r["contract"]["bid"]        = q.bid_price
                r["contract"]["ask"]        = q.ask_price
                r["contract"]["mid"]        = mid
                r["contract"]["spread_pct"] = sp
                r["contract"]["spread_dollar"] = round(q.ask_price - q.bid_price, 2)

                # Recompute Greeks with fresh quote
                try:
                    dte = r["contract"].get("dte") or 30
                    if isinstance(dte, str):
                        dte = int(dte) if dte.isdigit() else 30
                    T   = float(dte) / 365.0
                    strike = r["contract"].get("strike")
                    if isinstance(strike, str):
                        strike = float(strike.replace("$","")) if strike else spot
                    otype = "call" if r.get("direction") == "CALL" else "put"
                    iv = implied_volatility(mid, r["spot"], float(strike), T, 0.045, otype)
                    if iv:
                        g = compute_greeks(r["spot"], float(strike), T, 0.045, iv, otype)
                        r["contract"]["iv"]    = round(iv * 100, 1)
                        r["contract"]["delta"] = g["delta"]
                        r["contract"]["gamma"] = g["gamma"]
                        r["contract"]["theta"] = g["theta"]
                        r["contract"]["vega"]  = g["vega"]
                except Exception:
                    pass
        except Exception:
            pass

    return r


def refresh_all(candidates):
    """Refresh all candidates in parallel."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    updated = [None] * len(candidates)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(refresh_row, dict(r)): i for i, r in enumerate(candidates)}
        for fut in as_completed(futs):
            idx = futs[fut]
            try:
                updated[idx] = fut.result()
            except Exception as e:
                updated[idx] = candidates[idx]
                print(f"    [!] Refresh failed for {candidates[idx]['symbol']}: {e}")
    return updated


# ── Dashboard push ────────────────────────────────────────────────────────────

def push_dashboard(candidates, scan_date, news_map, cycle, push_to_github):
    from day_trade import generate_dashboard, publish_to_github

    dash = generate_dashboard(candidates, scan_date, news_map=news_map)

    if push_to_github:
        try:
            docs_dir = os.path.join(os.path.dirname(__file__), "docs")
            os.makedirs(docs_dir, exist_ok=True)
            shutil.copy(dash, os.path.join(docs_dir, "index.html"))
            subprocess.run(["git","add","docs/index.html"],
                           cwd=os.path.dirname(__file__), check=True,
                           capture_output=True)
            subprocess.run(["git","commit","-m",
                            f"Live update {scan_date} cycle {cycle}"],
                           cwd=os.path.dirname(__file__), check=True,
                           capture_output=True)
            subprocess.run(["git","push"],
                           cwd=os.path.dirname(__file__), check=True,
                           capture_output=True)
        except Exception as e:
            print(f"    [!] Push failed: {e}")

    return dash


# ── Add auto-refresh to dashboard ─────────────────────────────────────────────

def inject_live_bar(html_path, cycle, next_refresh_secs, n_calls, n_puts):
    """Inject a live status bar + auto-reload into the dashboard HTML."""
    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    now_str = datetime.now(ET).strftime("%I:%M:%S %p ET")
    mins_left = minutes_to_close()

    live_bar = f"""
<div id="livebar" style="position:fixed;bottom:0;left:0;right:0;z-index:999;
     background:linear-gradient(90deg,#0f172a,#1e1b4b,#0f172a);
     border-top:1px solid rgba(139,92,246,.3);
     padding:8px 24px;display:flex;justify-content:space-between;align-items:center;
     font-size:.72rem;color:#94a3b8">
  <div style="display:flex;gap:16px;align-items:center">
    <span style="display:flex;align-items:center;gap:6px">
      <span style="width:7px;height:7px;border-radius:50%;background:#10b981;
            animation:pulse 1s infinite"></span>
      <span style="color:#10b981;font-weight:700">LIVE</span>
    </span>
    <span>Cycle <strong style="color:#e2e8f0">{cycle}</strong></span>
    <span>Updated <strong style="color:#e2e8f0">{now_str}</strong></span>
    <span><strong style="color:#10b981">{n_calls}</strong> calls &nbsp;
          <strong style="color:#ef4444">{n_puts}</strong> puts</span>
    <span style="color:#6b7280">{mins_left:.0f} min to close</span>
  </div>
  <div style="display:flex;align-items:center;gap:10px">
    <span>Next refresh in <strong id="countdown" style="color:#8b5cf6">{next_refresh_secs}s</strong></span>
    <button onclick="location.reload()" style="background:#1e293b;border:1px solid #334155;
      border-radius:6px;color:#94a3b8;padding:4px 10px;cursor:pointer;font-size:.7rem">
      Refresh Now
    </button>
  </div>
</div>
<style>
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
body{{padding-bottom:44px}}
</style>
<script>
(function(){{
  let secs = {next_refresh_secs};
  const el = document.getElementById('countdown');
  const t = setInterval(function(){{
    secs--;
    if(el) el.textContent = secs + 's';
    if(secs <= 0){{ clearInterval(t); location.reload(); }}
  }}, 1000);
}})();
</script>"""

    html = html.replace("</body>", live_bar + "\n</body>")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Live options scanner — refreshes every minute")
    parser.add_argument("--interval", type=int, default=60, help="Refresh interval in seconds (default 60)")
    parser.add_argument("--no-push",  action="store_true", help="Don't push to GitHub Pages")
    parser.add_argument("--symbols",  nargs="*", default=None, help="Override symbols to watch")
    parser.add_argument("--open-browser", action="store_true", help="Open dashboard in browser on start")
    args = parser.parse_args()

    today = str(date.today())
    print(f"\n{'='*60}")
    print(f"  LIVE SCANNER  [{today}]  ({args.interval}s interval)")
    print(f"{'='*60}")

    # Load morning candidates
    candidates = load_candidates(override_symbols=args.symbols)
    if not candidates:
        return

    print(f"\n  Watching {len(candidates)} candidates: {[r['symbol'] for r in candidates]}")

    # Load news once (doesn't change much intraday)
    print(f"  Loading news...")
    try:
        from news import get_news_for_symbols
        news_map = get_news_for_symbols(
            [r["symbol"] for r in candidates], hours=24, include_rss=False
        )
    except Exception:
        news_map = {}

    # Wait for market open if needed
    mto = minutes_to_open()
    if mto and mto > 0:
        print(f"\n  Market opens in {mto:.0f} min — waiting...")
        time.sleep(min(mto * 60, 300))

    cycle = 0
    last_dash = None

    while True:
        now = datetime.now(ET)

        if not market_is_open():
            if now.hour >= 16:
                print(f"\n  Market closed. Final dashboard saved.")
                break
            print(f"  [{now.strftime('%H:%M:%S')}] Market not open yet — waiting 60s...")
            time.sleep(60)
            continue

        cycle += 1
        ts = now.strftime("%H:%M:%S")
        print(f"\n  [{ts}] Cycle {cycle} — refreshing {len(candidates)} symbols...", end=" ", flush=True)

        t0 = time.time()
        candidates = refresh_all(candidates)
        elapsed = time.time() - t0
        print(f"done in {elapsed:.1f}s")

        # Print mini summary
        for r in candidates[:5]:
            sig_str = ""
            if r.get("intraday_direction"):
                sig_str = f"  [{r['intraday_direction'][:3].upper()} {r.get('intraday_signal',0):+d}]"
            c = r.get("contract") or {}
            mid_str = f"  mid ${c.get('mid',0):.2f}" if c.get("mid") else ""
            delta_str = f"  δ{c.get('delta',0):.3f}" if c.get("delta") else ""
            print(f"    {r['direction']:4} {r['symbol']:<6}  ${r['spot']:.2f}"
                  f"{sig_str}{mid_str}{delta_str}")

        # Generate & push dashboard
        next_refresh = args.interval - int(elapsed)
        dash = push_dashboard(candidates, today, news_map, cycle, not args.no_push)
        inject_live_bar(dash, cycle, max(next_refresh, 10),
                        sum(1 for r in candidates if r["direction"]=="CALL"),
                        sum(1 for r in candidates if r["direction"]=="PUT"))

        # Open browser on first cycle
        if cycle == 1 and (args.open_browser or last_dash is None):
            webbrowser.open(f"file:///{dash.replace(os.sep, '/')}")
        last_dash = dash

        # Sleep until next interval
        sleep_secs = max(args.interval - int(time.time() - t0), 5)
        print(f"  Next refresh in {sleep_secs}s  |  "
              f"https://ablourchian.github.io/options-bot/")
        time.sleep(sleep_secs)


if __name__ == "__main__":
    main()
