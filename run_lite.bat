@echo off
setlocal
title East Godavari District Monitoring System - EGDMS (Unified Launcher)
cd /d "%~dp0"

set "CCTV_PYTHON_CMD="
if defined CCTV_PYTHON if exist "%CCTV_PYTHON%" set "CCTV_PYTHON_CMD=%CCTV_PYTHON%"
if not defined CCTV_PYTHON_CMD if exist ".venv\Scripts\python.exe" set "CCTV_PYTHON_CMD=%CD%\.venv\Scripts\python.exe"
if not defined CCTV_PYTHON_CMD if exist "venv\Scripts\python.exe" set "CCTV_PYTHON_CMD=%CD%\venv\Scripts\python.exe"
if not defined CCTV_PYTHON_CMD set "CCTV_PYTHON_CMD=python"

echo ====================================================================
echo   EAST GODAVARI DISTRICT MONITORING SYSTEM - EGDMS (UNIFIED LAUNCHER)
echo ====================================================================
echo.

echo [1/2] Opening EGDMS Portal in default browser...
start "" http://127.0.0.1:8000/
echo.

netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul
if %errorlevel%==0 (
    echo [INFO] Server is already active on port 8000. Connected cleanly!
    goto :end
)

echo [2/2] Starting FAANG-Resilient Server...
"%CCTV_PYTHON_CMD%" lite_server.py

:end
endlocal
