@echo off
REM ============================================================
REM  PSX RADAR - START EVERYTHING (daily use)
REM  Ensures the Laya engine container is running, then opens
REM  two windows: the 1s pipeline worker + the dashboard.
REM  Optional: START_ALL.cmd --demo   (synthetic chatter)
REM  NOTE (v3.3): the worker window is now OPTIONAL - if you open
REM  only the dashboard, it AUTO-BOOTS the daemon itself. Keep the
REM  worker window if you want its live logs visible.
REM ============================================================
setlocal
cd /d "%~dp0"

echo [1/4] Checking Laya engine container...
docker ps --filter "name=laya" --filter "status=running" 2>nul | findstr /c:"laya" >nul
if not errorlevel 1 goto :laya_ok

echo   not running - trying "docker start laya"...
docker start laya >nul 2>&1
if not errorlevel 1 goto :laya_started

echo   no container found - building image (one-time)...
docker build -t laya-engine:latest laya || goto :err
docker rm -f laya >nul 2>&1
docker run -d --name laya --restart unless-stopped -p 8000:8000 laya-engine:latest || goto :err

:laya_started
echo   waiting for warm-up...
timeout /t 3 /nobreak >nul

:laya_ok
curl -s http://localhost:8000/health
echo.
echo.

echo [2/4] Checking radar Python dependencies...
pip show streamlit >nul 2>&1
if errorlevel 1 (
    echo   installing - first run only...
    python -m pip install --timeout 120 --retries 10 -r requirements.txt || goto :err
)

echo.
echo [3/4] Launching the 1s pipeline worker...
start "PSX Radar - Worker" cmd /k python run_worker.py %*

echo.
echo [4/4] Launching the dashboard...
start "PSX Radar - Dashboard" cmd /k python run_app.py

echo.
echo ================================================================
echo  ALL SYSTEMS RUNNING
echo    Dashboard : http://localhost:8501
echo    Feed API  : http://localhost:8001/api/feed
echo    Laya      : http://localhost:8000/health
echo    Stop      : close the two windows, then stop_all.cmd
echo ================================================================
goto :eof

:err
echo.
echo *** FAILED - see the error above ***
exit /b 1
