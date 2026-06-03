# schedule_task.ps1
# Registers a Windows Task Scheduler job to run the daily options scan
# every weekday at 8:30 AM (one hour before market open).
#
# Run once as Administrator:
#   Right-click PowerShell → "Run as administrator"
#   cd "C:\Users\Arya\Desktop\options-bot"
#   .\schedule_task.ps1

$TaskName     = "OptionsBot_DailyScan"
$BotDir       = "C:\Users\Arya\Desktop\options-bot"
$PythonExe    = "$BotDir\venv\Scripts\python.exe"
$Script       = "$BotDir\daily_scan.py"
$LogFile      = "$BotDir\results\scan.log"

# Remove existing task if present
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed existing task: $TaskName"
}

# Action: run python daily_scan.py, redirect output to log
$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$Script`" >> `"$LogFile`" 2>&1" `
    -WorkingDirectory $BotDir

# Trigger: Mon–Fri at 8:30 AM
$Trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
    -At "8:30AM"

# Settings: run even if on battery, stop after 2 hours
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -DisallowDemandStart:$false

# Principal: run as current user
$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Daily options scanner — runs daily_scan.py at 8:30 AM on weekdays"

Write-Host ""
Write-Host "Task registered: $TaskName"
Write-Host "Schedule: Mon-Fri at 8:30 AM"
Write-Host "Log: $LogFile"
Write-Host ""
Write-Host "To run immediately:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host ""
Write-Host "To remove:"
Write-Host "  Unregister-ScheduledTask -TaskName $TaskName -Confirm:false"
