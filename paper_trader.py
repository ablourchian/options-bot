"""
Contrarian Intraday Paper Trader

Strategy:
  1. Every morning, scan the market for the biggest daily movers (up AND down)
  2. Wait for intraday signals confirming exhaustion / reversal starting
     - Big winner up +4%+ with RSI overbought, VWAP stretched → buy PUTS
     - Big loser down -4%+ with RSI oversold, VWAP stretched → buy CALLS
  3. Place paper options orders on Alpaca with tight risk rules

Logic: stocks that make big intraday moves tend to snap back. You're fading
the extreme move once you see confirmation the momentum is stalling.

Usage:
    python paper_trader.py              # scan movers + dry run
    python paper_trader.py --execute    # place real paper orders
    python paper_trader.py --status     # show open positions + P&L
    python paper_trader.py --movers     # just show today's top movers
    python paper_trader.py --close-all  # close all open positions
"""
import os
import re
import json
import argparse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    LimitOrderRequest, GetOptionContractsRequest
)
from alpaca.trading.enums import OrderSide, OrderType, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import (
    StockSnapshotRequest, OptionLatestQuoteRequest
)

import webbrowser
from intraday import get_signal

load_dotenv()

ET = ZoneInfo("America/New_York")
API_KEY    = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
TRADE_LOG   = os.path.join(RESULTS_DIR, "paper_trades.json")
os.makedirs(RESULTS_DIR, exist_ok=True)

# ── Universe to scan for big movers ──────────────────────────────────────────
# Large-cap, liquid options — your proven hunting ground
UNIVERSE = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","AMD","COIN",
    "CRWD","AVGO","NFLX","PLTR","ARM","MRVL","ORCL","CRM","UBER",
    "SHOP","SQ","HOOD","INTC","QCOM","MU","PANW","SNOW","NET","ZM",
    "ROKU","LYFT","DASH","RBLX","DKNG","GME","AMC","SOFI","RIVN",
    "SPY","QQQ","IWM"
]

# ── Strategy parameters ───────────────────────────────────────────────────────
MOVER_THRESHOLD    = 3.0    # % daily move to qualify as a big mover
STRONG_MOVE        = 6.0    # % move considered a strong fade candidate
MAX_SPEND_PER_TRADE = 50000  # $50k per trade
MAX_OPEN_POSITIONS  = 5
PROFIT_TARGET_DOLLARS = 5000  # take profit at +$5k
STOP_LOSS_DOLLARS     = 2000  # cut loss at -$2k
TARGET_DTE_MIN      = 5
TARGET_DTE_MAX      = 14    # short-dated for intraday reversal plays

# Time-of-day rules: only look for reversals after the move has had time to develop
REVERSAL_WINDOW_START = 12   # noon ET — move must have held this long
REVERSAL_WINDOW_END   = 15   # 3pm ET — too late to enter intraday


def trading_client():
    return TradingClient(API_KEY, SECRET_KEY, paper=True)


def stock_client():
    return StockHistoricalDataClient(API_KEY, SECRET_KEY)


def opt_client():
    return OptionHistoricalDataClient(API_KEY, SECRET_KEY)


# ── Trade log ─────────────────────────────────────────────────────────────────

def load_log():
    if os.path.exists(TRADE_LOG):
        with open(TRADE_LOG) as f:
            return json.load(f)
    return []


def save_log(log):
    with open(TRADE_LOG, "w") as f:
        json.dump(log, f, indent=2, default=str)


# ── Step 1: Find today's biggest movers ──────────────────────────────────────

def get_movers(universe, threshold=MOVER_THRESHOLD):
    """
    Returns list of dicts sorted by abs(daily_chg_pct), biggest first.
    Each dict: {sym, prev_close, current, chg_pct, volume, direction}
    direction: 'up' or 'down'
    """
    sc = stock_client()
    snap = sc.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=universe))

    movers = []
    for sym, s in snap.items():
        if not s.daily_bar or not s.previous_daily_bar:
            continue
        prev  = s.previous_daily_bar.close
        curr  = s.daily_bar.close
        chg   = (curr - prev) / prev * 100
        if abs(chg) >= threshold:
            movers.append({
                "sym":        sym,
                "prev_close": prev,
                "current":    curr,
                "chg_pct":    chg,
                "volume":     s.daily_bar.volume,
                "direction":  "up" if chg > 0 else "down",
                "high":       s.daily_bar.high,
                "low":        s.daily_bar.low,
            })

    return sorted(movers, key=lambda x: abs(x["chg_pct"]), reverse=True)


# ── Step 2: Check intraday signal for reversal confirmation ───────────────────

def reversal_confirmed(mover, sig):
    """
    Returns (confirmed: bool, reason: str, trade_direction: str)

    For a stock UP big → we want to fade with PUTS.
      Confirmation: RSI overbought (>65), or VWAP stretched above (+1.5%+),
                    or signal direction is overbought.

    For a stock DOWN big → we want to fade with CALLS.
      Confirmation: RSI oversold (<35), or VWAP stretched below (-1.5%+),
                    or signal direction is oversold.
    """
    if not sig:
        return False, "no signal data", None

    ind        = sig.get("indicators", {})
    rsi        = ind.get("rsi")
    stoch_k    = ind.get("stoch_k")
    vwap_pct   = ind.get("pct_from_vwap")
    direction  = sig.get("direction")
    score      = sig.get("signal", 0)
    chg        = mover["chg_pct"]

    reasons = []
    score_reversal = 0

    if mover["direction"] == "up":
        # Stock is up big — look for exhaustion signals to buy PUTS
        if rsi is not None and rsi > 65:
            score_reversal += 2
            reasons.append(f"RSI {rsi:.0f} overbought")
        elif rsi is not None and rsi > 55:
            score_reversal += 1
            reasons.append(f"RSI {rsi:.0f} elevated")
        if vwap_pct is not None and vwap_pct > 1.5:
            score_reversal += 2
            reasons.append(f"VWAP +{vwap_pct:.1f}% stretched")
        elif vwap_pct is not None and vwap_pct > 0.5:
            score_reversal += 1
            reasons.append(f"VWAP +{vwap_pct:.1f}% above")
        if stoch_k is not None and stoch_k > 75:
            score_reversal += 1
            reasons.append(f"Stoch {stoch_k:.0f} overbought")
        if direction == "overbought":
            score_reversal += 2
            reasons.append(f"intraday signal overbought ({score:+d})")
        if abs(chg) >= STRONG_MOVE:
            score_reversal += 1
            reasons.append(f"extreme move +{chg:.1f}%")

        min_score = 3 if abs(chg) >= STRONG_MOVE else 4
        if score_reversal >= min_score:
            return True, " · ".join(reasons), "put"
        return False, f"reversal score {score_reversal}/5 (need {min_score}) — {' · '.join(reasons) or 'no signal'}", None

    else:
        # Stock is down big — look for bounce signals to buy CALLS
        if rsi is not None and rsi < 35:
            score_reversal += 2
            reasons.append(f"RSI {rsi:.0f} oversold")
        elif rsi is not None and rsi < 45:
            score_reversal += 1
            reasons.append(f"RSI {rsi:.0f} low")
        if vwap_pct is not None and vwap_pct < -1.5:
            score_reversal += 2
            reasons.append(f"VWAP {vwap_pct:.1f}% stretched")
        elif vwap_pct is not None and vwap_pct < -0.5:
            score_reversal += 1
            reasons.append(f"VWAP {vwap_pct:.1f}% below")
        if stoch_k is not None and stoch_k < 25:
            score_reversal += 1
            reasons.append(f"Stoch {stoch_k:.0f} oversold")
        if direction == "oversold":
            score_reversal += 2
            reasons.append(f"intraday signal oversold ({score:+d})")
        if abs(chg) >= STRONG_MOVE:
            score_reversal += 1
            reasons.append(f"extreme move {chg:.1f}%")

        min_score = 3 if abs(chg) >= STRONG_MOVE else 4
        if score_reversal >= min_score:
            return True, " · ".join(reasons), "call"
        return False, f"reversal score {score_reversal}/5 (need {min_score}) — {' · '.join(reasons) or 'no signal'}", None


# ── Step 3: Find the best options contract ────────────────────────────────────

def find_contract(sym, trade_dir, spot):
    try:
        tc = trading_client()
        exp_min = (date.today() + timedelta(days=TARGET_DTE_MIN)).isoformat()
        exp_max = (date.today() + timedelta(days=TARGET_DTE_MAX)).isoformat()

        req = GetOptionContractsRequest(
            underlying_symbols=[sym],
            expiration_date_gte=exp_min,
            expiration_date_lte=exp_max,
            type=trade_dir,
            limit=100,
        )
        contracts = tc.get_option_contracts(req).option_contracts
        if not contracts:
            return None

        # For reversals: slightly OTM is ideal (cheaper, bigger % gain on reversal)
        best = None
        best_score = 999
        for c in contracts:
            if not c.strike_price:
                continue
            strike = float(c.strike_price)
            if trade_dir == "call":
                otm = (strike - spot) / spot  # positive = OTM
                # Target: 1-4% OTM call for reversal play
                if 0.01 <= otm <= 0.05:
                    sc = abs(otm - 0.025)
                    if sc < best_score:
                        best_score = sc
                        best = c
            else:
                otm = (spot - strike) / spot
                if 0.01 <= otm <= 0.05:
                    sc = abs(otm - 0.025)
                    if sc < best_score:
                        best_score = sc
                        best = c

        if not best:
            # Fallback: closest to ATM
            best = min(contracts, key=lambda c: abs(float(c.strike_price or 0) - spot))

        # Get quote
        oc = opt_client()
        try:
            oq = oc.get_option_latest_quote(
                OptionLatestQuoteRequest(symbol_or_symbols=best.symbol)
            )
            qd = oq.get(best.symbol)
            bid = float(qd.bid_price or 0) if qd else 0
            ask = float(qd.ask_price or 0) if qd else 0
            mid = (bid + ask) / 2 if bid and ask else 0
        except Exception:
            bid = ask = mid = 0

        return {
            "symbol": best.symbol,
            "strike": float(best.strike_price),
            "expiry": str(best.expiration_date),
            "dte":    (best.expiration_date - date.today()).days,
            "bid":    bid,
            "ask":    ask,
            "mid":    mid,
        }
    except Exception as e:
        print(f"    [!] Contract lookup failed: {e}")
        return None


# ── Main run ──────────────────────────────────────────────────────────────────

def run(execute=False):
    now = datetime.now(ET)
    print(f"\n  Contrarian Reversal Paper Trader — {now.strftime('%Y-%m-%d %H:%M ET')}")
    print(f"  Mode: {'LIVE PAPER ORDERS' if execute else 'DRY RUN'}\n")

    tc = trading_client()
    acct = tc.get_account()
    buying_power = float(acct.buying_power)
    portfolio    = float(acct.portfolio_value)
    print(f"  Account: ${portfolio:,.2f} portfolio  ${buying_power:,.2f} buying power")

    positions  = tc.get_all_positions()
    open_syms  = set()
    for p in positions:
        m = re.match(r"([A-Z]+)\d", p.symbol)
        if m:
            open_syms.add(m.group(1))

    slots = MAX_OPEN_POSITIONS - len(positions)
    print(f"  Open positions: {len(positions)}/{MAX_OPEN_POSITIONS}  ({slots} slots available)\n")

    if slots <= 0:
        print("  No slots available. Run --status to review positions.")
        return []

    # Time-of-day gate: reversals only valid noon–3pm ET
    hour = now.hour
    minute = now.minute
    if hour < REVERSAL_WINDOW_START:
        mins_left = (REVERSAL_WINDOW_START - hour) * 60 - minute
        print(f"  ⏰ Too early — reversal window opens at noon ET ({mins_left} min away)")
        print(f"     Moves need time to develop before fading them.\n")
        print(f"     Re-run at 12:00pm ET. Showing movers now for reference:\n")
    elif hour >= REVERSAL_WINDOW_END:
        print(f"  ⏰ Too late — reversal window closed at 3pm ET. Come back tomorrow.\n")
        return []

    # Step 1: Find big movers
    print(f"  Scanning {len(UNIVERSE)} stocks for big movers (≥{MOVER_THRESHOLD}%)...\n")
    movers = get_movers(UNIVERSE)

    if not movers:
        print("  No big movers found today. Try again during market hours.")
        return []

    print(f"  {'Ticker':<8} {'Move':>7}  {'Prev':>8}  {'Now':>8}  {'Volume':>12}  Fade?")
    print(f"  {'-'*65}")
    for mv in movers[:20]:
        arrow = "▲" if mv["direction"] == "up" else "▼"
        fade  = "PUT" if mv["direction"] == "up" else "CALL"
        strong = " ◀ STRONG" if abs(mv["chg_pct"]) >= STRONG_MOVE else ""
        print(f"  {mv['sym']:<8} {arrow}{abs(mv['chg_pct']):>5.1f}%  "
              f"${mv['prev_close']:>7.2f}  ${mv['current']:>7.2f}  "
              f"{mv['volume']:>12,.0f}  → fade {fade}{strong}")
    print()

    # Step 2: Check intraday signals on each mover
    print("  Checking intraday reversal signals...\n")
    candidates = []

    for mv in movers:
        sym = mv["sym"]
        if sym in open_syms:
            continue

        sig = get_signal(sym, timeframe="5Min", n_bars=78)
        confirmed, reason, trade_dir = reversal_confirmed(mv, sig)

        chg_str = f"{mv['chg_pct']:+.1f}%"
        if confirmed:
            print(f"  ✓ {sym:<8} {chg_str:>7}  → {trade_dir.upper()} FADE  |  {reason}")
            candidates.append({
                "mover":      mv,
                "sig":        sig,
                "trade_dir":  trade_dir,
                "reason":     reason,
            })
        else:
            print(f"  ✗ {sym:<8} {chg_str:>7}  no confirmation  |  {reason}")

    print(f"\n  {len(candidates)} confirmed reversal setups\n")

    if not candidates:
        print("  No confirmed reversals yet. Move too early or signals not there.")
        return []

    # Sort: biggest move first (more extreme = better reversal candidate)
    candidates.sort(key=lambda x: abs(x["mover"]["chg_pct"]), reverse=True)

    # Step 3: Find contracts and place orders
    log = load_log()
    placed = []

    for c in candidates[:slots]:
        sym       = c["mover"]["sym"]
        trade_dir = c["trade_dir"]
        spot      = c["mover"]["current"]
        chg       = c["mover"]["chg_pct"]
        sig       = c["sig"] or {}
        ind       = sig.get("indicators", {})

        print(f"  ─────────────────────────────────────────────")
        print(f"  → {sym}  {chg:+.1f}% today  fade with {trade_dir.upper()}")
        print(f"    Signals : {c['reason']}")
        print(f"    RSI     : {ind.get('rsi', '—'):.0f}" if ind.get('rsi') else f"    RSI     : —")
        print(f"    VWAP    : {ind.get('pct_from_vwap', 0):+.2f}%" if ind.get('pct_from_vwap') is not None else f"    VWAP    : —")
        print(f"    Stoch   : {ind.get('stoch_k', '—'):.0f}" if ind.get('stoch_k') else f"    Stoch   : —")
        print(f"    Spot    : ${spot:.2f}  High: ${c['mover']['high']:.2f}  Low: ${c['mover']['low']:.2f}")
        print()

        # Open TradingView chart so you can eyeball it
        chart_url = f"https://www.tradingview.com/chart/?symbol={sym}&interval=5"
        print(f"    Opening chart → {chart_url}")
        webbrowser.open(chart_url)
        print()

        answer = input(f"    Does the chart show a reversal starting? (y/n/skip): ").strip().lower()
        if answer != "y":
            print(f"    Skipped — {sym}\n")
            continue

        print(f"    ✓ Approved — finding contract...")

        contract = find_contract(sym, trade_dir, spot)
        if not contract:
            print(f"    No contract found — skip\n")
            continue

        mid = contract["mid"]
        if mid <= 0:
            print(f"    No quote (mid=${mid}) — skip\n")
            continue

        max_spend = MAX_SPEND_PER_TRADE
        qty   = max(1, int(max_spend / (mid * 100)))
        cost  = qty * mid * 100
        limit = round(mid * 1.03, 2)  # 3% above mid for fills

        print(f"    Contract: {contract['symbol']}")
        print(f"    Strike: ${contract['strike']:.2f}  Expiry: {contract['expiry']}  DTE: {contract['dte']}")
        print(f"    Spot: ${spot:.2f}  Mid: ${mid:.2f}  Limit: ${limit:.2f}")
        print(f"    Qty: {qty}  Cost: ${cost:.0f}")
        print(f"    Target: +{PROFIT_TARGET_PCT}%  Stop: {STOP_LOSS_PCT}%")

        if execute:
            try:
                req = LimitOrderRequest(
                    symbol=contract["symbol"],
                    qty=qty,
                    side=OrderSide.BUY,
                    type=OrderType.LIMIT,
                    time_in_force=TimeInForce.DAY,
                    limit_price=limit,
                )
                order = tc.submit_order(req)
                order_id = str(order.id)
                status = "open"
                print(f"    ✓ Order placed: {order_id}")
            except Exception as e:
                print(f"    ✗ Order failed: {e}\n")
                continue
        else:
            order_id = "dry_run"
            status = "dry_run"
            print(f"    [DRY RUN] Would place order")

        entry = {
            "date":         str(date.today()),
            "timestamp":    str(datetime.now(ET)),
            "sym":          sym,
            "daily_chg":    round(chg, 2),
            "trade_dir":    trade_dir,
            "contract":     contract["symbol"],
            "strike":       contract["strike"],
            "expiry":       contract["expiry"],
            "dte":          contract["dte"],
            "spot":         spot,
            "entry_price":  limit,
            "qty":          qty,
            "cost":         round(cost, 2),
            "target_price": round(mid * (1 + PROFIT_TARGET_PCT / 100), 2),
            "stop_price":   round(mid * (1 + STOP_LOSS_PCT / 100), 2),
            "reason":       c["reason"],
            "order_id":     order_id,
            "status":       status,
        }
        log.append(entry)
        placed.append(entry)
        print()

    save_log(log)
    print(f"  {'Placed' if execute else '[DRY RUN]'} {len(placed)} trade(s).")
    return placed


# ── Status ────────────────────────────────────────────────────────────────────

def show_status():
    print(f"\n  Status — {date.today()}\n")
    tc = trading_client()
    acct = tc.get_account()
    print(f"  Portfolio: ${float(acct.portfolio_value):,.2f}  "
          f"Buying Power: ${float(acct.buying_power):,.2f}\n")

    positions = tc.get_all_positions()
    if not positions:
        print("  No open positions.\n")
    else:
        log = load_log()
        log_map = {e["contract"]: e for e in log}

        print(f"  {'Symbol':<28} {'Qty':>4} {'Entry':>8} {'Now':>8} "
              f"{'P&L $':>9} {'P&L %':>7}  Action")
        print(f"  {'-'*85}")
        for p in positions:
            entry   = float(p.avg_entry_price)
            current = float(p.current_price) if p.current_price else entry
            pnl     = float(p.unrealized_pl)
            pnl_pct = float(p.unrealized_plpc) * 100
            pnl_s   = f"+${pnl:,.0f}" if pnl >= 0 else f"-${abs(pnl):,.0f}"
            pct_s   = f"+{pnl_pct:.1f}%" if pnl_pct >= 0 else f"{pnl_pct:.1f}%"
            le      = log_map.get(p.symbol, {})
            reason  = le.get("reason", "")

            if pnl_pct >= PROFIT_TARGET_PCT:
                action = f"✓ TAKE PROFIT"
            elif pnl_pct <= STOP_LOSS_PCT:
                action = f"✗ CUT LOSS"
            else:
                action = "hold"

            print(f"  {p.symbol:<28} {float(p.qty):>4.0f} ${entry:>7.2f} "
                  f"${current:>7.2f} {pnl_s:>9} {pct_s:>7}  {action}")
            if reason:
                print(f"    Entry thesis: {reason}")
        print()

    log = load_log()
    closed = [t for t in log if t.get("status") == "closed"]
    if closed:
        total = sum(t.get("pnl", 0) for t in closed)
        wins  = sum(1 for t in closed if t.get("pnl", 0) > 0)
        print(f"  Closed: {len(closed)} trades  Wins: {wins}  "
              f"Total realized: ${total:+,.0f}\n")


def show_movers():
    print(f"\n  Top Movers — {datetime.now(ET).strftime('%H:%M ET')}\n")
    movers = get_movers(UNIVERSE, threshold=2.0)
    print(f"  {'Ticker':<8} {'Move':>7}  {'Prev':>8}  {'Now':>8}  {'Hi':>8}  {'Lo':>8}  {'Volume':>12}")
    print(f"  {'-'*70}")
    for mv in movers[:25]:
        arrow = "▲" if mv["direction"] == "up" else "▼"
        print(f"  {mv['sym']:<8} {arrow}{abs(mv['chg_pct']):>5.1f}%  "
              f"${mv['prev_close']:>7.2f}  ${mv['current']:>7.2f}  "
              f"${mv['high']:>7.2f}  ${mv['low']:>7.2f}  {mv['volume']:>12,.0f}")
    print()


def monitor_exits(execute=False):
    """
    Check all open positions — auto-exit at +7% gain or -15% stop.
    Run this every few minutes while in a trade.
    """
    tc = trading_client()
    positions = tc.get_all_positions()
    if not positions:
        print("  No open positions.")
        return

    print(f"\n  Monitoring {len(positions)} position(s) — {datetime.now(ET).strftime('%H:%M:%S ET')}\n")
    for p in positions:
        pnl_pct = float(p.unrealized_plpc) * 100
        pnl     = float(p.unrealized_pl)
        current = float(p.current_price) if p.current_price else 0
        entry   = float(p.avg_entry_price)
        pct_s   = f"+{pnl_pct:.1f}%" if pnl_pct >= 0 else f"{pnl_pct:.1f}%"

        if pnl >= PROFIT_TARGET_DOLLARS:
            action = f"✓ EXIT — $5k target hit (${pnl:+,.0f})"
            should_exit = True
        elif pnl <= -STOP_LOSS_DOLLARS:
            action = f"✗ EXIT — $2k stop hit (${pnl:,.0f})"
            should_exit = True
        else:
            action = f"hold (${pnl:+,.0f})"
            should_exit = False

        print(f"  {p.symbol:<28} entry=${entry:.2f}  now=${current:.2f}  "
              f"P&L=${pnl:+.0f}  {action}")

        if should_exit:
            if execute:
                try:
                    tc.close_position(p.symbol)
                    print(f"    → Closed {p.symbol}")
                    log = load_log()
                    for t in log:
                        if t.get("contract") == p.symbol and t.get("status") == "open":
                            t["status"] = "closed"
                            t["exit_price"] = current
                            t["pnl"] = round(pnl, 2)
                            t["pnl_pct"] = round(pnl_pct, 2)
                            t["exit_time"] = str(datetime.now(ET))
                    save_log(log)
                except Exception as e:
                    print(f"    → Failed to close: {e}")
            else:
                print(f"    → [DRY RUN] Would close {p.symbol}")
    print()


def close_all():
    tc = trading_client()
    positions = tc.get_all_positions()
    if not positions:
        print("  No open positions.")
        return
    confirm = input(f"  Close all {len(positions)} positions? Type YES: ")
    if confirm.strip() != "YES":
        print("  Cancelled.")
        return
    for p in positions:
        try:
            tc.close_position(p.symbol)
            print(f"  Closed {p.symbol}")
        except Exception as e:
            print(f"  Failed {p.symbol}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Contrarian intraday paper trader")
    parser.add_argument("--execute",   action="store_true", help="Place real paper orders")
    parser.add_argument("--status",    action="store_true", help="Show positions + P&L")
    parser.add_argument("--movers",    action="store_true", help="Show today's top movers")
    parser.add_argument("--monitor",   action="store_true", help="Check exits on open positions")
    parser.add_argument("--close-all", action="store_true", help="Close all positions")
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.movers:
        show_movers()
    elif args.monitor:
        monitor_exits(execute=args.execute)
    elif args.close_all:
        close_all()
    else:
        run(execute=args.execute)


if __name__ == "__main__":
    main()
