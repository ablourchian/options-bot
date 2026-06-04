from swing_screener import get_daily_bars, get_premarket_quote, find_best_contract, calc_atr
from intraday import get_signal
from historical_vol import historical_volatility
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.trading.enums import AssetStatus, ContractType
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionLatestQuoteRequest
from implied_vol import implied_volatility
from greeks import greeks
from datetime import date, timedelta
import os
from dotenv import load_dotenv
load_dotenv()

API_KEY    = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
stock_client  = StockHistoricalDataClient(API_KEY, SECRET_KEY)
trade_client  = TradingClient(API_KEY, SECRET_KEY, paper=True)
option_client = OptionHistoricalDataClient(API_KEY, SECRET_KEY)

SYM = "AZO"
spot = get_premarket_quote(SYM)
bars = get_daily_bars(SYM, n=30)
atr  = calc_atr(bars)
hv   = historical_volatility(SYM, stock_client)
sig  = get_signal(SYM, timeframe="5Min", n_bars=78)

print(f"\n{'='*55}")
print(f"  AUTOZONE (AZO) — CALL ANALYSIS")
print(f"{'='*55}")
print(f"  Spot:    ${spot:.2f}")
print(f"  ATR:     {atr:.1f}% / day  (${spot*atr/100:.2f} avg daily range)")
if hv:
    print(f"  HV 30d:  {hv['hv_current']:.1f}%  (rank {hv['hv_rank']:.0f}%,  range {hv['hv_min']:.1f}–{hv['hv_max']:.1f}%)")

# Intraday signal
print(f"\n  {'─'*50}")
print(f"  INTRADAY SIGNAL")
print(f"  {'─'*50}")
if sig:
    ind = sig["indicators"]
    icon = "[++]" if sig["direction"]=="oversold" else "[--]" if sig["direction"]=="overbought" else "[ ]"
    print(f"  Signal:   {icon} {sig['direction'].upper()}  ({sig['signal']:+d}/5)  {sig['confidence']}")
    print(f"  RSI:      {ind.get('rsi',0):.1f}   {'OVERSOLD <30' if ind.get('rsi',50)<30 else 'OVERBOUGHT >70' if ind.get('rsi',50)>70 else 'neutral'}")
    print(f"  Stoch K:  {ind.get('stoch_k',0):.1f}")
    print(f"  VWAP:     {ind.get('pct_from_vwap',0):+.2f}%")
    print(f"  MACD:     {ind.get('macd_hist',0):+.5f}")
    print()
    for r in sig.get("reasons", []):
        print(f"    · {r}")

# Your open position
print(f"\n  {'─'*50}")
print(f"  YOUR OPEN POSITION")
print(f"  {'─'*50}")
open_strike = 3100.0
cost_per = 13250.0
pct_otm = (open_strike - spot) / spot * 100
print(f"  2x AZO Jul 17 2026 CALL ${open_strike:.0f}")
print(f"  Paid:         ${cost_per:.0f} per contract  (${cost_per*2:.0f} total)")
print(f"  Spot now:     ${spot:.2f}")
print(f"  Distance:     {pct_otm:.1f}% OTM  (stock needs +{pct_otm:.1f}% to be ATM)")
print(f"  Days left:    {(date(2026,7,17)-date.today()).days}d")

# Get live quote on YOUR contract
print(f"\n  Fetching live quote on your contracts...")
try:
    your_sym = "AZO260717C03100000"
    q = option_client.get_option_latest_quote(
        OptionLatestQuoteRequest(symbol_or_symbols=[your_sym])
    ).get(your_sym)
    if q and q.bid_price:
        mid = (q.bid_price + q.ask_price) / 2
        pnl_per = (mid * 100) - cost_per
        pnl_tot = pnl_per * 2
        T = (date(2026,7,17) - date.today()).days / 365.0
        iv = implied_volatility(mid, spot, open_strike, T, 0.045, "call")
        print(f"  Current bid:  ${q.bid_price:.2f}")
        print(f"  Current ask:  ${q.ask_price:.2f}")
        print(f"  Mid:          ${mid:.2f}")
        print(f"  Value:        ${mid*100:.0f} per contract  (${mid*200:.0f} total)")
        print(f"  P&L:          ${pnl_per:+.0f} per contract  (${pnl_tot:+.0f} total)")
        if iv:
            g = greeks(spot, open_strike, T, 0.045, iv, "call")
            print(f"  IV:           {iv*100:.1f}%")
            print(f"  Delta:        {g['delta']:+.4f}  (${g['delta']*spot:.2f} per $1 move)")
            print(f"  Theta:        {g['theta']:.4f}/day  (${g['theta']*100:.2f}/day per contract)")
            print(f"  Vega:         {g['vega']:.4f}")
    else:
        print(f"  No live quote for {your_sym} — may be illiquid or expired")
except Exception as e:
    print(f"  Quote error: {e}")

# Scan ALL AZO calls 30-90 DTE and show the best ones
print(f"\n  {'─'*50}")
print(f"  AVAILABLE CALL OPTIONS (30–90 DTE, near spot)")
print(f"  {'─'*50}")
today = date.today()
try:
    contracts = trade_client.get_option_contracts(GetOptionContractsRequest(
        underlying_symbols=["AZO"],
        status=AssetStatus.ACTIVE,
        expiration_date_gte=today + timedelta(days=14),
        expiration_date_lte=today + timedelta(days=90),
        type=ContractType.CALL,
        strike_price_gte=str(int(spot * 0.95)),
        strike_price_lte=str(int(spot * 1.10)),
        limit=30,
    )).option_contracts

    if contracts:
        syms = [c.symbol for c in contracts]
        quotes = option_client.get_option_latest_quote(
            OptionLatestQuoteRequest(symbol_or_symbols=syms)
        )
        print(f"\n  {'Symbol':<26} {'Strike':>8} {'DTE':>5} {'Bid':>7} {'Ask':>7} {'Mid':>7} {'Spread':>7} {'Delta':>7} {'Theta':>7} {'IV':>6}")
        print(f"  {'-'*95}")
        for c in sorted(contracts, key=lambda x: (x.expiration_date, float(x.strike_price))):
            q = quotes.get(c.symbol)
            if not q or not q.bid_price or q.bid_price == 0:
                continue
            mid = (q.bid_price + q.ask_price) / 2
            if mid < 0.10:
                continue
            sp_pct = (q.ask_price - q.bid_price) / mid * 100
            dte_c = (c.expiration_date - today).days
            T = dte_c / 365.0
            strike = float(c.strike_price)
            iv = implied_volatility(mid, spot, strike, T, 0.045, "call")
            delta_str = theta_str = iv_str = "—"
            if iv:
                g = greeks(spot, strike, T, 0.045, iv, "call")
                delta_str = f"{g['delta']:+.3f}"
                theta_str = f"{g['theta']:.3f}"
                iv_str    = f"{iv*100:.1f}%"
            flag = " <-- YOUR POSITION" if abs(strike - 3100) < 1 else ""
            print(f"  {c.symbol:<26} {strike:>8.0f} {dte_c:>5}d {q.bid_price:>7.2f} {q.ask_price:>7.2f} {mid:>7.2f} {sp_pct:>6.1f}% {delta_str:>7} {theta_str:>7} {iv_str:>6}{flag}")
    else:
        print("  No contracts found in range.")
except Exception as e:
    print(f"  Error: {e}")

print()
