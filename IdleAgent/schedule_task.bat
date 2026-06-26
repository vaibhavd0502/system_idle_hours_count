@echo off
:: ============================================================
::  Registers run_me.bat as a scheduled task
::  Task name : idlecounthours
::  Runs      : every 5 minutes
::  Right-click this file -> Run as administrator
:: ============================================================

:: Remove old task if exists
schtasks /delete /tn "idlecounthours" /f >nul 2>&1

:: Register new task
schtasks /create ^
    /tn "idlecounthours" ^
    /tr "cmd.exe /c C:\IdleAgent\run_me.bat" ^
    /sc MINUTE /mo 5 ^
    /ru SYSTEM ^
    /rl HIGHEST ^
    /f

if %errorlevel% neq 0 (
    echo [ERROR] Could not create task. Run as Administrator.
    pause & exit /b 1
)

echo [OK] Task "idlecounthours" scheduled every 5 minutes.
echo.

:: Run it immediately for first report
schtasks /run /tn "idlecounthours"
echo [OK] Task triggered for first run.
echo.

:: Confirm
schtasks /query /tn "idlecounthours"
echo.
pause
