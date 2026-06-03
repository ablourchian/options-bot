"""
Alpaca order executor — takes ranked trade rows from the scanner,
validates intraday signal direction, and places options orders.

Safety features:
  - Intraday signal filter: only trades where signal agrees with contract direction
  - Max open positions cap
  - Max spend per trade
  - Duplicate guard (won't re-enter a position already held today)
  - DRY RUN by default — set DRY_RUN=false in .env or pass --live to go live

Usage:
    python executor.py                  # dry run, top 3 A+ setups
    python executor.py --live           # LIVE trading — prompts YES to confirm
    python executor.py --min-grade A    # include A setups too
    python executor.py --max-trades 5
    python executor.py --no-signal-filter   # skip intraday signal check
    python executor.py --log            # print trade history
"""
import os
import csv
import json
import argparse
from datetime import date, datetime
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import OptionLegRequest, PlaceOptionOrderRequest
from alpaca.trading.enums import (
    OrderSide, OrderType, TimeInForce, PositionIntent,
    AssetClass,
)

load_dotenv()

API_KEY    = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

PAPER            = os.getenv("DRY_RUN", "true").lower() != "false"
MAX_POSITIONS    = int(os.getenv("MAX_POSITIONS", 10))
MAX_SPEND        = float(os.getenv("MAX_SPEND_PER_TRADE", 500))
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", 1000))

TRADE_LOG = os.path.join(os.path.dirname(__file__), "results", "trades.json")


def _trading_client(paper: bool = True):
    return TradingClient(API_KEY, SECRET_KEY, paper=paper)


# ── Trade log ─────────────────────────────────────────────────────────────────

def load_log() -> list[dict]:
    if os.path.exists(TRADE_LOG):
        with open(TRADE_LOG) as f:
            return json.load(f)
    return []


def save_log(log: list[dict]):
    os.makedirs(os.path.dirname(TRADE_LOG), exist_ok=True)
    with open(TRADE_LOG, "w") as f:
        json.dump(log, f, indent=2, default=str)


def open_today(log: list[dict]) -> list[dict]:
    today = str(date.today())
    return [t for t in log if t.get("date") == today and t.get("status") == "open"]


def already_held(symbol: str, log: list[dict]) -> bool:
    return any(t["underlying"] == symbol for t in open_today(log))


# ── Order helpers ─────────────────────────────────────────────────────────────

def contracts_to_buy(mid: float, max_spend: float) -> int:
    cost = mid * 100
    return max(1, int(max_spend / cost)) if cost > 0 else 0


def place_order(option_symbol: str, qty: int, side: OrderSide,
                limit_price: float, paper: bool = True, dry_run: bool = True):
    if dry_run:
        print(f"    [DRY RUN] {side.value} {qty}x {option_symbol} @ ${limit_price:.2f}")
        return "dry_run"

    client = _trading_client(paper=paper)
    order = client.submit_order(PlaceOptionOrderRequest(
        symbol=option_symbol,
        qty=qty,
        side=side,
        type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        limit_price=limit_price,
    ))
    print(f"    Order submitted: {order.id}")
    return str(order.id)


# ── Signal alignment check ────────────────────────────────────────────────────

def signal_agrees(contract_type: str, signal: dict) -> tuple[bool, str]:
    """
    Returns (agree: bool, reason: str).
    Call = needs oversold/bullish signal (stock going up).
    Put  = needs overbought/bearish signal (stock going down).
    """
    direction  = signal.get("direction", "neutral")
    confidence = signal.get("confidence", "weak")
    score      = signal.get("signal", 0)

    if contract_type == "call":
        if direction == "oversold" and confidence in ("strong", "moderate"):
            return True, f"oversold ({score:+d}) — supports calls"
        elif direction == "overbought":
            return False, f"overbought ({score:+d}) — opposes calls"
        else:
            return False, f"neutral/weak signal ({score:+d}) — skip"
    else:  # put
        if direction == "overbought" and confidence in ("strong", "moderate"):
            return True, f"overbought ({score:+d}) — supports puts"
        elif direction == "oversold":
            return False, f"oversold ({score:+d}) — opposes puts"
        else:
            return False, f"neutral/weak signal ({score:+d}) — skip"


# ── Main executor ─────────────────────────────────────────────────────────────

def execute_trades(ranked_rows: list[dict], dry_run: bool = True,
                   paper: bool = True, min_grade: str = "A+",
                   max_trades: int = 3, max_spend: float = None,
                   use_signal_filter: bool = True):

    from intraday import get_signal

    max_spend = max_spend or MAX_SPEND
    log = load_log()
    open_pos = open_today(log)

    if len(open_pos) >= MAX_POSITIONS:
        print(f"  Max positions ({MAX_POSITIONS}) reached. No new trades.")
        return []

    # Fetch account info
    try:
        client = _trading_client(paper=paper)
        acct = client.get_account()
        buying_power = float(acct.buying_power)
        print(f"  Buying power: ${buying_power:,.2f}  |  "
              f"Portfolio: ${float(acct.portfolio_value):,.2f}")
    except Exception as e:
        print(f"  [!] Could not fetch account: {e}")
        buying_power = float("inf") if dry_run else 0
        if not dry_run and buying_power == 0:
            return []

    grade_order = ["A+", "A", "B", "C", "D"]
    min_idx = grade_order.index(min_grade) if min_grade in grade_order else 0
    eligible = [
        r for r in ranked_rows
        if r.get("grade") in grade_order[:min_idx + 1]
        and r.get("mid") and r["mid"] > 0
        and not already_held(r["underlying"], log)
    ]

    print(f"  {len(eligible)} eligible setups (grade ≥ {min_grade}, no duplicates)\n")

    # Cache signals per underlying (don't re-fetch for same stock)
    signal_cache = {}

    placed = []
    for row in eligible:
        if len(open_pos) + len(placed) >= MAX_POSITIONS:
            break
        if len(placed) >= max_trades:
            break

        sym   = row["underlying"]
        otype = row["type"]
        mid   = row["mid"]
        qty   = contracts_to_buy(mid, max_spend)
        cost  = qty * mid * 100

        print(f"  Evaluating {sym} {otype.upper()} ${row['strike']} "
              f"(grade {row['grade']}, score {row.get('score',0):.1f})")

        # Buying power check
        if cost > buying_power * 0.15:
            print(f"    Skip — cost ${cost:.0f} > 15% of buying power\n")
            continue

        # Intraday signal check
        if use_signal_filter:
            if sym not in signal_cache:
                print(f"    Fetching intraday signal for {sym}...")
                signal_cache[sym] = get_signal(sym)
            sig = signal_cache.get(sym)

            if sig is None:
                print(f"    Skip — could not fetch intraday data\n")
                continue

            agrees, reason = signal_agrees(otype, sig)
            print(f"    Signal: {sig['direction']} ({sig['signal']:+d}) — {reason}")
            if not agrees:
                print(f"    Skip — signal does not support this trade\n")
                continue

        limit = round(mid * 1.02, 2)  # 2% above mid for better fill odds
        side  = OrderSide.BUY

        print(f"    ✓ Placing: {qty}x @ limit ${limit}  (total ${cost:.2f})")

        order_id = place_order(
            option_symbol=row["symbol"],
            qty=qty,
            side=side,
            limit_price=limit,
            paper=paper,
            dry_run=dry_run,
        )

        entry = {
            "date":          str(date.today()),
            "timestamp":     str(datetime.now()),
            "underlying":    sym,
            "option_symbol": row["symbol"],
            "type":          otype,
            "strike":        row["strike"],
            "dte":           row["dte"],
            "quantity":      qty,
            "entry_price":   limit,
            "total_cost":    round(cost, 2),
            "grade":         row["grade"],
            "score":         row.get("score"),
            "iv_rank":       row.get("iv_rank"),
            "delta":         row.get("delta"),
            "intraday_signal": signal_cache.get(sym, {}).get("signal"),
            "intraday_dir":    signal_cache.get(sym, {}).get("direction"),
            "order_id":      order_id,
            "status":        "dry_run" if dry_run else "open",
        }
        log.append(entry)
        placed.append(entry)
        print()

    save_log(log)
    print(f"  {'[DRY RUN] ' if dry_run else ''}Placed {len(placed)} order(s).")
    return placed


def print_log(n: int = 30):
    log = load_log()
    if not log:
        print("  No trades logged yet.")
        return

    print(f"\n  {'Date':<12} {'Status':<10} {'Sym':<8} {'Type':<5} "
          f"{'Strike':>7} {'Qty':>4} {'Entry':>7} {'Cost':>8} {'Grade':<5} {'Score':>5} {'Signal':<12}")
    print(f"  {'-'*90}")
    for t in log[-n:]:
        sig = f"{t.get('intraday_dir','—')} ({t.get('intraday_signal','—'):+})" \
              if t.get("intraday_signal") is not None else "—"
        print(
            f"  {t['date']:<12} {t['status']:<10} {t['underlying']:<8} {t['type']:<5} "
            f"{t['strike']:>7} {t['quantity']:>4} ${t['entry_price']:>6.2f} "
            f"${t['total_cost']:>7.2f} {t['grade']:<5} {t.get('score',0):>5.1f} {sig}"
        )
    print()


def main():
    parser = argparse.ArgumentParser(description="Alpaca options executor")
    parser.add_argument("--live",  action="store_true",
                        help="Submit real orders (default: dry run on paper)")
    parser.add_argument("--real-money", action="store_true",
                        help="Use live Alpaca account instead of paper")
    parser.add_argument("--min-grade",  default="A+", choices=["A+","A","B"])
    parser.add_argument("--max-trades", type=int, default=3)
    parser.add_argument("--max-spend",  type=float, default=MAX_SPEND)
    parser.add_argument("--no-signal-filter", action="store_true",
                        help="Skip intraday signal alignment check")
    parser.add_argument("--log", action="store_true")
    args = parser.parse_args()

    if args.log:
        print_log()
        return

    dry_run = not args.live
    paper   = not args.real_money

    if not dry_run:
        mode = "LIVE REAL MONEY" if not paper else "LIVE PAPER"
        confirm = input(f"\n  ⚠️  {mode} mode. Type YES to confirm: ")
        if confirm.strip() != "YES":
            print("  Cancelled.")
            return

    results_dir = os.path.join(os.path.dirname(__file__), "results")
    today_csv   = os.path.join(results_dir, f"{date.today()}.csv")
    if not os.path.exists(today_csv):
        print(f"  No scan results for today. Run daily_scan.py first.")
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
            rows.append(r)

    ranked = sorted(rows, key=lambda r: r.get("score") or 0, reverse=True)
    print(f"\n  {len(ranked)} contracts loaded from today's scan")

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
