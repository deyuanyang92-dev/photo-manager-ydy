@echo off
setlocal
title Specimen Photo Workbench

set "SCRIPT_DIR=%~dp0"
set "LOG_FILE=/tmp/specimen-photo-workbench-launch.log"

echo Starting Specimen Photo Workbench from WSL...
echo Windows path: %SCRIPT_DIR%

where wsl.exe >nul 2>nul
if errorlevel 1 (
    echo.
    echo ERROR: wsl.exe was not found. Install or enable Windows Subsystem for Linux.
    echo.
    pause
    exit /b 1
)

for /f "usebackq delims=" %%I in (`wsl.exe wslpath -a "%SCRIPT_DIR%" 2^>nul`) do set "WSL_DIR=%%I"

if not defined WSL_DIR (
    echo.
    echo ERROR: Could not convert this Windows folder to a WSL path.
    echo Try opening WSL manually and running:
    echo   cd /mnt/n/claude/photo-platform-ydy-v3
    echo   python3 main.py --check-gui
    echo.
    pause
    exit /b 1
)

echo WSL path: %WSL_DIR%
echo.

REM Launch python in the foreground of this bash, but capture its PID and
REM forward TERM/INT/HUP to it. main.py installs SIGTERM/SIGHUP/SIGINT handlers
REM that call app.quit() -> aboutToQuit -> _teardown -> db close. Without this,
REM closing the .cmd window can orphan python in WSL holding the project's
REM SQLite WAL lock -> next launch "must reboot". Process substitution keeps
REM python as the job whose PID we track (not tee).
wsl.exe --cd "%WSL_DIR%" bash -lc "set -o pipefail; python3 main.py > >(tee %LOG_FILE%) 2>&1 & PYTHON_PID=$!; trap 'kill -TERM $PYTHON_PID 2>/dev/null' TERM INT HUP; wait $PYTHON_PID"
set "APP_EXIT=%ERRORLEVEL%"

if not "%APP_EXIT%"=="0" (
    echo.
    echo ERROR: App failed to start. Exit code: %APP_EXIT%
    echo WSL log: %LOG_FILE%
    echo.
    echo Running GUI diagnostic:
    echo.
    wsl.exe --cd "%WSL_DIR%" bash -lc "python3 main.py --check-gui 2>&1 | tee -a %LOG_FILE%"
    echo.
    echo Keep this window open and send the text above if it still fails.
    echo.
    pause
    exit /b %APP_EXIT%
)

exit /b 0
