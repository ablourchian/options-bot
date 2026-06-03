"""
Dashboard generator — reads the latest scan results and opens a polished HTML dashboard.

Usage:
    python dashboard.py                   # latest scan
    python dashboard.py --date 2025-06-03
    python dashboard.py --no-open
"""
import os
import csv
import argparse
import webbrowser
from datetime import date
from collections import defaultdict

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


def load_results(scan_date: str) -> list[dict]:
    path = os.path.join(RESULTS_DIR, f"{scan_date}.csv")
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            for field in ["score","iv","iv_rank","iv_hv_ratio","delta","gamma",
                          "theta","vega","mid","bid","ask","strike","dte","spot",
                          "_s_iv_rank","_s_iv_hv","_s_spread","_s_theta","_s_delta"]:
                try:
                    r[field] = float(r[field]) if r.get(field) not in ("","None",None) else None
                except (ValueError, KeyError):
                    r[field] = None
            rows.append(r)
    return rows


def load_tracker_history() -> list[dict]:
    path = os.path.join(RESULTS_DIR, "tracker.csv")
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            for field in ["score","iv","iv_rank","iv_hv_ratio","delta","theta"]:
                try:
                    r[field] = float(r[field]) if r.get(field) not in ("","None",None) else None
                except (ValueError, KeyError):
                    r[field] = None
            rows.append(r)
    return rows


def f(val, spec=".2f", fb="—"):
    if val is None: return fb
    try: return format(float(val), spec)
    except: return fb


def grade_pill(grade):
    colors = {"A+":"#10b981","A":"#34d399","B":"#f59e0b","C":"#f97316","D":"#ef4444"}
    c = colors.get(grade, "#6b7280")
    return f'<span style="background:{c};color:#fff;padding:2px 10px;border-radius:20px;font-size:.75rem;font-weight:700;letter-spacing:.05em">{grade}</span>'


def type_badge(t):
    if t == "call":
        return '<span style="background:rgba(59,130,246,.15);color:#60a5fa;padding:2px 8px;border-radius:6px;font-size:.75rem;font-weight:600">CALL ▲</span>'
    return '<span style="background:rgba(239,68,68,.15);color:#f87171;padding:2px 8px;border-radius:6px;font-size:.75rem;font-weight:600">PUT ▼</span>'


def score_ring(score, grade):
    colors = {"A+":"#10b981","A":"#34d399","B":"#f59e0b","C":"#f97316","D":"#ef4444"}
    c = colors.get(grade, "#6b7280")
    pct = min(score, 100)
    # SVG circle: circumference = 2π×18 ≈ 113
    circ = 113
    dash = circ * pct / 100
    return f'''<svg width="56" height="56" viewBox="0 0 40 40">
      <circle cx="20" cy="20" r="18" fill="none" stroke="#1f2937" stroke-width="3.5"/>
      <circle cx="20" cy="20" r="18" fill="none" stroke="{c}" stroke-width="3.5"
        stroke-dasharray="{dash:.1f} {circ:.1f}" stroke-dashoffset="28.3"
        stroke-linecap="round"/>
      <text x="20" y="24" text-anchor="middle" fill="{c}" font-size="9" font-weight="700">{pct:.0f}</text>
    </svg>'''


def mini_bar(val, max_val, color="#3b82f6"):
    pct = min((val or 0) / max_val * 100, 100)
    bg = "#1f2937"
    return f'''<div style="display:flex;align-items:center;gap:6px">
      <div style="flex:1;height:4px;background:{bg};border-radius:2px;overflow:hidden">
        <div style="width:{pct:.0f}%;height:100%;background:{color};border-radius:2px"></div>
      </div>
      <span style="font-size:.7rem;color:#9ca3af;width:22px;text-align:right">{val or 0:.0f}</span>
    </div>'''


def build_stat_cards(rows, scan_date):
    total = len(rows)
    a_plus = sum(1 for r in rows if r.get("grade") == "A+")
    a_gr   = sum(1 for r in rows if r.get("grade") == "A")
    tickers = len(set(r["underlying"] for r in rows))
    avg_score = sum(r.get("score") or 0 for r in rows) / total if total else 0
    avg_ivr = sum(r.get("iv_rank") or 0 for r in rows if r.get("iv_rank")) / max(1, sum(1 for r in rows if r.get("iv_rank")))
    calls = sum(1 for r in rows if r.get("type") == "call")
    puts  = sum(1 for r in rows if r.get("type") == "put")

    def card(val, label, sub="", color="#3b82f6"):
        return f'''<div class="stat-card">
          <div style="font-size:2rem;font-weight:800;color:{color};line-height:1">{val}</div>
          <div style="font-size:.8rem;font-weight:600;color:#e5e7eb;margin-top:4px">{label}</div>
          {f'<div style="font-size:.7rem;color:#6b7280;margin-top:2px">{sub}</div>' if sub else ''}
        </div>'''

    return f'''
      {card(a_plus, "A+ Setups", "highest conviction", "#10b981")}
      {card(a_gr,   "A Setups",  "strong signals",     "#34d399")}
      {card(f"{avg_score:.0f}", "Avg Score", "out of 100", "#3b82f6")}
      {card(f"{avg_ivr:.0f}%",  "Avg IV Rank", "vs 1yr history", "#8b5cf6")}
      {card(tickers, "Tickers", f"{calls}C / {puts}P", "#f59e0b")}
      {card(total,   "Contracts", "scanned today", "#6b7280")}
    '''


def build_top_cards(rows, n=6, signals=None):
    top = sorted(rows, key=lambda r: r.get("score") or 0, reverse=True)[:n]
    cards = ""
    for r in top:
        grade = r.get("grade","D")
        score = r.get("score") or 0
        spread_pct = ((r["ask"]-r["bid"])/r["mid"]*100) if (r.get("mid") and r["mid"] > 0) else None

        comp_bars = f'''
          <div style="margin-top:12px;display:flex;flex-direction:column;gap:5px">
            <div style="display:flex;justify-content:space-between;font-size:.68rem;color:#6b7280;margin-bottom:2px">
              <span>Score components</span><span style="color:#9ca3af">/pts</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px;font-size:.7rem">
              <span style="width:52px;color:#9ca3af">IV Rank</span>
              {mini_bar(r.get("_s_iv_rank"), 25, "#8b5cf6")}
            </div>
            <div style="display:flex;align-items:center;gap:6px;font-size:.7rem">
              <span style="width:52px;color:#9ca3af">IV/HV</span>
              {mini_bar(r.get("_s_iv_hv"), 20, "#3b82f6")}
            </div>
            <div style="display:flex;align-items:center;gap:6px;font-size:.7rem">
              <span style="width:52px;color:#9ca3af">Spread</span>
              {mini_bar(r.get("_s_spread"), 20, "#10b981")}
            </div>
            <div style="display:flex;align-items:center;gap:6px;font-size:.7rem">
              <span style="width:52px;color:#9ca3af">Theta</span>
              {mini_bar(r.get("_s_theta"), 20, "#f59e0b")}
            </div>
            <div style="display:flex;align-items:center;gap:6px;font-size:.7rem">
              <span style="width:52px;color:#9ca3af">Delta</span>
              {mini_bar(r.get("_s_delta"), 15, "#ef4444")}
            </div>
          </div>'''

        cards += f'''<div class="trade-card">
          <div style="display:flex;justify-content:space-between;align-items:flex-start">
            <div>
              <div style="font-size:1.3rem;font-weight:800;color:#f9fafb;letter-spacing:-.01em">{r.get("underlying")}</div>
              <div style="margin-top:4px;display:flex;gap:6px;align-items:center">
                {type_badge(r.get("type","call"))}
                <span style="font-size:.75rem;color:#9ca3af">${f(r.get("strike"))} strike · {f(r.get("dte"),".0f")}d DTE</span>
              </div>
            </div>
            <div style="text-align:center">
              {score_ring(score, grade)}
              <div style="margin-top:2px">{grade_pill(grade)}</div>
            </div>
          </div>

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px">
            <div class="metric"><span class="metric-lbl">Mid Price</span><span class="metric-val">${f(r.get("mid"))}</span></div>
            <div class="metric"><span class="metric-lbl">Spot</span><span class="metric-val">${f(r.get("spot"))}</span></div>
            <div class="metric"><span class="metric-lbl">IV%</span><span class="metric-val">{f(r.get("iv"),".1f")}%</span></div>
            <div class="metric"><span class="metric-lbl">IV Rank</span><span class="metric-val" style="color:#8b5cf6">{f(r.get("iv_rank"),".1f")}%</span></div>
            <div class="metric"><span class="metric-lbl">Delta</span><span class="metric-val">{f(r.get("delta"),".3f")}</span></div>
            <div class="metric"><span class="metric-lbl">Theta/day</span><span class="metric-val" style="color:#f59e0b">{f(r.get("theta"),".3f")}</span></div>
            <div class="metric"><span class="metric-lbl">IV/HV</span><span class="metric-val">{f(r.get("iv_hv_ratio"),".2f")}</span></div>
            <div class="metric"><span class="metric-lbl">Spread</span><span class="metric-val" style="color:{'#10b981' if spread_pct and spread_pct<5 else '#f59e0b' if spread_pct and spread_pct<15 else '#ef4444'}">{f(spread_pct,".1f")}%</span></div>
          </div>
          {comp_bars}
          {signal_badge(signals.get(r.get("underlying","")) if signals else None)}
        </div>'''
    return cards


def build_table(rows, top_n=50):
    rows = sorted(rows, key=lambda r: r.get("score") or 0, reverse=True)[:top_n]
    trs = ""
    for r in rows:
        spread_pct = ((r["ask"]-r["bid"])/r["mid"]*100) if (r.get("mid") and r["mid"] > 0) else None
        sc = f(r.get("score"),".1f")
        ivr = f(r.get("iv_rank"),".1f")
        ivhv = f(r.get("iv_hv_ratio"),".2f")
        sp_color = "#10b981" if spread_pct and spread_pct < 5 else "#f59e0b" if spread_pct and spread_pct < 15 else "#ef4444"
        trs += f'''<tr>
          <td>{grade_pill(r.get("grade","D"))}</td>
          <td><span style="font-size:.85rem;color:#9ca3af">{sc}</span></td>
          <td style="font-weight:700;color:#f9fafb">{r.get("underlying")}</td>
          <td>{type_badge(r.get("type","call"))}</td>
          <td>${f(r.get("strike"))}</td>
          <td style="color:#9ca3af">{f(r.get("dte"),".0f")}d</td>
          <td>${f(r.get("spot"))}</td>
          <td style="font-weight:600">${f(r.get("mid"))}</td>
          <td>{f(r.get("iv"),".1f")}%</td>
          <td style="color:#8b5cf6;font-weight:600">{ivr}%</td>
          <td>{ivhv}</td>
          <td>{f(r.get("delta"),".3f")}</td>
          <td style="color:#f59e0b">{f(r.get("theta"),".4f")}</td>
          <td style="color:{sp_color}">{f(spread_pct,".1f")}%</td>
        </tr>'''
    return trs


def build_charts_js(rows, history):
    grades = {"A+":0,"A":0,"B":0,"C":0,"D":0}
    for r in rows:
        g = r.get("grade","D")
        if g in grades: grades[g] += 1

    by_sym = defaultdict(list)
    for r in rows:
        if r.get("score"): by_sym[r["underlying"]].append(r["score"])
    sym_scores = sorted([(s, sum(v)/len(v)) for s,v in by_sym.items()], key=lambda x:x[1], reverse=True)[:12]

    iv_buckets = [0]*10
    for r in rows:
        v = r.get("iv_rank")
        if v is not None: iv_buckets[min(int(v/10),9)] += 1

    by_date = defaultdict(list)
    for r in history:
        if r.get("score") and r.get("date"): by_date[r["date"]].append(r["score"])
    trend_dates = sorted(by_date.keys())[-14:]
    trend_scores = [round(sum(by_date[d])/len(by_date[d]),1) for d in trend_dates]

    return f"""
const gradeData = {{labels:{list(grades.keys())},data:{list(grades.values())},colors:['#10b981','#34d399','#f59e0b','#f97316','#ef4444']}};
const symData   = {{labels:{[s[0] for s in sym_scores]},data:{[round(s[1],1) for s in sym_scores]}}};
const ivData    = {{labels:['0-10','10-20','20-30','30-40','40-50','50-60','60-70','70-80','80-90','90+'],data:{iv_buckets}}};
const trendData = {{labels:{trend_dates},data:{trend_scores}}};
"""


def signal_badge(sig: dict) -> str:
    if not sig:
        return ""
    direction  = sig.get("direction", "neutral")
    confidence = sig.get("confidence", "weak")
    score      = sig.get("signal", 0)
    ind        = sig.get("indicators", {})

    if direction == "oversold":
        color = "#10b981" if confidence == "strong" else "#34d399"
        icon  = "▲▲" if confidence == "strong" else "▲"
        label = "OVERSOLD"
    elif direction == "overbought":
        color = "#ef4444" if confidence == "strong" else "#f87171"
        icon  = "▼▼" if confidence == "strong" else "▼"
        label = "OVERBOUGHT"
    else:
        color = "#6b7280"
        icon  = "—"
        label = "NEUTRAL"

    rsi_v = f"RSI {ind['rsi']:.0f}" if ind.get("rsi") is not None else ""
    stoch_v = f"Stoch {ind['stoch_k']:.0f}" if ind.get("stoch_k") is not None else ""
    vwap_v = f"VWAP {ind['pct_from_vwap']:+.1f}%" if ind.get("pct_from_vwap") is not None else ""
    tags = "  ".join(x for x in [rsi_v, stoch_v, vwap_v] if x)

    return f'''<div style="background:rgba({
        '16,185,129' if direction=='oversold' else '239,68,68' if direction=='overbought' else '107,114,128'
    },.1);border:1px solid {color};border-radius:8px;padding:8px 10px;margin-top:10px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span style="color:{color};font-weight:700;font-size:.8rem">{icon} {label}</span>
        <span style="color:#6b7280;font-size:.7rem">score {score:+d}/5</span>
      </div>
      <div style="color:#9ca3af;font-size:.68rem;margin-top:3px">{tags}</div>
    </div>'''


def fetch_intraday_signals(underlyings: list) -> dict:
    try:
        from intraday import get_signals_bulk
        return get_signals_bulk(list(set(underlyings)), timeframe="5Min", n_bars=78)
    except Exception:
        return {}


def generate_html(rows, history, scan_date, include_signals=False):
    calls = sorted([r for r in rows if r.get("type")=="call"], key=lambda r: r.get("score") or 0, reverse=True)
    puts  = sorted([r for r in rows if r.get("type")=="put"],  key=lambda r: r.get("score") or 0, reverse=True)
    all_s = sorted(rows, key=lambda r: r.get("score") or 0, reverse=True)

    signals = {}
    if include_signals:
        signals = fetch_intraday_signals([r["underlying"] for r in all_s[:20]])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Options Bot · {scan_date}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0a0a0f;color:#e5e7eb;font-family:'Inter',system-ui,sans-serif;font-size:14px;line-height:1.5}}
::-webkit-scrollbar{{width:6px;height:6px}}
::-webkit-scrollbar-track{{background:#111}}
::-webkit-scrollbar-thumb{{background:#374151;border-radius:3px}}

/* Layout */
.topbar{{background:linear-gradient(135deg,#0f172a 0%,#1e1b4b 50%,#0f172a 100%);
  border-bottom:1px solid rgba(139,92,246,.2);padding:20px 32px;
  display:flex;justify-content:space-between;align-items:center}}
.topbar-title{{font-size:1.25rem;font-weight:800;color:#f9fafb;letter-spacing:-.02em}}
.topbar-title span{{color:#8b5cf6}}
.topbar-meta{{font-size:.8rem;color:#6b7280}}
.topbar-meta strong{{color:#9ca3af}}

.page{{max-width:1400px;margin:0 auto;padding:24px 32px}}
.section{{margin-bottom:36px}}
.section-title{{font-size:.7rem;font-weight:700;color:#6b7280;text-transform:uppercase;
  letter-spacing:.12em;margin-bottom:16px;display:flex;align-items:center;gap:8px}}
.section-title::after{{content:'';flex:1;height:1px;background:#1f2937}}

/* Stat cards */
.stat-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px}}
.stat-card{{background:#111827;border:1px solid #1f2937;border-radius:12px;
  padding:16px;transition:border-color .2s}}
.stat-card:hover{{border-color:#374151}}

/* Trade cards */
.cards-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}}
.trade-card{{background:#111827;border:1px solid #1f2937;border-radius:14px;
  padding:18px;transition:all .2s;cursor:default}}
.trade-card:hover{{border-color:#374151;background:#131d2e;transform:translateY(-1px)}}
.metric{{background:#0f172a;border-radius:8px;padding:8px 10px;display:flex;flex-direction:column;gap:2px}}
.metric-lbl{{font-size:.68rem;color:#6b7280;font-weight:500;text-transform:uppercase;letter-spacing:.04em}}
.metric-val{{font-size:.9rem;font-weight:700;color:#e5e7eb}}

/* Tabs */
.tabs{{display:flex;gap:4px;background:#111827;border:1px solid #1f2937;
  border-radius:10px;padding:4px;width:fit-content;margin-bottom:20px}}
.tab{{padding:7px 18px;border-radius:7px;font-size:.82rem;font-weight:600;
  cursor:pointer;color:#6b7280;transition:all .15s;border:none;background:none}}
.tab.active{{background:#1f2937;color:#f9fafb}}
.tab-pane{{display:none}}.tab-pane.active{{display:block}}

/* Charts */
.charts-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}}
.chart-card{{background:#111827;border:1px solid #1f2937;border-radius:12px;padding:20px}}
.chart-title{{font-size:.72rem;font-weight:600;color:#9ca3af;text-transform:uppercase;
  letter-spacing:.08em;margin-bottom:14px}}
canvas{{max-height:190px!important}}

/* Table */
.tbl-wrap{{background:#111827;border:1px solid #1f2937;border-radius:12px;overflow:hidden}}
.tbl-scroll{{overflow-x:auto;overflow-y:auto;max-height:560px}}
table{{width:100%;border-collapse:collapse;white-space:nowrap}}
thead th{{background:#0f172a;color:#6b7280;font-size:.68rem;font-weight:600;
  text-transform:uppercase;letter-spacing:.08em;padding:10px 14px;
  position:sticky;top:0;z-index:1;border-bottom:1px solid #1f2937}}
tbody td{{padding:9px 14px;border-bottom:1px solid #111;font-size:.82rem;color:#d1d5db}}
tbody tr:last-child td{{border-bottom:none}}
tbody tr:hover td{{background:#131d2e}}

/* Filter bar */
.filter-bar{{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;align-items:center}}
.filter-btn{{padding:5px 14px;border-radius:20px;font-size:.75rem;font-weight:600;
  cursor:pointer;border:1px solid #1f2937;background:#111827;color:#9ca3af;transition:all .15s}}
.filter-btn.active{{background:#1f2937;color:#f9fafb;border-color:#374151}}
.search-box{{padding:6px 12px;border-radius:8px;border:1px solid #1f2937;
  background:#0f172a;color:#e5e7eb;font-size:.8rem;outline:none;width:160px}}
.search-box:focus{{border-color:#374151}}
</style>
</head>
<body>

<div class="topbar">
  <div>
    <div class="topbar-title">Options<span>Bot</span> Dashboard</div>
    <div class="topbar-meta" style="margin-top:3px">Scan date: <strong>{scan_date}</strong> &nbsp;·&nbsp; {len(rows)} contracts across {len(set(r["underlying"] for r in rows))} tickers</div>
  </div>
  <div style="display:flex;gap:8px">
    <button class="filter-btn active" onclick="setMode('all',this)">All</button>
    <button class="filter-btn" onclick="setMode('call',this)">Calls Only</button>
    <button class="filter-btn" onclick="setMode('put',this)">Puts Only</button>
  </div>
</div>

<div class="page">

  <!-- Stats -->
  <div class="section">
    <div class="section-title">Today at a Glance</div>
    <div class="stat-grid">{build_stat_cards(rows, scan_date)}</div>
  </div>

  <!-- Top Setups -->
  <div class="section">
    <div class="section-title">Top Setups</div>
    <div class="tabs">
      <button class="tab active" onclick="switchTab('all-tab',this)">All</button>
      <button class="tab" onclick="switchTab('calls-tab',this)">📈 Calls</button>
      <button class="tab" onclick="switchTab('puts-tab',this)">📉 Puts</button>
    </div>
    <div id="all-tab" class="tab-pane active">
      <div class="cards-grid">{build_top_cards(all_s, 6, signals)}</div>
    </div>
    <div id="calls-tab" class="tab-pane">
      <div class="cards-grid">{build_top_cards(calls, 6, signals)}</div>
    </div>
    <div id="puts-tab" class="tab-pane">
      <div class="cards-grid">{build_top_cards(puts, 6, signals)}</div>
    </div>
  </div>

  <!-- Charts -->
  <div class="section">
    <div class="section-title">Analytics</div>
    <div class="charts-grid">
      <div class="chart-card"><div class="chart-title">Grade Distribution</div><canvas id="cGrade"></canvas></div>
      <div class="chart-card"><div class="chart-title">Top Tickers by Avg Score</div><canvas id="cSym"></canvas></div>
      <div class="chart-card"><div class="chart-title">IV Rank Distribution</div><canvas id="cIV"></canvas></div>
      <div class="chart-card"><div class="chart-title">Historical Avg Score</div><canvas id="cTrend"></canvas></div>
    </div>
  </div>

  <!-- Full Table -->
  <div class="section">
    <div class="section-title">All Ranked Contracts</div>
    <div class="filter-bar">
      <input class="search-box" id="tblSearch" placeholder="Search ticker…" oninput="filterTable()">
      <button class="filter-btn active" id="fb-all"  onclick="filterType('all',this)">All</button>
      <button class="filter-btn" id="fb-call" onclick="filterType('call',this)">Calls</button>
      <button class="filter-btn" id="fb-put"  onclick="filterType('put',this)">Puts</button>
      <button class="filter-btn" id="fb-aplus" onclick="filterGrade('A+',this)">A+ Only</button>
      <button class="filter-btn" id="fb-a"    onclick="filterGrade('A',this)">A+ & A</button>
    </div>
    <div class="tbl-wrap">
      <div class="tbl-scroll">
        <table id="mainTable">
          <thead><tr>
            <th>Grade</th><th>Score</th><th>Ticker</th><th>Type</th>
            <th>Strike</th><th>DTE</th><th>Spot</th><th>Mid</th>
            <th>IV%</th><th>IV Rank</th><th>IV/HV</th>
            <th>Delta</th><th>Theta</th><th>Spread%</th>
          </tr></thead>
          <tbody id="tblBody">{build_table(all_s, 100)}</tbody>
        </table>
      </div>
    </div>
  </div>

</div>

<script>
{build_charts_js(rows, history)}

Chart.defaults.color = '#6b7280';
Chart.defaults.font.family = 'Inter, system-ui';
const grid = {{ color:'#1f2937' }};

new Chart(document.getElementById('cGrade'),{{
  type:'doughnut',
  data:{{labels:gradeData.labels,datasets:[{{data:gradeData.data,backgroundColor:gradeData.colors,borderWidth:0,hoverOffset:4}}]}},
  options:{{plugins:{{legend:{{position:'right',labels:{{boxWidth:10,padding:12}}}}}},cutout:'65%'}}
}});

new Chart(document.getElementById('cSym'),{{
  type:'bar',
  data:{{labels:symData.labels,datasets:[{{data:symData.data,backgroundColor:'#3b82f6',borderRadius:4,borderSkipped:false}}]}},
  options:{{indexAxis:'y',plugins:{{legend:{{display:false}}}},scales:{{x:{{grid,ticks:{{color:'#6b7280'}}}},y:{{grid:{{display:false}},ticks:{{color:'#9ca3af',font:{{size:11}}}}}}}}}}
}});

new Chart(document.getElementById('cIV'),{{
  type:'bar',
  data:{{labels:ivData.labels,datasets:[{{data:ivData.data,backgroundColor:'#8b5cf6',borderRadius:4,borderSkipped:false}}]}},
  options:{{plugins:{{legend:{{display:false}}}},scales:{{x:{{grid:{{display:false}},ticks:{{color:'#6b7280',font:{{size:10}}}}}},y:{{grid,ticks:{{color:'#6b7280'}}}}}}}}
}});

new Chart(document.getElementById('cTrend'),{{
  type:'line',
  data:{{labels:trendData.labels,datasets:[{{data:trendData.data,borderColor:'#10b981',
    backgroundColor:'rgba(16,185,129,.08)',fill:true,tension:.35,
    pointBackgroundColor:'#10b981',pointRadius:3,pointHoverRadius:5}}]}},
  options:{{plugins:{{legend:{{display:false}}}},scales:{{x:{{grid:{{display:false}},ticks:{{color:'#6b7280',font:{{size:10}}}}}},y:{{grid,ticks:{{color:'#6b7280'}}}}}}}}
}});

// Tab switching
function switchTab(id, el) {{
  document.querySelectorAll('.tab-pane').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tabs .tab').forEach(t=>t.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  el.classList.add('active');
}}

// Table filtering
let _typeFilter = 'all';
let _gradeFilter = null;

function filterType(t, el) {{
  _typeFilter = t;
  _gradeFilter = null;
  document.querySelectorAll('[id^=fb-]').forEach(b=>b.classList.remove('active'));
  el.classList.add('active');
  filterTable();
}}

function filterGrade(g, el) {{
  _gradeFilter = g;
  _typeFilter = 'all';
  document.querySelectorAll('[id^=fb-]').forEach(b=>b.classList.remove('active'));
  el.classList.add('active');
  filterTable();
}}

function filterTable() {{
  const q = document.getElementById('tblSearch').value.toUpperCase();
  const rows = document.querySelectorAll('#tblBody tr');
  rows.forEach(tr => {{
    const cells = tr.querySelectorAll('td');
    const ticker = cells[2]?.textContent.toUpperCase() || '';
    const type   = cells[3]?.textContent.toLowerCase() || '';
    const grade  = cells[0]?.textContent.trim() || '';
    let show = ticker.includes(q);
    if (_typeFilter !== 'all' && !type.includes(_typeFilter)) show = false;
    if (_gradeFilter) {{
      if (_gradeFilter === 'A+') show = show && grade === 'A+';
      else show = show && (grade === 'A+' || grade === 'A');
    }}
    tr.style.display = show ? '' : 'none';
  }});
}}

function setMode(m, el) {{
  document.querySelectorAll('.topbar button').forEach(b=>b.classList.remove('active'));
  el.classList.add('active');
  // Also update the tab panel cards visibility
}}
</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=str(date.today()))
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    rows = load_results(args.date)
    if not rows:
        files = sorted(
            [f for f in os.listdir(RESULTS_DIR) if f.endswith(".csv") and f != "tracker.csv"],
            reverse=True
        ) if os.path.exists(RESULTS_DIR) else []
        if files:
            args.date = files[0].replace(".csv","")
            rows = load_results(args.date)
            print(f"  No results for today, showing {args.date}")

    if not rows:
        print("No scan results found. Run daily_scan.py first.")
        return

    history = load_tracker_history()
    html = generate_html(rows, history, args.date)

    out = os.path.join(RESULTS_DIR, f"dashboard_{args.date}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Dashboard → {out}")

    if not args.no_open:
        webbrowser.open(f"file:///{out.replace(os.sep, '/')}")


if __name__ == "__main__":
    main()
