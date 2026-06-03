@echo off
cd /d "C:\Users\Arya\Desktop\options-bot"
call venv\Scripts\activate.bat
echo [%date% %time%] Starting daily scan... >> results\scan.log
python daily_scan.py >> results\scan.log 2>&1
echo [%date% %time%] Scan complete. >> results\scan.log
