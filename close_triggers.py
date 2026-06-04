"""Run once — closes any positions that have already hit TP or SL."""
from position_monitor import (load_log, get_open_positions, fetch_quotes,
                               print_positions, sell_to_close, save_log, discord_alert)
from datetime import datetime

TP = 15.0
SL = 7.5

log       = load_log()
positions = get_open_positions(log)
syms      = [p["option_symbol"] for p in positions]
quotes    = fetch_quotes(syms)

print("\nCurrent positions:")
print_positions(positions, quotes, TP, SL)

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

    if pnl_pct >= TP:
        reason = f"TAKE PROFIT +{pnl_pct:.1f}%"
        print(f"  Firing {reason} on {sym}  +${pnl_dol:.0f}")
        order_id = sell_to_close(sym, qty, mid, buy_order_id=p.get("order_id"), dry_run=False)
        p.update({
            "status": "closed", "exit_price": mid,
            "exit_pnl_pct": round(pnl_pct, 2), "exit_pnl_dol": round(pnl_dol, 2),
            "exit_reason": reason, "exit_time": str(datetime.now()),
            "sell_order": order_id,
        })
        changed = True
        discord_alert(
            f"**TAKE PROFIT** {sym}\nEntry ${entry:.2f} -> Exit ${mid:.2f}\n"
            f"**+{pnl_pct:.1f}%  +${pnl_dol:,.0f}**", 0x10b981)

    elif pnl_pct <= -SL:
        reason = f"STOP LOSS {pnl_pct:.1f}%"
        print(f"  Firing {reason} on {sym}  -${abs(pnl_dol):.0f}")
        order_id = sell_to_close(sym, qty, mid, buy_order_id=p.get("order_id"), dry_run=False)
        p.update({
            "status": "closed", "exit_price": mid,
            "exit_pnl_pct": round(pnl_pct, 2), "exit_pnl_dol": round(pnl_dol, 2),
            "exit_reason": reason, "exit_time": str(datetime.now()),
            "sell_order": order_id,
        })
        changed = True
        discord_alert(
            f"**STOP LOSS** {sym}\nEntry ${entry:.2f} → Exit ${mid:.2f}\n"
            f"**{pnl_pct:.1f}%  -${abs(pnl_dol):,.0f}**", 0xef4444)

if changed:
    save_log(log)
    print("\nTrade log updated.")
    print("\nRemaining open positions:")
    print_positions(get_open_positions(log), quotes, TP, SL)
else:
    print("No triggers hit — all positions within bounds.")
