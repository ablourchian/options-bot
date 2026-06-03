"""
Options scanner — fetches contracts for a list of symbols, computes IV, Greeks,
IV rank, IV percentile, and IV/HV ratio. Prints a ranked table sorted by IV rank.

Usage:
    python scanner.py                        # scans default symbols
    python scanner.py SPY QQQ AAPL          # scans specific symbols
    python scanner.py --dte-min 7 --dte-max 45 --type call SPY QQQ
"""
import os
import argparse
from datetime import date, timedelta
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.trading.enums import AssetStatus, ContractType
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, OptionLatestQuoteRequest

from implied_vol import implied_volatility
from greeks import greeks
from historical_vol import historical_volatility, iv_rank

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
stock_data = StockHistoricalDataClient(API_KEY, SECRET_KEY)
option_data = OptionHistoricalDataClient(API_KEY, SECRET_KEY)

RISK_FREE_RATE = 0.045
DEFAULT_SYMBOLS = ["SPY", "QQQ", "AAPL", "TSLA"]


def get_spot(symbol):
    try:
        q = stock_data.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=symbol)
        )[symbol]
        return (q.bid_price + q.ask_price) / 2
    except Exception as e:
        print(f"  [!] Could not fetch spot for {symbol}: {e}")
        return None


def scan_symbol(symbol, spot, hv_data, dte_min, dte_max, contract_type, strike_pct_range):
    today = date.today()
    exp_start = today + timedelta(days=dte_min)
    exp_end = today + timedelta(days=dte_max)

    low_strike = spot * (1 - strike_pct_range)
    high_strike = spot * (1 + strike_pct_range)

    if contract_type == "both":
        ctypes = [ContractType.CALL, ContractType.PUT]
    elif contract_type == "call":
        ctypes = [ContractType.CALL]
    else:
        ctypes = [ContractType.PUT]

    contracts = []
    for ct in ctypes:
        try:
            result = trading_client.get_option_contracts(GetOptionContractsRequest(
                underlying_symbols=[symbol],
                status=AssetStatus.ACTIVE,
                expiration_date_gte=exp_start,
                expiration_date_lte=exp_end,
                type=ct,
                strike_price_gte=str(int(low_strike)),
                strike_price_lte=str(int(high_strike) + 1),
                limit=50,
            )).option_contracts
            contracts.extend(result)
        except Exception as e:
            print(f"  [!] Contract fetch failed for {symbol} {ct}: {e}")

    if not contracts:
        return []

    symbols = [c.symbol for c in contracts]
    try:
        quotes = option_data.get_option_latest_quote(
            OptionLatestQuoteRequest(symbol_or_symbols=symbols)
        )
    except Exception as e:
        print(f"  [!] Quote fetch failed for {symbol}: {e}")
        return []

    rows = []
    for c in contracts:
        q = quotes.get(c.symbol)
        if not q or q.bid_price == 0 or q.ask_price == 0:
            continue

        mid = (q.bid_price + q.ask_price) / 2
        strike = float(c.strike_price)
        T_days = (c.expiration_date - today).days
        T = T_days / 365.0
        otype = "call" if c.type == ContractType.CALL else "put"

        iv = implied_volatility(
            market_price=mid,
            S=spot, K=strike, T=T, r=RISK_FREE_RATE,
            option_type=otype,
        )
        if iv is None:
            continue

        g = greeks(S=spot, K=strike, T=T, r=RISK_FREE_RATE, sigma=iv, option_type=otype)

        ivr = {"iv_rank": None, "iv_pctile": None, "iv_hv_ratio": None}
        if hv_data:
            ivr = iv_rank(iv * 100, hv_data)

        rows.append({
            "symbol": c.symbol,
            "underlying": symbol,
            "type": otype,
            "strike": strike,
            "dte": T_days,
            "spot": round(spot, 2),
            "bid": q.bid_price,
            "ask": q.ask_price,
            "mid": round(mid, 2),
            "iv": round(iv * 100, 1),
            "iv_rank": ivr["iv_rank"],
            "iv_pctile": ivr["iv_pctile"],
            "iv_hv_ratio": ivr["iv_hv_ratio"],
            "delta": g["delta"],
            "theta": g["theta"],
            "vega": g["vega"],
            "hv": hv_data["hv_current"] if hv_data else None,
        })

    return rows


def print_table(rows, sort_by, top_n):
    valid = [r for r in rows if r.get(sort_by) is not None]
    invalid = [r for r in rows if r.get(sort_by) is None]
    sorted_rows = sorted(valid, key=lambda r: r[sort_by], reverse=True)[:top_n]

    header = (
        f"{'Symbol':<24} {'T':<4} {'Strike':>7} {'DTE':>4} {'Spot':>7} "
        f"{'Mid':>6} {'IV%':>5} {'IVRnk':>6} {'IVPct':>6} {'IV/HV':>6} "
        f"{'Delta':>7} {'Theta':>7}"
    )
    divider = "-" * len(header)
    print(f"\n{header}")
    print(divider)

    for r in sorted_rows:
        ivr_str = f"{r['iv_rank']:>5.1f}%" if r["iv_rank"] is not None else "   N/A"
        ivp_str = f"{r['iv_pctile']:>5.1f}%" if r["iv_pctile"] is not None else "   N/A"
        ivhv_str = f"{r['iv_hv_ratio']:>6.2f}" if r["iv_hv_ratio"] is not None else "   N/A"
        print(
            f"{r['symbol']:<24} {r['type'][0].upper():<4} {r['strike']:>7.2f} {r['dte']:>4} "
            f"{r['spot']:>7.2f} {r['mid']:>6.2f} {r['iv']:>4.1f}% "
            f"{ivr_str} {ivp_str} {ivhv_str} "
            f"{r['delta']:>7.4f} {r['theta']:>7.4f}"
        )

    print(divider)
    print(f"  {len(sorted_rows)} contracts shown | sorted by {sort_by} desc")
    if invalid:
        print(f"  ({len(invalid)} contracts had no HV data and were excluded from sort)")
    print()


def main():
    parser = argparse.ArgumentParser(description="Options IV scanner with IV rank")
    parser.add_argument("symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--dte-min", type=int, default=7)
    parser.add_argument("--dte-max", type=int, default=45)
    parser.add_argument("--type", dest="contract_type",
                        choices=["call", "put", "both"], default="both")
    parser.add_argument("--strike-range", type=float, default=0.05,
                        help="Strike range as fraction of spot (default 0.05 = ±5%%)")
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--sort", choices=["iv_rank", "iv_pctile", "iv", "iv_hv_ratio"],
                        default="iv_rank", help="Column to sort by (default: iv_rank)")
    parser.add_argument("--hv-window", type=int, default=30,
                        help="Rolling HV window in trading days (default: 30)")
    parser.add_argument("--hv-lookback", type=int, default=365,
                        help="Lookback period for HV min/max in days (default: 365)")
    args = parser.parse_args()

    symbols = [s.upper() for s in args.symbols]
    print(f"\nScanning {symbols}")
    print(f"DTE {args.dte_min}–{args.dte_max} | Type: {args.contract_type} | "
          f"Strike ±{int(args.strike_range*100)}% | Sort: {args.sort}")

    all_rows = []
    for sym in symbols:
        print(f"\n  [{sym}] Fetching HV ({args.hv_window}d window, {args.hv_lookback}d lookback)...")
        hv_data = historical_volatility(sym, stock_data,
                                        window=args.hv_window,
                                        lookback_days=args.hv_lookback)
        if hv_data:
            print(f"    HV: {hv_data['hv_current']}%  "
                  f"(range {hv_data['hv_min']}%–{hv_data['hv_max']}%  "
                  f"HV rank: {hv_data['hv_rank']}%)")
        else:
            print(f"    [!] HV data unavailable — IV rank will be N/A")

        print(f"  [{sym}] Fetching spot...")
        spot = get_spot(sym)
        if spot is None:
            continue
        print(f"    Spot: ${spot:.2f}")

        print(f"  [{sym}] Scanning contracts...")
        rows = scan_symbol(sym, spot, hv_data, args.dte_min, args.dte_max,
                           args.contract_type, args.strike_range)
        print(f"    {len(rows)} contracts with valid IV")
        all_rows.extend(rows)

    if not all_rows:
        print("\nNo results. Markets may be closed or no contracts found in range.")
        return

    print_table(all_rows, sort_by=args.sort, top_n=args.top)


if __name__ == "__main__":
    main()
