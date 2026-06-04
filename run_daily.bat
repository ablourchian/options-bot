@echo off
cd /d "C:\Users\Arya\Desktop\options-bot"
call venv\Scripts\activate.bat

echo [%date% %time%] Sending morning newsletter... >> results\scan.log
python newsletter.py --html >> results\scan.log 2>&1

echo [%date% %time%] Starting morning scan... >> results\scan.log
python day_trade.py --index all --top-stocks 50 --top 20 >> results\scan.log 2>&1
echo [%date% %time%] Morning scan done. Placing paper trades... >> results\scan.log

python -c "
import sys, os, csv, json
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.abspath('.')))
os.chdir(r'C:\Users\Arya\Desktop\options-bot')
from executor import execute_trades
import csv
rows = []
with open(f'results/daytrade_{date.today()}.csv', newline='') as f:
    for r in csv.DictReader(f):
        for field in ['score','mid','bid','ask','strike','dte','spot']:
            try: r[field] = float(r[field]) if r.get(field) not in ('','None',None) else None
            except: pass
        r['contract'] = {'symbol':r.get('c_symbol',''),'strike':r.get('c_strike',''),
            'dte':r.get('c_dte',''),'expiry':r.get('c_expiry',''),
            'mid':float(r['c_mid']) if r.get('c_mid') else 0,
            'bid':float(r['c_bid']) if r.get('c_bid') else 0,
            'ask':float(r['c_ask']) if r.get('c_ask') else 0} if r.get('c_symbol') else {}
        rows.append(r)
ranked = sorted(rows, key=lambda r: r.get('score') or 0, reverse=True)
execute_trades(ranked, dry_run=False, paper=True, min_grade='A', max_trades=5, max_spend=1000, use_signal_filter=False)
" >> results\scan.log 2>&1

echo [%date% %time%] Starting position monitor and live scanner... >> results\scan.log
start "PositionMonitor" /min venv\Scripts\python.exe position_monitor.py --tp 15 --sl 7.5
python live_scan.py --open-browser >> results\scan.log 2>&1
echo [%date% %time%] Done. >> results\scan.log
