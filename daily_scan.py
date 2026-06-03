"""
Daily scanner — finds the most volatile stocks across S&P 500 / Dow / Nasdaq 100,
scans their options, scores every contract, and saves a ranked CSV.

Results are saved to: ./results/YYYY-MM-DD.csv
A rolling tracker (all-time best) is maintained at: ./results/tracker.csv

Usage:
    python daily_scan.py                          # full scan, top 30 volatile stocks
    python daily_scan.py --top-stocks 20          # scan top 20 by HV
    python daily_scan.py --mode buy               # score for buying (low IV)
    python daily_scan.py --index sp500            # only S&P 500
    python daily_scan.py --index nasdaq           # only Nasdaq 100
    python daily_scan.py --index dow              # only Dow 30
    python daily_scan.py --workers 5              # parallel HV fetching (default 5)
    python daily_scan.py --dry-run                # print top stocks only, no options scan
"""
import os
import csv
import argparse
import time
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient

from universe import get_universe, get_sp500, get_nasdaq100, get_dow30
from historical_vol import historical_volatility
from scanner import get_spot, scan_symbol
from ranker import rank_rows

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
stock_data = StockHistoricalDataClient(API_KEY, SECRET_KEY)
option_data = OptionHistoricalDataClient(API_KEY, SECRET_KEY)

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
RISK_FREE_RATE = 0.045

CSV_FIELDS = [
    "date", "rank", "grade", "score",
    "symbol", "underlying", "type", "strike", "dte", "spot",
    "bid", "ask", "mid", "iv", "iv_rank", "iv_pctile", "iv_hv_ratio",
    "hv", "delta", "gamma", "theta", "vega",
    "_s_iv_rank", "_s_iv_hv", "_s_spread", "_s_theta", "_s_delta",
]


# ── Volatility screener ───────────────────────────────────────────────────────

def fetch_hv_worker(sym):
    """Returns (symbol, hv_data | None)."""
    try:
        hv = historical_volatility(sym, stock_data, window=30, lookback_days=365)
        return sym, hv
    except Exception:
        return sym, None


def get_most_volatile(symbols: list[str], top_n: int, workers: int) -> list[tuple]:
    """
    Returns list of (symbol, hv_data) sorted by current HV descending, top_n entries.
    """
    print(f"\n  Fetching 30d HV for {len(symbols)} symbols using {workers} workers...")
    results = []
    done = 0
    total = len(symbols)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_hv_worker, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            sym, hv = fut.result()
            done += 1
            if hv:
                results.append((sym, hv))
            if done % 50 == 0 or done == total:
                print(f"    {done}/{total} done...", flush=True)

    results.sort(key=lambda x: x[1]["hv_current"], reverse=True)
    return results[:top_n]


# ── Results I/O ───────────────────────────────────────────────────────────────

def ensure_results_dir():
    os.makedirs(RESULTS_DIR, exist_ok=True)


def save_daily(rows: list[dict], scan_date: str):
    path = os.path.join(RESULTS_DIR, f"{scan_date}.csv")
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n  Saved {len(rows)} rows → {path}")
    return path


def update_tracker(rows: list[dict], scan_date: str):
    """Append today's top-50 rows to the rolling tracker CSV."""
    tracker_path = os.path.join(RESULTS_DIR, "tracker.csv")
    write_header = not os.path.exists(tracker_path)
    with open(tracker_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(rows[:50])
    print(f"  Tracker updated → {tracker_path} (+{min(50, len(rows))} rows)")


# ── Display ───────────────────────────────────────────────────────────────────

def print_volatile_stocks(stocks: list[tuple]):
    print(f"\n  {'#':<4} {'Symbol':<8} {'HV%':>6} {'HV Rank%':>9} {'HV Min%':>8} {'HV Max%':>8}")
    print(f"  {'-'*50}")
    for i, (sym, hv) in enumerate(stocks, 1):
        print(f"  {i:<4} {sym:<8} {hv['hv_current']:>6.1f}% {hv['hv_rank']:>8.1f}% "
              f"{hv['hv_min']:>7.1f}% {hv['hv_max']:>7.1f}%")


def print_ranked_trades(rows: list[dict], top_n: int = 50):
    rows = rows[:top_n]
    print(f"\n{'='*110}")
    print(f"  RANKED TRADES  (top {len(rows)})")
    print(f"{'='*110}")
    header = (
        f"  {'#':<4} {'Grd':<4} {'Score':>5} "
        f"{'Symbol':<24} {'T':<4} {'Strike':>7} {'DTE':>4} {'Spot':>7} "
        f"{'Mid':>6} {'IV%':>5} {'IVRnk':>6} {'IV/HV':>6} "
        f"{'Delta':>7} {'Theta':>7} {'Spread%':>8}"
    )
    print(header)
    print(f"  {'-'*106}")
    for i, r in enumerate(rows, 1):
        spread_pct = ((r["ask"] - r["bid"]) / r["mid"] * 100) if r["mid"] > 0 else 0
        ivr = f"{r['iv_rank']:.1f}%" if r["iv_rank"] is not None else "  N/A"
        ivhv = f"{r['iv_hv_ratio']:.2f}" if r["iv_hv_ratio"] is not None else "  N/A"
        print(
            f"  {i:<4} {r['grade']:<4} {r['score']:>5.1f} "
            f"{r['symbol']:<24} {r['type'][0].upper():<4} {r['strike']:>7.2f} "
            f"{r['dte']:>4} {r['spot']:>7.2f} {r['mid']:>6.2f} "
            f"{r['iv']:>4.1f}% {ivr:>6} {ivhv:>6} "
            f"{r['delta']:>7.4f} {r['theta']:>7.4f} {spread_pct:>7.1f}%"
        )
    print(f"  {'-'*106}")


def print_score_breakdown(rows: list[dict], top_n: int = 10):
    print(f"\n  Score breakdown (top {top_n}):")
    print(f"  {'Symbol':<24} {'Score':>5} {'IVRnk':>6} {'IV/HV':>6} {'Spread':>7} {'Theta':>7} {'Delta':>6}")
    print(f"  {'-'*70}")
    for r in rows[:top_n]:
        print(
            f"  {r['symbol']:<24} {r['score']:>5.1f} "
            f"{r['_s_iv_rank']:>6.1f} {r['_s_iv_hv']:>6.1f} "
            f"{r['_s_spread']:>7.1f} {r['_s_theta']:>7.1f} {r['_s_delta']:>6.1f}"
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Daily options scanner across index constituents")
    parser.add_argument("--index", choices=["sp500", "nasdaq", "dow", "all"], default="all")
    parser.add_argument("--top-stocks", type=int, default=30,
                        help="Number of most volatile stocks to scan (default 30)")
    parser.add_argument("--dte-min", type=int, default=14)
    parser.add_argument("--dte-max", type=int, default=45)
    parser.add_argument("--strike-range", type=float, default=0.07,
                        help="Strike range as fraction of spot (default 0.07 = ±7%%)")
    parser.add_argument("--mode", choices=["sell", "buy"], default="sell",
                        help="Scoring mode: sell = high IV, buy = low IV (default: sell)")
    parser.add_argument("--type", dest="contract_type",
                        choices=["call", "put", "both"], default="both")
    parser.add_argument("--top-trades", type=int, default=50,
                        help="Trades to display and save (default 50)")
    parser.add_argument("--workers", type=int, default=5,
                        help="Parallel workers for HV fetching (default 5)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only show volatile stocks, skip options scan")
    args = parser.parse_args()

    today = str(date.today())
    ensure_results_dir()

    # 1. Build universe
    print(f"\n{'='*60}")
    print(f"  OPTIONS BOT — Daily Scan  [{today}]")
    print(f"{'='*60}")

    print(f"\n  Building universe (index={args.index})...")
    if args.index == "sp500":
        symbols = get_sp500()
    elif args.index == "nasdaq":
        symbols = get_nasdaq100()
    elif args.index == "dow":
        symbols = get_dow30()
    else:
        symbols = get_universe()
    print(f"  Universe: {len(symbols)} symbols")

    # 2. Find most volatile
    volatile_stocks = get_most_volatile(symbols, top_n=args.top_stocks, workers=args.workers)
    print(f"\n  Top {len(volatile_stocks)} most volatile stocks:")
    print_volatile_stocks(volatile_stocks)

    if args.dry_run:
        print("\n  [dry-run] Skipping options scan.")
        return

    # 3. Scan options for each volatile stock
    print(f"\n  Scanning options (DTE {args.dte_min}–{args.dte_max}, "
          f"±{int(args.strike_range*100)}% strikes, type={args.contract_type})...")

    all_rows = []
    for i, (sym, hv_data) in enumerate(volatile_stocks, 1):
        print(f"  [{i}/{len(volatile_stocks)}] {sym}  HV={hv_data['hv_current']:.1f}%", end="  ")
        spot = get_spot(sym)
        if spot is None:
            print("(no quote)")
            continue
        print(f"spot=${spot:.2f}", end="  ")
        rows = scan_symbol(sym, spot, hv_data, args.dte_min, args.dte_max,
                           args.contract_type, args.strike_range)
        print(f"{len(rows)} contracts")
        all_rows.extend(rows)
        time.sleep(0.1)  # gentle rate limiting

    if not all_rows:
        print("\n  No contracts found. Markets may be closed.")
        return

    # 4. Fetch news + intraday signals for all scanned underlyings
    underlyings = list(set(r["underlying"] for r in all_rows))

    print(f"\n  Fetching news & earnings for {len(underlyings)} tickers...")
    try:
        from news import get_news_for_symbols
        news_map = get_news_for_symbols(underlyings, hours=24, include_rss=False)
        print(f"    News fetched for {len(news_map)} tickers")
    except Exception as e:
        print(f"    [!] News fetch failed: {e}")
        news_map = {}

    print(f"  Fetching intraday signals...")
    try:
        from intraday import get_signals_bulk
        signal_map = get_signals_bulk(underlyings[:30], timeframe="5Min", n_bars=78)
        print(f"    Signals computed for {len(signal_map)} tickers")
    except Exception as e:
        print(f"    [!] Signal fetch failed: {e}")
        signal_map = {}

    # 5. Score and rank
    print(f"\n  Scoring {len(all_rows)} contracts (mode={args.mode})...")
    ranked = rank_rows(all_rows, mode=args.mode,
                       news_map=news_map, signal_map=signal_map)
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
        r["date"] = today
        # Attach catalyst + sentiment to row for dashboard
        nd = news_map.get(r.get("underlying"), {})
        r["catalyst"]        = nd.get("catalyst", "")
        r["news_sentiment"]  = nd.get("sentiment", {}).get("label", "")
        r["days_to_earnings"] = nd.get("earnings", {}).get("days_to_earnings")

    # 5. Display
    print_ranked_trades(ranked, top_n=args.top_trades)
    print_score_breakdown(ranked, top_n=10)

    # 6. Save
    save_daily(ranked, today)
    update_tracker(ranked, today)

    # 7. Generate and open dashboard
    dash_path = None
    try:
        from dashboard import load_tracker_history, generate_html
        import webbrowser
        history = load_tracker_history()
        html = generate_html(ranked, history, today)
        dash_path = os.path.join(RESULTS_DIR, f"dashboard_{today}.html")
        with open(dash_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  Dashboard saved → {dash_path}")
        webbrowser.open(f"file:///{dash_path.replace(os.sep, '/')}")
    except Exception as e:
        print(f"  [!] Dashboard generation failed: {e}")

    # 8. Send Discord notification
    try:
        from notifier import send_daily_report
        send_daily_report(ranked, today, dashboard_path=dash_path)
    except Exception as e:
        print(f"  [!] Notification failed: {e}")

    print(f"\n  Done. {len(ranked)} contracts ranked. Results in ./results/\n")


if __name__ == "__main__":
    main()
