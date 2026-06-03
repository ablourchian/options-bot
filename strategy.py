"""
Strategy engine — runs the scanner and applies rule-based filters to surface
high-conviction trade setups.

Strategies implemented:
  sell_put       — sell cash-secured puts when IV rank is high, delta < threshold
  sell_call      — sell covered calls when IV rank is high
  buy_call       — buy calls when IV rank is low (cheap premium)
  buy_put        — buy puts when IV rank is low
  iron_condor    — pair a short put + short call (both OTM) when IV rank is high

Usage:
    python strategy.py                          # all strategies, default symbols
    python strategy.py --strategy sell_put SPY QQQ
    python strategy.py --strategy iron_condor --dte-min 21 --dte-max 45
"""
import os
import argparse
from datetime import date, timedelta
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient

from historical_vol import historical_volatility, iv_rank
from scanner import get_spot, scan_symbol

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
stock_data = StockHistoricalDataClient(API_KEY, SECRET_KEY)
option_data = OptionHistoricalDataClient(API_KEY, SECRET_KEY)

# ── Strategy filter thresholds ──────────────────────────────────────────────

STRATEGY_PARAMS = {
    "sell_put": {
        "contract_type": "put",
        "iv_rank_min": 40,       # only sell when IV is elevated
        "iv_hv_ratio_min": 1.1,  # IV must be > HV (premium is rich)
        "delta_min": -0.40,      # not too deep ITM
        "delta_max": -0.15,      # not too far OTM
        "dte_min": 21,
        "dte_max": 45,
        "label": "Sell Put (CSP)",
        "rationale": "Elevated IV rank — rich premium, limited downside risk",
    },
    "sell_call": {
        "contract_type": "call",
        "iv_rank_min": 40,
        "iv_hv_ratio_min": 1.1,
        "delta_min": 0.15,
        "delta_max": 0.40,
        "dte_min": 21,
        "dte_max": 45,
        "label": "Sell Call (CC)",
        "rationale": "Elevated IV rank — collect premium on covered call",
    },
    "buy_call": {
        "contract_type": "call",
        "iv_rank_max": 30,       # buy when IV is cheap
        "iv_hv_ratio_max": 0.95,
        "delta_min": 0.30,
        "delta_max": 0.60,
        "dte_min": 30,
        "dte_max": 60,
        "label": "Buy Call",
        "rationale": "Low IV rank — cheap premium relative to historical vol",
    },
    "buy_put": {
        "contract_type": "put",
        "iv_rank_max": 30,
        "iv_hv_ratio_max": 0.95,
        "delta_min": -0.60,
        "delta_max": -0.30,
        "dte_min": 30,
        "dte_max": 60,
        "label": "Buy Put",
        "rationale": "Low IV rank — cheap downside protection",
    },
}


def apply_filters(rows, params):
    results = []
    for r in rows:
        if r["iv_rank"] is None or r["iv_hv_ratio"] is None:
            continue

        if "iv_rank_min" in params and r["iv_rank"] < params["iv_rank_min"]:
            continue
        if "iv_rank_max" in params and r["iv_rank"] > params["iv_rank_max"]:
            continue
        if "iv_hv_ratio_min" in params and r["iv_hv_ratio"] < params["iv_hv_ratio_min"]:
            continue
        if "iv_hv_ratio_max" in params and r["iv_hv_ratio"] > params["iv_hv_ratio_max"]:
            continue
        if r["delta"] is None:
            continue
        if "delta_min" in params and r["delta"] < params["delta_min"]:
            continue
        if "delta_max" in params and r["delta"] > params["delta_max"]:
            continue

        results.append(r)

    return results


def find_iron_condors(all_rows, iv_rank_min=40):
    """
    Pair short OTM put + short OTM call on the same underlying and expiration.
    Both legs must pass the IV rank threshold.
    """
    from itertools import product

    puts = [r for r in all_rows
            if r["type"] == "put"
            and r["iv_rank"] is not None and r["iv_rank"] >= iv_rank_min
            and r["delta"] is not None and -0.30 <= r["delta"] <= -0.10]

    calls = [r for r in all_rows
             if r["type"] == "call"
             and r["iv_rank"] is not None and r["iv_rank"] >= iv_rank_min
             and r["delta"] is not None and 0.10 <= r["delta"] <= 0.30]

    condors = []
    seen = set()
    for p, c in product(puts, calls):
        if p["underlying"] != c["underlying"]:
            continue
        if p["dte"] != c["dte"]:
            continue
        key = (p["symbol"], c["symbol"])
        if key in seen:
            continue
        seen.add(key)
        net_credit = round(p["mid"] + c["mid"], 2)
        condors.append({
            "underlying": p["underlying"],
            "dte": p["dte"],
            "put_symbol": p["symbol"],
            "put_strike": p["strike"],
            "put_delta": p["delta"],
            "put_mid": p["mid"],
            "call_symbol": c["symbol"],
            "call_strike": c["strike"],
            "call_delta": c["delta"],
            "call_mid": c["mid"],
            "net_credit": net_credit,
            "iv_rank": round((p["iv_rank"] + c["iv_rank"]) / 2, 1),
            "iv_pctile": round(((p["iv_pctile"] or 0) + (c["iv_pctile"] or 0)) / 2, 1),
        })

    return sorted(condors, key=lambda x: x["net_credit"], reverse=True)


def print_single_leg(rows, strategy_name, params, top_n=10):
    if not rows:
        print(f"  No setups found matching {strategy_name} criteria.\n")
        return

    rows = sorted(rows, key=lambda r: r["iv_rank"], reverse=True)[:top_n]
    print(f"\n  {'Symbol':<24} {'Strike':>7} {'DTE':>4} {'Mid':>6} "
          f"{'IV%':>5} {'IVRnk':>6} {'IV/HV':>6} {'Delta':>7} {'Theta':>7}")
    print(f"  {'-'*80}")
    for r in rows:
        print(
            f"  {r['symbol']:<24} {r['strike']:>7.2f} {r['dte']:>4} {r['mid']:>6.2f} "
            f"{r['iv']:>4.1f}% {r['iv_rank']:>5.1f}% {r['iv_hv_ratio']:>6.2f} "
            f"{r['delta']:>7.4f} {r['theta']:>7.4f}"
        )
    print(f"  {len(rows)} setups | Rationale: {params['rationale']}\n")


def print_condors(condors, top_n=10):
    if not condors:
        print("  No iron condor setups found.\n")
        return

    condors = condors[:top_n]
    print(f"\n  {'Underlying':<10} {'DTE':>4} {'Put Strike':>11} {'Call Strike':>12} "
          f"{'Net Credit':>11} {'Avg IVRnk':>10}")
    print(f"  {'-'*64}")
    for c in condors:
        print(
            f"  {c['underlying']:<10} {c['dte']:>4} "
            f"{c['put_strike']:>11.2f} ({c['put_delta']:+.2f}δ) "
            f"{c['call_strike']:>11.2f} ({c['call_delta']:+.2f}δ) "
            f"  ${c['net_credit']:>7.2f}   {c['iv_rank']:>7.1f}%"
        )
    print(f"  {len(condors)} setups | Rationale: High IV rank — sell both wings, collect credit\n")


def main():
    parser = argparse.ArgumentParser(description="Options strategy engine")
    parser.add_argument("symbols", nargs="*", default=["SPY", "QQQ", "AAPL", "TSLA"])
    parser.add_argument("--strategy", choices=list(STRATEGY_PARAMS.keys()) + ["iron_condor", "all"],
                        default="all")
    parser.add_argument("--dte-min", type=int, default=None,
                        help="Override DTE min from strategy defaults")
    parser.add_argument("--dte-max", type=int, default=None,
                        help="Override DTE max from strategy defaults")
    parser.add_argument("--strike-range", type=float, default=0.08)
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    symbols = [s.upper() for s in args.symbols]
    strategies = list(STRATEGY_PARAMS.keys()) + ["iron_condor"] \
        if args.strategy == "all" else [args.strategy]

    # Determine DTE range needed across all requested strategies
    dte_mins = []
    dte_maxs = []
    for s in strategies:
        if s == "iron_condor":
            dte_mins.append(21); dte_maxs.append(45)
        else:
            p = STRATEGY_PARAMS[s]
            dte_mins.append(args.dte_min or p["dte_min"])
            dte_maxs.append(args.dte_max or p["dte_max"])
    global_dte_min = min(dte_mins)
    global_dte_max = max(dte_maxs)

    print(f"\nStrategy engine | Symbols: {symbols} | Strategies: {strategies}")
    print(f"DTE range: {global_dte_min}–{global_dte_max} | Strike ±{int(args.strike_range*100)}%\n")

    all_rows = []
    for sym in symbols:
        print(f"  [{sym}] Loading data...")
        hv_data = historical_volatility(sym, stock_data)
        spot = get_spot(sym)
        if spot is None:
            continue

        hv_str = f"HV {hv_data['hv_current']}% (rank {hv_data['hv_rank']}%)" if hv_data else "HV N/A"
        print(f"    Spot ${spot:.2f} | {hv_str}")

        rows = scan_symbol(sym, spot, hv_data, global_dte_min, global_dte_max,
                           "both", args.strike_range)
        print(f"    {len(rows)} contracts scanned")
        all_rows.extend(rows)

    if not all_rows:
        print("\nNo data. Markets may be closed or no contracts found.")
        return

    for strat in strategies:
        if strat == "iron_condor":
            print(f"\n{'='*60}")
            print(f"  IRON CONDOR")
            print(f"{'='*60}")
            condors = find_iron_condors(all_rows, iv_rank_min=40)
            print_condors(condors, top_n=args.top)
        else:
            params = STRATEGY_PARAMS[strat].copy()
            if args.dte_min:
                params["dte_min"] = args.dte_min
            if args.dte_max:
                params["dte_max"] = args.dte_max

            ct = params["contract_type"]
            filtered = [r for r in all_rows
                        if r["type"] == ct
                        and params["dte_min"] <= r["dte"] <= params["dte_max"]]
            matches = apply_filters(filtered, params)

            print(f"\n{'='*60}")
            print(f"  {params['label'].upper()}")
            print(f"{'='*60}")
            print_single_leg(matches, strat, params, top_n=args.top)


if __name__ == "__main__":
    main()
