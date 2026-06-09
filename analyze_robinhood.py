"""
Robinhood Trade Analysis — reads your consolidated PDF statements,
parses every options trade, and generates a full good/bad breakdown report.

Usage:
    python analyze_robinhood.py
    python analyze_robinhood.py --pdf /path/to/statements.pdf
"""
import os
import re
import argparse
import webbrowser
from collections import defaultdict
from datetime import datetime

PDF_DEFAULT = os.path.expanduser("~/Downloads/Consolidated_Robinhood_Statements.pdf")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)


def parse_pdf(path):
    import pdfplumber
    trades = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            for line in text.split("\n"):
                m = re.search(
                    r"(\w+)\s+(\d{2}/\d{2}/\d{4})\s+(Call|Put)\s+\$([0-9,]+\.?\d*)"
                    r"\s+\w+\s+Cash\s+(BTO|STC|STO|BTC)\s+(\d{2}/\d{2}/\d{4})"
                    r"\s+([\d.]+)\s+\$([0-9,]+\.?\d*)\s+\$([0-9,]+\.?\d*)",
                    line,
                )
                if m:
                    sym, exp, otype, strike, action, trade_date, qty, price, total = m.groups()
                    trades.append({
                        "sym": sym,
                        "exp": exp,
                        "type": otype,
                        "strike": float(strike.replace(",", "")),
                        "action": action,
                        "date": trade_date,
                        "qty": float(qty),
                        "price": float(price.replace(",", "")),
                        "total": float(total.replace(",", "")),
                    })
    return trades


def build_positions(trades):
    spent = defaultdict(float)
    earned = defaultdict(float)
    buy_dates = {}
    sell_dates = {}
    buy_prices = {}
    sell_prices = {}
    sym_map = {}
    type_map = {}

    for t in trades:
        key = f"{t['sym']} {t['type']} ${t['strike']:.0f} {t['exp']}"
        sym_map[key] = t["sym"]
        type_map[key] = t["type"]
        if t["action"] in ("BTO", "STO"):
            spent[key] += t["total"]
            if key not in buy_dates:
                buy_dates[key] = t["date"]
                buy_prices[key] = t["price"]
        else:
            earned[key] += t["total"]
            sell_dates[key] = t["date"]
            sell_prices[key] = t["price"]

    results = []
    for key in set(list(spent.keys()) + list(earned.keys())):
        pnl = earned[key] - spent[key]
        pnl_pct = pnl / spent[key] * 100 if spent[key] else 0
        buy_dt = datetime.strptime(buy_dates.get(key, "01/01/2026"), "%m/%d/%Y")
        sell_dt = datetime.strptime(sell_dates.get(key, buy_dates.get(key, "01/01/2026")), "%m/%d/%Y")
        hold = (sell_dt - buy_dt).days
        results.append({
            "key": key,
            "sym": sym_map.get(key, "?"),
            "type": type_map.get(key, "?"),
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "spent": spent[key],
            "hold": hold,
            "buy_date": buy_dates.get(key, ""),
            "buy_price": buy_prices.get(key, 0),
            "sell_price": sell_prices.get(key, 0),
            "closed": key in sell_dates,
        })
    return sorted(results, key=lambda x: x["pnl"])


def verdict(r):
    if not r["closed"]:
        return "Open"
    if r["pnl"] > 5000:
        return "Great Win"
    if r["pnl"] > 0:
        return "Win"
    if r["pnl"] > -3000:
        return "Small Loss"
    return "Bad Loss"


def verdict_color(v):
    return {
        "Great Win": "#10b981",
        "Win": "#34d399",
        "Open": "#3b82f6",
        "Small Loss": "#f59e0b",
        "Bad Loss": "#ef4444",
    }.get(v, "#6b7280")


def verdict_bg(v):
    return {
        "Great Win": "rgba(16,185,129,.09)",
        "Win": "rgba(52,211,153,.06)",
        "Open": "rgba(59,130,246,.07)",
        "Small Loss": "rgba(245,158,11,.08)",
        "Bad Loss": "rgba(239,68,68,.09)",
    }.get(v, "transparent")


def fmt_dollar(v):
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):,.0f}"


def fmt_pct(v):
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}%"


def build_rows(positions):
    rows = ""
    for r in sorted(positions, key=lambda x: x["pnl"]):
        v = verdict(r)
        vc = verdict_color(v)
        bg = verdict_bg(v)
        pnl_c = "#10b981" if r["pnl"] >= 0 else "#ef4444"
        type_badge = (
            '<span style="background:rgba(59,130,246,.15);color:#60a5fa;padding:2px 7px;border-radius:5px;font-size:.72rem;font-weight:600">CALL ▲</span>'
            if r["type"] == "Call" else
            '<span style="background:rgba(239,68,68,.15);color:#f87171;padding:2px 7px;border-radius:5px;font-size:.72rem;font-weight:600">PUT ▼</span>'
        )
        rows += f"""<tr style="background:{bg}">
          <td><span style="color:{vc};font-weight:700;font-size:.78rem">● {v}</span></td>
          <td style="font-weight:800;color:#f9fafb">{r['sym']}</td>
          <td>{type_badge}</td>
          <td style="color:#9ca3af;font-size:.78rem">{r['key'].split(' ',2)[2]}</td>
          <td style="color:#9ca3af">{r['buy_date']}</td>
          <td style="color:#9ca3af">{r['hold']}d</td>
          <td>${r['buy_price']:.2f}</td>
          <td>${r['sell_price']:.2f}</td>
          <td style="color:{pnl_c};font-weight:700">{fmt_dollar(r['pnl'])}</td>
          <td style="color:{pnl_c};font-weight:700">{fmt_pct(r['pnl_pct'])}</td>
          <td style="color:#6b7280">${r['spent']:,.0f}</td>
        </tr>"""
    return rows


def build_html(positions):
    winners = [r for r in positions if r["pnl"] > 0 and r["closed"]]
    losers  = [r for r in positions if r["pnl"] < 0 and r["closed"]]
    total_pnl = sum(r["pnl"] for r in positions if r["closed"])
    win_rate = len(winners) / (len(winners) + len(losers)) * 100 if (winners or losers) else 0
    avg_win = sum(r["pnl"] for r in winners) / len(winners) if winners else 0
    avg_loss = sum(r["pnl"] for r in losers) / len(losers) if losers else 0
    avg_hold_w = sum(r["hold"] for r in winners) / len(winners) if winners else 0
    avg_hold_l = sum(r["hold"] for r in losers) / len(losers) if losers else 0
    biggest_loss = min(positions, key=lambda x: x["pnl"])
    biggest_win  = max(positions, key=lambda x: x["pnl"])
    call_pnl = sum(r["pnl"] for r in positions if r["type"] == "Call" and r["closed"])
    put_pnl  = sum(r["pnl"] for r in positions if r["type"] == "Put" and r["closed"])

    # What went wrong analysis
    problems = []
    if avg_loss < -avg_win * 2:
        problems.append(("Losses dwarf wins", f"Your average win is {fmt_dollar(avg_win)} but your average loss is {fmt_dollar(avg_loss)}. A few big losses are erasing many small wins."))
    if biggest_loss["pnl"] < -50000:
        problems.append(("One catastrophic trade", f"{biggest_loss['sym']} cost you {fmt_dollar(biggest_loss['pnl'])} — a single trade wiped out months of profits. No position should ever be this large."))
    oversized = [r for r in losers if r["spent"] > 50000]
    if oversized:
        problems.append(("Oversizing on losers", f"{len(oversized)} losing trades had more than $50k at risk. Large size on bad trades is the #1 account killer."))
    if put_pnl < -20000:
        problems.append(("Puts are costing you", f"Your puts are down {fmt_dollar(put_pnl)} total. Buying puts as a directional bet is expensive — they decay fast."))

    # What went right
    strengths = []
    if win_rate >= 70:
        strengths.append(("High win rate", f"{win_rate:.0f}% of your trades are winners — you have good trade selection instincts."))
    if avg_hold_w <= 5:
        strengths.append(("Quick profit-taking", f"You hold winning trades an average of {avg_hold_w:.1f} days — you take profits before they reverse."))
    quick_wins = [r for r in winners if r["hold"] <= 2 and r["pnl"] > 5000]
    if quick_wins:
        strengths.append(("Fast big wins", f"{len(quick_wins)} trades returned over $5k in 2 days or less. You can identify explosive setups."))
    if biggest_win["pnl"] > 20000:
        strengths.append(("Strong best trades", f"Your best trade ({biggest_win['sym']}) returned {fmt_dollar(biggest_win['pnl'])}. You know how to ride a winner."))

    def stat(val, label, color="#3b82f6"):
        return f"""<div class="stat-card">
          <div style="font-size:1.7rem;font-weight:800;color:{color};line-height:1">{val}</div>
          <div style="font-size:.75rem;font-weight:600;color:#e5e7eb;margin-top:4px">{label}</div>
        </div>"""

    def insight(title, body, color):
        return f"""<div style="background:rgba(0,0,0,.3);border-left:3px solid {color};border-radius:0 8px 8px 0;padding:14px 16px;margin-bottom:10px">
          <div style="font-weight:700;color:{color};font-size:.85rem;margin-bottom:4px">{title}</div>
          <div style="color:#9ca3af;font-size:.8rem;line-height:1.5">{body}</div>
        </div>"""

    problems_html = "".join(insight(t, b, "#ef4444") for t, b in problems) or "<p style='color:#6b7280'>No major issues found.</p>"
    strengths_html = "".join(insight(t, b, "#10b981") for t, b in strengths) or "<p style='color:#6b7280'>Keep building your edge.</p>"

    total_color = "#10b981" if total_pnl >= 0 else "#ef4444"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Robinhood Trade Analysis</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0a0a0f;color:#e5e7eb;font-family:'Inter',system-ui,sans-serif;font-size:14px;line-height:1.5}}
.topbar{{background:linear-gradient(135deg,#0f172a 0%,#1e1b4b 50%,#0f172a 100%);
  border-bottom:1px solid rgba(139,92,246,.2);padding:20px 32px}}
.page{{max-width:1300px;margin:0 auto;padding:24px 32px}}
.section{{margin-bottom:36px}}
.section-title{{font-size:.7rem;font-weight:700;color:#6b7280;text-transform:uppercase;
  letter-spacing:.12em;margin-bottom:16px;display:flex;align-items:center;gap:8px}}
.section-title::after{{content:'';flex:1;height:1px;background:#1f2937}}
.stat-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}}
.stat-card{{background:#111827;border:1px solid #1f2937;border-radius:12px;padding:16px}}
.two-col{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
.card{{background:#111827;border:1px solid #1f2937;border-radius:12px;padding:20px}}
.tbl-wrap{{background:#111827;border:1px solid #1f2937;border-radius:12px;overflow:hidden}}
.tbl-scroll{{overflow-x:auto;max-height:600px;overflow-y:auto}}
table{{width:100%;border-collapse:collapse;white-space:nowrap}}
thead th{{background:#0f172a;color:#6b7280;font-size:.65rem;font-weight:600;text-transform:uppercase;
  letter-spacing:.08em;padding:10px 14px;border-bottom:1px solid #1f2937;position:sticky;top:0}}
tbody td{{padding:9px 14px;border-bottom:1px solid #111;font-size:.8rem;color:#d1d5db;vertical-align:middle}}
tbody tr:last-child td{{border-bottom:none}}
</style>
</head>
<body>
<div class="topbar">
  <div style="font-size:1.25rem;font-weight:800;color:#f9fafb">Robinhood <span style="color:#8b5cf6">Trade Analysis</span></div>
  <div style="font-size:.78rem;color:#6b7280;margin-top:3px">{len(positions)} positions parsed from consolidated statements</div>
</div>
<div class="page">

  <div class="section">
    <div class="section-title">Overview</div>
    <div class="stat-grid">
      {stat(fmt_dollar(total_pnl), "Total Realized P&L", total_color)}
      {stat(f"{win_rate:.0f}%", "Win Rate", "#10b981" if win_rate >= 60 else "#ef4444")}
      {stat(f"{len(winners)}/{len(winners)+len(losers)}", "W / L", "#8b5cf6")}
      {stat(fmt_dollar(avg_win), "Avg Win", "#10b981")}
      {stat(fmt_dollar(avg_loss), "Avg Loss", "#ef4444")}
      {stat(f"{avg_hold_w:.0f}d / {avg_hold_l:.0f}d", "Hold: Win / Loss", "#6b7280")}
      {stat(fmt_dollar(call_pnl), "Calls P&L", "#3b82f6")}
      {stat(fmt_dollar(put_pnl), "Puts P&L", "#8b5cf6")}
    </div>
  </div>

  <div class="section">
    <div class="section-title">What You Did Wrong vs Right</div>
    <div class="two-col">
      <div class="card">
        <div style="font-size:.85rem;font-weight:700;color:#ef4444;margin-bottom:14px">✗ What Went Wrong</div>
        {problems_html}
      </div>
      <div class="card">
        <div style="font-size:.85rem;font-weight:700;color:#10b981;margin-bottom:14px">✓ What You Did Right</div>
        {strengths_html}
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Biggest Win &amp; Loss</div>
    <div class="two-col">
      <div class="card" style="border-color:rgba(16,185,129,.3)">
        <div style="color:#6b7280;font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.08em">Best Trade</div>
        <div style="font-size:1.5rem;font-weight:800;color:#f9fafb;margin:6px 0">{biggest_win['sym']}</div>
        <div style="color:#9ca3af;font-size:.8rem">{biggest_win['key'].split(' ',2)[2]}</div>
        <div style="font-size:1.8rem;font-weight:800;color:#10b981;margin-top:10px">{fmt_dollar(biggest_win['pnl'])}</div>
        <div style="color:#6b7280;font-size:.75rem">Held {biggest_win['hold']} days · Entry ${biggest_win['buy_price']:.2f} → Exit ${biggest_win['sell_price']:.2f}</div>
      </div>
      <div class="card" style="border-color:rgba(239,68,68,.3)">
        <div style="color:#6b7280;font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.08em">Worst Trade</div>
        <div style="font-size:1.5rem;font-weight:800;color:#f9fafb;margin:6px 0">{biggest_loss['sym']}</div>
        <div style="color:#9ca3af;font-size:.8rem">{biggest_loss['key'].split(' ',2)[2]}</div>
        <div style="font-size:1.8rem;font-weight:800;color:#ef4444;margin-top:10px">{fmt_dollar(biggest_loss['pnl'])}</div>
        <div style="color:#6b7280;font-size:.75rem">Held {biggest_loss['hold']} days · Entry ${biggest_loss['buy_price']:.2f} → Exit ${biggest_loss['sell_price']:.2f}</div>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">All Trades (worst to best)</div>
    <div class="tbl-wrap">
      <div class="tbl-scroll">
        <table>
          <thead><tr>
            <th>Verdict</th><th>Ticker</th><th>Type</th><th>Strike · Expiry</th>
            <th>Date</th><th>Hold</th><th>Entry</th><th>Exit</th>
            <th>P&L $</th><th>P&L %</th><th>At Risk</th>
          </tr></thead>
          <tbody>{build_rows(positions)}</tbody>
        </table>
      </div>
    </div>
  </div>

</div>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", default=PDF_DEFAULT)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    print(f"Reading {args.pdf}...")
    trades = parse_pdf(args.pdf)
    print(f"  Parsed {len(trades)} trade lines")

    positions = build_positions(trades)
    closed = [p for p in positions if p["closed"]]
    winners = [p for p in closed if p["pnl"] > 0]
    losers  = [p for p in closed if p["pnl"] < 0]
    total_pnl = sum(p["pnl"] for p in closed)

    print(f"  {len(positions)} positions  |  {len(winners)} winners  |  {len(losers)} losers")
    print(f"  Total P&L: {fmt_dollar(total_pnl)}")

    html = build_html(positions)
    out = os.path.join(RESULTS_DIR, "robinhood_analysis.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n  Report → {out}")

    if not args.no_open:
        webbrowser.open(f"file:///{out.replace(os.sep, '/')}")


if __name__ == "__main__":
    main()
