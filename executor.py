"""
Alpaca paper options executor — takes today's top ranked setups,
validates intraday signal direction, and places paper option orders.

Safety:
  - Intraday signal must agree with contract direction
  - Max 15% of buying power per trade
  - Max N open positions
  - Hard stop: manual YES confirmation for live money
  - DRY RUN by default

Usage:
    python executor.py                        # dry run top 3 A+ setups
    python executor.py --live                 # paper trade (prompts YES)
    python executor.py --min-grade A          # include A setups
    python executor.py --max-trades 5
    python executor.py --max-spend 500
    python executor.py --no-signal-filter     # skip intraday check
    python executor.py --log                  # print trade history
"""
import os
import csv
import json
import argparse
from datetime import date, datetime
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest
from alpaca.trading.enums import OrderSide, OrderType, TimeInForce, AssetClass

load_dotenv()

API_KEY    = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

MAX_POSITIONS    = int(os.getenv("MAX_POSITIONS", 10))
MAX_SPEND        = float(os.getenv("MAX_SPEND_PER_TRADE", 500))
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", 1000))

TRADE_LOG = os.path.join(os.path.dirname(__file__), "results", "trades.json")


def _client(paper=True):
    return TradingClient(API_KEY, SECRET_KEY, paper=paper)


# ── Trade log ─────────────────────────────────────────────────────────────────

def load_log():
    if os.path.exists(TRADE_LOG):
        with open(TRADE_LOG) as f:
            return json.load(f)
    return []


def save_log(log):
    os.makedirs(os.path.dirname(TRADE_LOG), exist_ok=True)
    with open(TRADE_LOG, "w") as f:
        json.dump(log, f, indent=2, default=str)


def open_today(log):
    today = str(date.today())
    return [t for t in log if t.get("date") == today and t.get("status") == "open"]


def already_held(symbol, log):
    return any(t["underlying"] == symbol for t in open_today(log))


# ── Order placement ───────────────────────────────────────────────────────────

def contracts_to_buy(mid, max_spend):
    cost = mid * 100
    return max(1, int(max_spend / cost)) if cost > 0 else 0


def place_order(option_symbol, qty, limit_price, paper=True, dry_run=True):
    if dry_run:
        print(f"    [DRY RUN] BUY {qty}x {option_symbol} @ ${limit_price:.2f}  "
              f"(total ~${qty * limit_price * 100:.0f})")
        return "dry_run"

    client = _client(paper=paper)
    req = LimitOrderRequest(
        symbol=option_symbol,
        qty=qty,
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        limit_price=round(limit_price, 2),
    )
    order = client.submit_order(req)
    print(f"    Order submitted: {order.id}  status={order.status}")
    return str(order.id)


# ── Signal alignment ──────────────────────────────────────────────────────────

def signal_agrees(contract_type, signal):
    direction  = signal.get("direction", "neutral")
    confidence = signal.get("confidence", "weak")
    score      = signal.get("signal", 0)

    if contract_type == "call":
        if direction == "oversold" and confidence in ("strong", "moderate"):
            return True, f"oversold ({score:+d}) — supports calls"
        elif direction == "overbought":
            return False, f"overbought ({score:+d}) — opposes calls"
        return False, f"neutral ({score:+d}) — skip"
    else:
        if direction == "overbought" and confidence in ("strong", "moderate"):
            return True, f"overbought ({score:+d}) — supports puts"
        elif direction == "oversold":
            return False, f"oversold ({score:+d}) — opposes puts"
        return False, f"neutral ({score:+d}) — skip"


# ── Main executor ─────────────────────────────────────────────────────────────

def execute_trades(ranked_rows, dry_run=True, paper=True, min_grade="A+",
                   max_trades=3, max_spend=None, use_signal_filter=True):

    from intraday import get_signal

    max_spend = max_spend or MAX_SPEND
    log = load_log()
    open_pos = open_today(log)

    if len(open_pos) >= MAX_POSITIONS:
        print(f"  Max positions ({MAX_POSITIONS}) reached — no new trades.")
        return []

    try:
        client = _client(paper=paper)
        acct   = client.get_account()
        buying_power = float(acct.buying_power)
        print(f"  Account: ${float(acct.portfolio_value):,.2f} portfolio  "
              f"${buying_power:,.2f} buying power")
    except Exception as e:
        print(f"  [!] Could not fetch account: {e}")
        buying_power = 999999 if dry_run else 0
        if not dry_run and buying_power == 0:
            return []

    # score threshold: A+=85, A=70, B=55
    score_floor = {"A+": 70, "A": 55, "B": 40}.get(min_grade, 70)
    eligible = [
        r for r in ranked_rows
        if (r.get("score") or 0) >= score_floor
        and r.get("contract") and r["contract"].get("symbol")
        and (r["contract"].get("mid") or 0) > 0
        and not already_held(r.get("underlying",""), log)
    ]

    print(f"  {len(eligible)} eligible setups (score ≥ {score_floor}, has contract, no duplicates)\n")

    signal_cache = {}
    placed = []

    for row in eligible:
        if len(open_pos) + len(placed) >= MAX_POSITIONS:
            break
        if len(placed) >= max_trades:
            break

        sym   = row.get("underlying","")
        otype = row.get("direction","CALL").lower()
        c     = row["contract"]
        mid   = float(c.get("mid") or 0)
        qty   = contracts_to_buy(mid, max_spend)
        cost  = qty * mid * 100

        print(f"  Evaluating {sym} {otype.upper()} ${c.get('strike','')}  "
              f"grade={row.get('grade','?')}  score={row.get('score',0):.0f}")

        if cost > buying_power * 0.15:
            print(f"    Skip — ${cost:.0f} > 15% of buying power\n")
            continue

        # Intraday signal check
        if use_signal_filter:
            if sym not in signal_cache:
                print(f"    Fetching intraday signal...")
                try:
                    signal_cache[sym] = get_signal(sym)
                except Exception:
                    signal_cache[sym] = None

            sig = signal_cache.get(sym)
            if sig:
                agrees, reason = signal_agrees(otype, sig)
                print(f"    Signal: {sig['direction']} ({sig['signal']:+d}) — {reason}")
                if not agrees:
                    print(f"    Skip — signal doesn't support this trade\n")
                    continue
            else:
                print(f"    No signal data — proceeding anyway")

        limit = round(mid * 1.02, 2)
        print(f"    Placing: {qty}x {c['symbol']} @ ${limit:.2f}  (~${cost:.0f})")

        order_id = place_order(
            option_symbol=c["symbol"],
            qty=qty,
            limit_price=limit,
            paper=paper,
            dry_run=dry_run,
        )

        entry = {
            "date":          str(date.today()),
            "timestamp":     str(datetime.now()),
            "underlying":    sym,
            "option_symbol": c["symbol"],
            "type":          otype,
            "strike":        c.get("strike"),
            "dte":           c.get("dte"),
            "quantity":      qty,
            "entry_price":   limit,
            "total_cost":    round(cost, 2),
            "grade":         row.get("grade"),
            "score":         row.get("score"),
            "iv_rank":       row.get("iv_rank"),
            "delta":         c.get("delta"),
            "intraday_signal": (signal_cache.get(sym) or {}).get("signal"),
            "intraday_dir":    (signal_cache.get(sym) or {}).get("direction"),
            "order_id":      order_id,
            "status":        "dry_run" if dry_run else "open",
        }
        log.append(entry)
        placed.append(entry)
        print()

    save_log(log)
    print(f"  {'[DRY RUN] ' if dry_run else ''}Placed {len(placed)} order(s).")
    return placed


def print_log(n=30):
    log = load_log()
    if not log:
        print("  No trades logged yet.")
        return

    print(f"\n  {'Date':<12} {'Status':<10} {'Sym':<8} {'Type':<5} "
          f"{'Strike':>7} {'Qty':>4} {'Entry':>7} {'Cost':>8} {'Grade':<5} {'Score':>5} {'Signal'}")
    print(f"  {'-'*90}")
    for t in log[-n:]:
        sig = f"{t.get('intraday_dir','—')} ({t.get('intraday_signal') or '—'})" \
              if t.get("intraday_signal") is not None else "—"
        print(f"  {t['date']:<12} {t['status']:<10} {t.get('underlying',''):<8} "
              f"{t.get('type',''):<5} {str(t.get('strike',''))[:7]:>7} "
              f"{t.get('quantity',0):>4} ${t.get('entry_price',0):>6.2f} "
              f"${t.get('total_cost',0):>7.0f} {t.get('grade',''):<5} "
              f"{t.get('score',0):>5.1f} {sig}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Alpaca paper options executor")
    parser.add_argument("--live",            action="store_true",
                        help="Submit real paper orders (default: dry run)")
    parser.add_argument("--real-money",      action="store_true",
                        help="Use live Alpaca account (NOT paper)")
    parser.add_argument("--min-grade",       default="A+", choices=["A+","A","B"])
    parser.add_argument("--max-trades",      type=int,   default=3)
    parser.add_argument("--max-spend",       type=float, default=MAX_SPEND)
    parser.add_argument("--no-signal-filter",action="store_true")
    parser.add_argument("--log",             action="store_true")
    args = parser.parse_args()

    if args.log:
        print_log()
        return

    dry_run = not args.live
    paper   = not args.real_money

    if not dry_run:
        mode = "LIVE REAL MONEY" if not paper else "LIVE PAPER"
        confirm = input(f"\n  {mode} — type YES to confirm: ")
        if confirm.strip() != "YES":
            print("  Cancelled.")
            return

    results_dir = os.path.join(os.path.dirname(__file__), "results")
    today_csv   = os.path.join(results_dir, f"daytrade_{date.today()}.csv")

    if not os.path.exists(today_csv):
        print(f"  No scan for today. Run day_trade.py first.")
        return

    rows = []
    with open(today_csv, newline="") as f:
        for r in csv.DictReader(f):
            for field in ["score","mid","bid","ask","strike","dte","spot",
                          "iv","iv_rank","iv_hv_ratio","delta","theta"]:
                try:
                    r[field] = float(r[field]) if r.get(field) not in ("","None",None) else None
                except (ValueError, KeyError):
                    r[field] = None
            r["contract"] = {
                "symbol":   r.get("c_symbol",""),
                "strike":   r.get("c_strike",""),
                "dte":      r.get("c_dte",""),
                "expiry":   r.get("c_expiry",""),
                "mid":      float(r["c_mid"]) if r.get("c_mid") else 0,
                "bid":      float(r["c_bid"]) if r.get("c_bid") else 0,
                "ask":      float(r["c_ask"]) if r.get("c_ask") else 0,
                "delta":    r.get("delta"),
                "theta":    r.get("theta"),
            } if r.get("c_symbol") else {}
            rows.append(r)

    ranked = sorted(rows, key=lambda r: r.get("score") or 0, reverse=True)
    print(f"\n  {len(ranked)} contracts from today's scan")

    execute_trades(
        ranked,
        dry_run=dry_run,
        paper=paper,
        min_grade=args.min_grade,
        max_trades=args.max_trades,
        max_spend=args.max_spend,
        use_signal_filter=not args.no_signal_filter,
    )


if __name__ == "__main__":
    main()
