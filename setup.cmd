@echo off
REM ============================================================
REM  PSX RADAR - ONE-TIME SETUP (run once, ever)
REM  1) installs radar Python deps  2) builds + starts Laya
REM  After this, daily use is just:  START_ALL.cmd
REM ============================================================
setlocal
cd /d "%~dp0"

where docker >nul 2>&1 || (echo Docker not found - install/start Docker Desktop first & exit /b 1)
where python >nul 2>&1 || (echo Python not found - install Python 3.10+ and add to PATH & exit /b 1)

echo [1/3] Installing radar Python dependencies (patient timeouts for slow networks)...
python -m pip install --timeout 120 --retries 10 -r requirements.txt || goto :err

echo.
echo [2/3] Building Laya System 1 image (zero-dependency - no pip, no PyPI)...
docker build -t laya-engine:latest laya || goto :err
docker rm -f laya >nul 2>&1
docker run -d --name laya --restart unless-stopped -p 8000:8000 laya-engine:latest || goto :err

echo.
echo [3/3] Verifying Laya health...
timeout /t 3 /nobreak >nul
curl -s http://localhost:8000/health
echo.
echo.
echo ================================================================
echo  SETUP COMPLETE
echo    From now on, everything starts with:  START_ALL.cmd
echo ================================================================
goto :eof

:err
echo.
echo *** SETUP FAILED - see the error above ***
exit /b 1
