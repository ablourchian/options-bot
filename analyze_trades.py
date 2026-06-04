import pdfplumber, re
from collections import defaultdict

path = r'C:\Users\Arya\Downloads\Consolidated_Robinhood_Statements (1).pdf'
all_text = []
with pdfplumber.open(path) as pdf:
    for page in pdf.pages:
        t = page.extract_text()
        if t:
            all_text.append(t)

full = '\n'.join(all_text)
lines = full.split('\n')

# ── Parse trades ──────────────────────────────────────────────────────────────
trades = []
for line in lines:
    if any(x in line for x in ['BTO','STC','STO','BTC']):
        m = re.search(
            r'(\w+)\s+Cash\s+(BTO|STC|STO|BTC)\s+(\d{2}/\d{2}/\d{4})\s+([\d.]+)\s+\$([\d,]+\.?\d*)\s+\$([\d,]+\.?\d*)',
            line
        )
        if m:
            sym, action, date, qty, price, total = m.groups()
            trades.append({
                'sym': sym, 'action': action, 'date': date,
                'qty': float(qty),
                'price': float(price.replace(',','')),
                'total': float(total.replace(',',''))
            })

print(f'Parsed {len(trades)} trades\n')

# ── P&L per symbol ────────────────────────────────────────────────────────────
spent  = defaultdict(float)
earned = defaultdict(float)
counts = defaultdict(int)

for t in trades:
    if t['action'] in ('BTO','STO'):
        spent[t['sym']] += t['total']
    else:
        earned[t['sym']] += t['total']
    counts[t['sym']] += 1

all_syms = sorted(set(list(spent.keys()) + list(earned.keys())))
total_spent = total_earned = 0
results = []

for sym in all_syms:
    pnl = earned[sym] - spent[sym]
    results.append((sym, spent[sym], earned[sym], pnl, counts[sym]))
    total_spent  += spent[sym]
    total_earned += earned[sym]

results.sort(key=lambda x: x[3])  # worst first

print(f"{'Symbol':<8} {'Spent':>12} {'Earned':>12} {'P&L':>12} {'Trades':>7}")
print('-' * 57)
for sym, sp, ea, pnl, cnt in results:
    flag = ' <-- LOSS' if pnl < -500 else (' <-- WIN' if pnl > 500 else '')
    print(f"{sym:<8} ${sp:>11,.0f} ${ea:>11,.0f} ${pnl:>+11,.0f} {cnt:>6}{flag}")

print('-' * 57)
total_pnl = total_earned - total_spent
print(f"{'TOTAL':<8} ${total_spent:>11,.0f} ${total_earned:>11,.0f} ${total_pnl:>+11,.0f}")

# ── Holding period analysis ───────────────────────────────────────────────────
from datetime import datetime
print('\n\n--- HOLDING PERIOD ANALYSIS ---')
buy_dates = {}
hold_days = defaultdict(list)

for t in trades:
    key = t['sym']
    dt = datetime.strptime(t['date'], '%m/%d/%Y')
    if t['action'] in ('BTO','STO'):
        buy_dates[key] = dt
    elif t['action'] in ('STC','BTC') and key in buy_dates:
        days = (dt - buy_dates[key]).days
        hold_days[key].append(days)

print(f"{'Symbol':<8} {'Avg Hold Days':>14} {'Trades':>7}")
print('-' * 35)
all_days = []
for sym, days in sorted(hold_days.items()):
    avg = sum(days)/len(days)
    all_days.extend(days)
    print(f"{sym:<8} {avg:>14.1f} {len(days):>7}")

if all_days:
    print(f"\nOverall avg hold: {sum(all_days)/len(all_days):.1f} days")
    print(f"Same-day trades:  {sum(1 for d in all_days if d==0)}")
    print(f"1-7 day trades:   {sum(1 for d in all_days if 1<=d<=7)}")
    print(f"8+ day trades:    {sum(1 for d in all_days if d>7)}")

# ── Call vs Put breakdown ─────────────────────────────────────────────────────
print('\n\n--- CALL vs PUT BREAKDOWN ---')
call_pnl = put_pnl = 0
call_trades = put_trades = 0

for line in lines:
    if 'BTO' in line or 'STC' in line:
        m = re.search(
            r'(\w+)\s+\d{2}/\d{2}/\d{4}\s+(Call|Put)\s+\$[\d,.]+\s+\w+\s+Cash\s+(BTO|STC)\s+\d{2}/\d{2}/\d{4}\s+[\d.]+\s+\$([\d,.]+)\s+\$([\d,.]+)',
            line
        )
        if m:
            sym, otype, action, price, total = m.groups()
            total_val = float(total.replace(',',''))
            if otype == 'Call':
                call_trades += 1
                call_pnl += total_val if action == 'STC' else -total_val
            else:
                put_trades += 1
                put_pnl += total_val if action == 'STC' else -total_val

print(f"Calls: {call_trades} trades  P&L: ${call_pnl:+,.0f}")
print(f"Puts:  {put_trades} trades  P&L: ${put_pnl:+,.0f}")

# ── Account balance timeline ──────────────────────────────────────────────────
print('\n\n--- ACCOUNT BALANCE TIMELINE (from statements) ---')
months_seen = set()
for i, line in enumerate(lines):
    if 'Net Account Balance' in line and i+1 < len(lines):
        next_line = lines[i+1] if i+1 < len(lines) else ''
        amounts = re.findall(r'\$([\d,]+\.\d{2})', line + ' ' + next_line)
        if amounts and len(amounts) >= 2:
            period_line = lines[max(0,i-15):i]
            period = ''
            for pl in period_line:
                m2 = re.search(r'(\d{2}/\d{2}/\d{4}) to (\d{2}/\d{2}/\d{4})', pl)
                if m2:
                    period = m2.group(1) + ' - ' + m2.group(2)
                    break
            if period and period not in months_seen:
                months_seen.add(period)
                print(f"{period:<30} Open: ${amounts[0]:>12}  Close: ${amounts[1]:>12}")
