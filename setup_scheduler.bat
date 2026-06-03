@echo off
:: Run this file as Administrator to register the daily scan task.
:: Right-click -> "Run as administrator"

set TASK_NAME=OptionsBot_DailyScan
set BOT_DIR=C:\Users\Arya\Desktop\options-bot
set BAT_FILE=%BOT_DIR%\run_daily.bat

:: Delete existing task if present
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

:: Register new task: Mon-Fri at 8:30 AM, run as current user
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "\"%BAT_FILE%\"" ^
  /sc WEEKLY ^
  /d MON,TUE,WED,THU,FRI ^
  /st 08:30 ^
  /rl HIGHEST ^
  /f

if %errorlevel% == 0 (
    echo.
    echo  Task registered: %TASK_NAME%
    echo  Schedule: Mon-Fri at 8:30 AM
    echo  Log: %BOT_DIR%\results\scan.log
    echo.
    echo  To run immediately:
    echo    schtasks /run /tn "%TASK_NAME%"
    echo.
    echo  To view in Task Scheduler:
    echo    taskschd.msc
) else (
    echo.
    echo  ERROR: Could not register task.
    echo  Make sure you right-clicked and chose "Run as administrator".
)

pause
