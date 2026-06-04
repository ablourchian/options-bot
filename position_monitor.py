"""
Position monitor — checks open paper positions every 60 seconds.
Auto-sells when:
  • Up   >= TAKE_PROFIT  (default +15%)
  • Down <= STOP_LOSS    (default -7.5%)

Also sends a Discord alert on every fill.

Usage:
    python position_monitor.py                    # monitor with defaults
    python position_monitor.py --tp 20 --sl 10    # +20% target / -10% stop
    python position_monitor.py --interval 30      # check every 30s
    python position_monitor.py --dry-run          # alert only, no actual sells
    python position_monitor.py --status           # print open positions and exit
"""
import os
import json
import argparse
import time
import urllib.request
from datetime import date, datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, OrderType, TimeInForce, QueryOrderStatus
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionLatestQuoteRequest

load_dotenv()

ET         = ZoneInfo("America/New_York")
API_KEY    = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
WEBHOOK    = os.getenv("DISCORD_WEBHOOK")
TRADE_LOG  = os.path.join(os.path.dirname(__file__), "results", "trades.json")

TAKE_PROFIT = 15.0   # % gain to take profit
STOP_LOSS   = 7.5    # % loss to stop out


# ── Clients ───────────────────────────────────────────────────────────────────

def trading_client():
    return TradingClient(API_KEY, SECRET_KEY, paper=True)

def option_data_client():
    return OptionHistoricalDataClient(API_KEY, SECRET_KEY)


# ── Trade log helpers ─────────────────────────────────────────────────────────

def load_log():
    if os.path.exists(TRADE_LOG):
        with open(TRADE_LOG) as f:
            return json.load(f)
    return []

def save_log(log):
    with open(TRADE_LOG, "w") as f:
        json.dump(log, f, indent=2, default=str)

def get_open_positions(log):
    return [t for t in log if t.get("status") == "open"]


# ── Live quotes ───────────────────────────────────────────────────────────────

def fetch_quotes(option_symbols):
    """Returns {symbol: mid_price}"""
    if not option_symbols:
        return {}
    try:
        oc = option_data_client()
        quotes = oc.get_option_latest_quote(
            OptionLatestQuoteRequest(symbol_or_symbols=option_symbols)
        )
        result = {}
        for sym, q in quotes.items():
            if q.bid_price and q.ask_price:
                result[sym] = round((q.bid_price + q.ask_price) / 2, 2)
        return result
    except Exception as e:
        print(f"  [!] Quote fetch error: {e}")
        return {}


# ── Order placement ───────────────────────────────────────────────────────────

def cancel_open_buy(option_symbol, buy_order_id=None, dry_run=False):
    """Cancel a pending buy order for the symbol if it hasn't filled yet."""
    if dry_run:
        print(f"    [DRY RUN] CANCEL buy order for {option_symbol}")
        return True
    try:
        client = trading_client()
        # Cancel by order ID if we have it
        if buy_order_id and buy_order_id not in ("dry_run", None):
            try:
                client.cancel_order_by_id(buy_order_id)
                print(f"    Cancelled buy order {buy_order_id}")
                return True
            except Exception:
                pass
        # Otherwise scan open orders for this symbol
        orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
        for o in orders:
            if o.symbol == option_symbol and o.side == OrderSide.BUY:
                client.cancel_order_by_id(str(o.id))
                print(f"    Cancelled open buy order {o.id} for {option_symbol}")
                return True
        return False
    except Exception as e:
        print(f"    [!] Cancel failed: {e}")
        return False


def sell_to_close(option_symbol, qty, limit_price, buy_order_id=None, dry_run=False):
    """
    Cancel any pending buy first, then place sell-to-close.
    If the buy was never filled, cancelling it IS the exit.
    """
    if dry_run:
        print(f"    [DRY RUN] EXIT {qty}x {option_symbol} @ ${limit_price:.2f}")
        return "dry_run"
    try:
        client = trading_client()
        # Check if buy order is still open — if so, just cancel it
        if buy_order_id and buy_order_id not in ("dry_run", None):
            try:
                order = client.get_order_by_id(buy_order_id)
                if order.status.value in ("new", "accepted", "pending_new", "held"):
                    client.cancel_order_by_id(buy_order_id)
                    print(f"    Buy order {buy_order_id} cancelled (never filled)")
                    return f"cancelled_{buy_order_id}"
            except Exception:
                pass

        # Buy was filled — place a sell-to-close
        limit = round(limit_price * 0.98, 2)
        req = LimitOrderRequest(
            symbol=option_symbol,
            qty=qty,
            side=OrderSide.SELL,
            type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY,
            limit_price=limit,
        )
        order = client.submit_order(req)
        print(f"    SELL order submitted: {order.id}  status={order.status}")
        return str(order.id)
    except Exception as e:
        print(f"    [!] Exit failed: {e}")
        return None


# ── Discord alert ─────────────────────────────────────────────────────────────

def discord_alert(msg, color=0x10b981):
    if not WEBHOOK:
        return
    import json as _json
    payload = {"embeds": [{"description": msg, "color": color}]}
    data = _json.dumps(payload).encode()
    req  = urllib.request.Request(
        WEBHOOK, data=data,
        headers={"Content-Type": "application/json",
                 "User-Agent": "DiscordBot (options-bot, 1.0)"},
        method="POST"
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


# ── Print positions ───────────────────────────────────────────────────────────

def print_positions(positions, quotes, tp, sl):
    if not positions:
        print("  No open positions.")
        return

    print(f"\n  {'Symbol':<28} {'Qty':>4} {'Entry':>8} {'Now':>8} "
          f"{'P&L$':>8} {'P&L%':>7} {'Status'}")
    print(f"  {'-'*80}")

    for p in positions:
        sym   = p["option_symbol"]
        qty   = p.get("quantity", 1)
        entry = float(p.get("entry_price", 0))
        now   = quotes.get(sym)
        if now and entry > 0:
            pnl_pct = (now - entry) / entry * 100
            pnl_dol = (now - entry) * qty * 100
            if pnl_pct >= tp:
                status = f"TAKE PROFIT (+{pnl_pct:.1f}%)"
            elif pnl_pct <= -sl:
                status = f"STOP LOSS ({pnl_pct:.1f}%)"
            else:
                status = f"holding ({pnl_pct:+.1f}%)"
            print(f"  {sym:<28} {qty:>4} ${entry:>7.2f} ${now:>7.2f} "
                  f"${pnl_dol:>+8.0f} {pnl_pct:>+6.1f}%  {status}")
        else:
            print(f"  {sym:<28} {qty:>4} ${entry:>7.2f} {'—':>8} {'—':>8} {'—':>7}  (no quote)")

    print()


# ── Main monitor loop ─────────────────────────────────────────────────────────

def market_is_open():
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    h = now.hour + now.minute / 60
    return 9.5 <= h < 16.0


def run_monitor(tp=TAKE_PROFIT, sl=STOP_LOSS, interval=60, dry_run=False):
    print(f"\n{'='*60}")
    print(f"  POSITION MONITOR")
    print(f"  Take Profit: +{tp}%   Stop Loss: -{sl}%")
    print(f"  Interval: {interval}s   Dry Run: {dry_run}")
    print(f"{'='*60}\n")

    cycle = 0
    while True:
        now = datetime.now(ET)

        if not market_is_open():
            if now.hour >= 16:
                print(f"  Market closed. Monitor stopped.")
                break
            print(f"  [{now.strftime('%H:%M')}] Market not open — waiting...")
            time.sleep(60)
            continue

        cycle += 1
        log       = load_log()
        positions = get_open_positions(log)

        if not positions:
            print(f"  [{now.strftime('%H:%M:%S')}] No open positions.")
            time.sleep(interval)
            continue

        # Fetch live quotes for all open positions
        syms   = list({p["option_symbol"] for p in positions})
        quotes = fetch_quotes(syms)
        ts     = now.strftime("%H:%M:%S")

        print(f"  [{ts}] Cycle {cycle} — {len(positions)} position(s)")
        print_positions(positions, quotes, tp, sl)

        changed = False
        for p in log:
            if p.get("status") != "open":
                continue

            sym   = p["option_symbol"]
            qty   = p.get("quantity", 1)
            entry = float(p.get("entry_price", 0))
            mid   = quotes.get(sym)

            if not mid or entry <= 0:
                continue

            pnl_pct = (mid - entry) / entry * 100
            pnl_dol = (mid - entry) * qty * 100

            # Take profit
            if pnl_pct >= tp:
                reason = f"TAKE PROFIT +{pnl_pct:.1f}%"
                print(f"  *** {reason} — selling {qty}x {sym}")
                order_id = sell_to_close(sym, qty, mid, dry_run=dry_run)
                p["status"]      = "closed" if not dry_run else "dry_close"
                p["exit_price"]  = mid
                p["exit_pnl_pct"]= round(pnl_pct, 2)
                p["exit_pnl_dol"]= round(pnl_dol, 2)
                p["exit_reason"] = reason
                p["exit_time"]   = str(datetime.now())
                p["sell_order"]  = order_id
                changed = True
                discord_alert(
                    f"**TAKE PROFIT** {sym}\n"
                    f"Entry ${entry:.2f} → Exit ${mid:.2f}\n"
                    f"**+{pnl_pct:.1f}%  +${pnl_dol:,.0f}**",
                    color=0x10b981
                )

            # Stop loss
            elif pnl_pct <= -sl:
                reason = f"STOP LOSS {pnl_pct:.1f}%"
                print(f"  *** {reason} — selling {qty}x {sym}")
                order_id = sell_to_close(sym, qty, mid, dry_run=dry_run)
                p["status"]      = "closed" if not dry_run else "dry_close"
                p["exit_price"]  = mid
                p["exit_pnl_pct"]= round(pnl_pct, 2)
                p["exit_pnl_dol"]= round(pnl_dol, 2)
                p["exit_reason"] = reason
                p["exit_time"]   = str(datetime.now())
                p["sell_order"]  = order_id
                changed = True
                discord_alert(
                    f"**STOP LOSS** {sym}\n"
                    f"Entry ${entry:.2f} → Exit ${mid:.2f}\n"
                    f"**{pnl_pct:.1f}%  -${abs(pnl_dol):,.0f}**",
                    color=0xef4444
                )

        if changed:
            save_log(log)

        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Options position monitor")
    parser.add_argument("--tp",       type=float, default=TAKE_PROFIT,
                        help=f"Take profit %% (default {TAKE_PROFIT})")
    parser.add_argument("--sl",       type=float, default=STOP_LOSS,
                        help=f"Stop loss %% (default {STOP_LOSS})")
    parser.add_argument("--interval", type=int,   default=60)
    parser.add_argument("--dry-run",  action="store_true",
                        help="Alert only — don't actually submit sell orders")
    parser.add_argument("--status",   action="store_true",
                        help="Print positions and exit")
    args = parser.parse_args()

    if args.status:
        log       = load_log()
        positions = get_open_positions(log)
        syms      = [p["option_symbol"] for p in positions]
        quotes    = fetch_quotes(syms) if syms else {}
        print_positions(positions, quotes, args.tp, args.sl)
        return

    run_monitor(tp=args.tp, sl=args.sl, interval=args.interval, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
