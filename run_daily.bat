@echo off
cd /d "C:\Users\Arya\Desktop\options-bot"
call venv\Scripts\activate.bat

echo [%date% %time%] Sending morning newsletter... >> results\scan.log
python newsletter.py --html >> results\scan.log 2>&1

echo [%date% %time%] Starting morning scan... >> results\scan.log
python day_trade.py --index all --top-stocks 50 --top 20 >> results\scan.log 2>&1
echo [%date% %time%] Morning scan complete. Starting live scanner... >> results\scan.log

python live_scan.py --open-browser >> results\scan.log 2>&1
echo [%date% %time%] Live scanner stopped. >> results\scan.log
