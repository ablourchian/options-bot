"""
Trade Screener — pulls your Alpaca options positions and closed orders,
scores each trade, and shows a good/bad breakdown with an HTML report.

Usage:
    python trade_screener.py           # opens HTML report in browser
    python trade_screener.py --no-open # just prints summary
"""
import os
import re
import argparse
import webbrowser
from datetime import datetime, timezone
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus, OrderSide

load_dotenv()

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)


def parse_option_symbol(symbol: str) -> dict:
    """Parse OCC option symbol e.g. HOOD260710C00086000"""
    m = re.match(r"([A-Z]+)(\d{6})([CP])(\d{8})", symbol)
    if not m:
        return {"underlying": symbol, "expiry": "", "type": "?", "strike": 0}
    underlying, exp, cp, strike_raw = m.groups()
    expiry = f"20{exp[:2]}-{exp[2:4]}-{exp[4:]}"
    strike = int(strike_raw) / 1000
    return {
        "underlying": underlying,
        "expiry": expiry,
        "type": "Call" if cp == "C" else "Put",
        "strike": strike,
    }


def get_client():
    return TradingClient(
        os.getenv("ALPACA_API_KEY"),
        os.getenv("ALPACA_SECRET_KEY"),
        paper=True,
    )


def fetch_open_positions(client):
    positions = client.get_all_positions()
    results = []
    for p in positions:
        info = parse_option_symbol(p.symbol)
        entry = float(p.avg_entry_price)
        current = float(p.current_price) if p.current_price else entry
        qty = float(p.qty)
        pnl = float(p.unrealized_pl)
        pnl_pct = float(p.unrealized_plpc) * 100

        results.append({
            "symbol": p.symbol,
            "underlying": info["underlying"],
            "expiry": info["expiry"],
            "type": info["type"],
            "strike": info["strike"],
            "entry": entry,
            "current": current,
            "qty": qty,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "status": "open",
            "filled_at": None,
        })
    return results


def fetch_closed_trades(client):
    req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=100)
    orders = client.get_orders(filter=req)

    # group buys and sells by underlying option symbol
    buys = {}
    sells = {}
    for o in orders:
        if not o.filled_avg_price:
            continue
        sym = o.symbol
        price = float(o.filled_avg_price)
        qty = float(o.filled_qty or o.qty)
        cost = price * qty * 100

        if o.side == OrderSide.BUY:
            if sym not in buys:
                buys[sym] = {"cost": 0, "qty": 0, "filled_at": o.filled_at}
            buys[sym]["cost"] += cost
            buys[sym]["qty"] += qty
            if o.filled_at and (buys[sym]["filled_at"] is None or o.filled_at < buys[sym]["filled_at"]):
                buys[sym]["filled_at"] = o.filled_at
        else:
            if sym not in sells:
                sells[sym] = {"proceeds": 0, "qty": 0, "filled_at": o.filled_at}
            sells[sym]["proceeds"] += cost
            sells[sym]["qty"] += qty

    # only closed trades (have both buy and sell)
    closed = []
    for sym in buys:
        if sym not in sells:
            continue
        info = parse_option_symbol(sym)
        buy = buys[sym]
        sell = sells[sym]
        pnl = sell["proceeds"] - buy["cost"]
        entry = buy["cost"] / (buy["qty"] * 100) if buy["qty"] else 0
        exit_price = sell["proceeds"] / (sell["qty"] * 100) if sell["qty"] else 0
        pnl_pct = (pnl / buy["cost"] * 100) if buy["cost"] else 0
        filled_at = buy["filled_at"]

        closed.append({
            "symbol": sym,
            "underlying": info["underlying"],
            "expiry": info["expiry"],
            "type": info["type"],
            "strike": info["strike"],
            "entry": entry,
            "current": exit_price,
            "qty": buy["qty"],
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "status": "closed",
            "filled_at": filled_at,
        })
    return closed


def score_trade(trade):
    """Return verdict: Good / Bad / Watch"""
    pnl_pct = trade["pnl_pct"]
    if trade["status"] == "open":
        if pnl_pct <= -20:
            return "Bad", "Down more than 20% — consider cutting"
        elif pnl_pct >= 25:
            return "Good", "Up 25%+ — consider taking profits"
        elif pnl_pct >= 10:
            return "Good", "Profitable and holding"
        elif pnl_pct < 0:
            return "Watch", "Small loss — monitor closely"
        else:
            return "Watch", "Breakeven — wait for move"
    else:
        if pnl_pct >= 20:
            return "Good", "Strong win"
        elif pnl_pct > 0:
            return "Good", "Profitable trade"
        elif pnl_pct >= -15:
            return "Watch", "Small loss"
        else:
            return "Bad", "Large loss"


def fmt_pct(v):
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}%"


def fmt_dollar(v):
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):,.0f}"


def build_row(t):
    verdict, reason = score_trade(t)
    color = {"Good": "#10b981", "Bad": "#ef4444", "Watch": "#f59e0b"}[verdict]
    bg = {"Good": "rgba(16,185,129,.08)", "Bad": "rgba(239,68,68,.08)", "Watch": "rgba(245,158,11,.08)"}[verdict]
    pnl_color = "#10b981" if t["pnl"] >= 0 else "#ef4444"
    type_badge = (
        '<span style="background:rgba(59,130,246,.15);color:#60a5fa;padding:2px 8px;border-radius:6px;font-size:.75rem;font-weight:600">CALL ▲</span>'
        if t["type"] == "Call" else
        '<span style="background:rgba(239,68,68,.15);color:#f87171;padding:2px 8px;border-radius:6px;font-size:.75rem;font-weight:600">PUT ▼</span>'
    )
    status_badge = (
        '<span style="background:rgba(59,130,246,.15);color:#60a5fa;padding:2px 8px;border-radius:6px;font-size:.72rem">OPEN</span>'
        if t["status"] == "open" else
        '<span style="background:rgba(107,114,128,.15);color:#9ca3af;padding:2px 8px;border-radius:6px;font-size:.72rem">CLOSED</span>'
    )
    label = "Current" if t["status"] == "open" else "Exit"
    date_str = t["filled_at"].strftime("%b %d") if t["filled_at"] else "—"

    return f"""
    <tr style="background:{bg}">
      <td><span style="color:{color};font-weight:700;font-size:.8rem">● {verdict}</span><br>
          <span style="color:#6b7280;font-size:.68rem">{reason}</span></td>
      <td style="font-weight:800;color:#f9fafb;font-size:.95rem">{t["underlying"]}</td>
      <td>{type_badge}</td>
      <td style="color:#9ca3af">${t["strike"]:.0f} · {t["expiry"]}</td>
      <td>{status_badge}</td>
      <td style="color:#9ca3af">{date_str}</td>
      <td>${t["entry"]:.2f}</td>
      <td>${t["current"]:.2f} <span style="color:#6b7280;font-size:.72rem">{label}</span></td>
      <td style="color:{pnl_color};font-weight:700">{fmt_dollar(t["pnl"])}</td>
      <td style="color:{pnl_color};font-weight:700">{fmt_pct(t["pnl_pct"])}</td>
    </tr>"""


def build_html(trades):
    open_trades = [t for t in trades if t["status"] == "open"]
    closed_trades = [t for t in trades if t["status"] == "closed"]

    total_open_pnl = sum(t["pnl"] for t in open_trades)
    total_closed_pnl = sum(t["pnl"] for t in closed_trades)
    winners = sum(1 for t in trades if t["pnl"] > 0)
    losers = sum(1 for t in trades if t["pnl"] < 0)
    win_rate = winners / len(trades) * 100 if trades else 0

    good = [t for t in trades if score_trade(t)[0] == "Good"]
    bad = [t for t in trades if score_trade(t)[0] == "Bad"]
    watch = [t for t in trades if score_trade(t)[0] == "Watch"]

    sorted_trades = sorted(trades, key=lambda t: t["pnl_pct"], reverse=True)

    rows_html = "".join(build_row(t) for t in sorted_trades)

    def stat(val, label, color="#3b82f6"):
        return f"""<div class="stat-card">
          <div style="font-size:1.8rem;font-weight:800;color:{color};line-height:1">{val}</div>
          <div style="font-size:.78rem;font-weight:600;color:#e5e7eb;margin-top:4px">{label}</div>
        </div>"""

    open_color = "#10b981" if total_open_pnl >= 0 else "#ef4444"
    closed_color = "#10b981" if total_closed_pnl >= 0 else "#ef4444"

    now = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trade Screener</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0a0a0f;color:#e5e7eb;font-family:'Inter',system-ui,sans-serif;font-size:14px;line-height:1.5}}
.topbar{{background:linear-gradient(135deg,#0f172a 0%,#1e1b4b 50%,#0f172a 100%);
  border-bottom:1px solid rgba(139,92,246,.2);padding:20px 32px;
  display:flex;justify-content:space-between;align-items:center}}
.topbar-title{{font-size:1.25rem;font-weight:800;color:#f9fafb}}
.topbar-title span{{color:#8b5cf6}}
.page{{max-width:1300px;margin:0 auto;padding:24px 32px}}
.section{{margin-bottom:36px}}
.section-title{{font-size:.7rem;font-weight:700;color:#6b7280;text-transform:uppercase;
  letter-spacing:.12em;margin-bottom:16px;display:flex;align-items:center;gap:8px}}
.section-title::after{{content:'';flex:1;height:1px;background:#1f2937}}
.stat-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px;margin-bottom:32px}}
.stat-card{{background:#111827;border:1px solid #1f2937;border-radius:12px;padding:16px}}
.verdict-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:32px}}
.verdict-card{{border-radius:12px;padding:20px;text-align:center}}
.tbl-wrap{{background:#111827;border:1px solid #1f2937;border-radius:12px;overflow:hidden}}
.tbl-scroll{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;white-space:nowrap}}
thead th{{background:#0f172a;color:#6b7280;font-size:.68rem;font-weight:600;text-transform:uppercase;
  letter-spacing:.08em;padding:10px 14px;border-bottom:1px solid #1f2937}}
tbody td{{padding:10px 14px;border-bottom:1px solid #111;font-size:.82rem;color:#d1d5db;vertical-align:middle}}
tbody tr:last-child td{{border-bottom:none}}
</style>
</head>
<body>
<div class="topbar">
  <div>
    <div class="topbar-title">Options <span>Trade Screener</span></div>
    <div style="font-size:.78rem;color:#6b7280;margin-top:3px">As of {now} · Alpaca Paper Account</div>
  </div>
</div>
<div class="page">

  <div class="section">
    <div class="section-title">Summary</div>
    <div class="stat-grid">
      {stat(len(trades), "Total Trades", "#8b5cf6")}
      {stat(len(open_trades), "Open", "#3b82f6")}
      {stat(len(closed_trades), "Closed", "#6b7280")}
      {stat(f"{win_rate:.0f}%", "Win Rate", "#10b981" if win_rate >= 50 else "#ef4444")}
      {stat(fmt_dollar(total_open_pnl), "Unrealized P&L", open_color)}
      {stat(fmt_dollar(total_closed_pnl), "Realized P&L", closed_color)}
    </div>
  </div>

  <div class="section">
    <div class="section-title">Verdict Breakdown</div>
    <div class="verdict-grid">
      <div class="verdict-card" style="background:rgba(16,185,129,.08);border:1px solid rgba(16,185,129,.3)">
        <div style="font-size:2.5rem;font-weight:800;color:#10b981">{len(good)}</div>
        <div style="font-weight:700;color:#10b981;margin-top:4px">Good Trades</div>
        <div style="font-size:.75rem;color:#6b7280;margin-top:6px">Profitable or on track</div>
      </div>
      <div class="verdict-card" style="background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.3)">
        <div style="font-size:2.5rem;font-weight:800;color:#f59e0b">{len(watch)}</div>
        <div style="font-weight:700;color:#f59e0b;margin-top:4px">Watch</div>
        <div style="font-size:.75rem;color:#6b7280;margin-top:6px">Small loss or unclear direction</div>
      </div>
      <div class="verdict-card" style="background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.3)">
        <div style="font-size:2.5rem;font-weight:800;color:#ef4444">{len(bad)}</div>
        <div style="font-weight:700;color:#ef4444;margin-top:4px">Bad Trades</div>
        <div style="font-size:.75rem;color:#6b7280;margin-top:6px">Large losses — review or cut</div>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">All Trades</div>
    <div class="tbl-wrap">
      <div class="tbl-scroll">
        <table>
          <thead><tr>
            <th>Verdict</th><th>Ticker</th><th>Type</th><th>Strike · Expiry</th>
            <th>Status</th><th>Date</th><th>Entry</th><th>Current/Exit</th>
            <th>P&L $</th><th>P&L %</th>
          </tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
    </div>
  </div>

</div>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    print("Fetching trades from Alpaca...")
    client = get_client()
    open_trades = fetch_open_positions(client)
    closed_trades = fetch_closed_trades(client)
    trades = open_trades + closed_trades

    print(f"  {len(open_trades)} open positions")
    print(f"  {len(closed_trades)} closed trades\n")

    for t in sorted(trades, key=lambda t: t["pnl_pct"], reverse=True):
        verdict, reason = score_trade(t)
        icon = {"Good": "✓", "Bad": "✗", "Watch": "~"}[verdict]
        print(f"  {icon} {t['underlying']:6} {t['type']:4} ${t['strike']:.0f}  {fmt_pct(t['pnl_pct']):>8}  {fmt_dollar(t['pnl']):>10}  {reason}")

    html = build_html(trades)
    out = os.path.join(RESULTS_DIR, "trade_screener.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n  Report → {out}")

    if not args.no_open:
        webbrowser.open(f"file:///{out.replace(os.sep, '/')}")


if __name__ == "__main__":
    main()
