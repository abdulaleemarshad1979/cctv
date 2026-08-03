@echo off
setlocal
title Sentinel GCS - Government Tactical Defense Portal
cd /d "%~dp0"

set "CCTV_PYTHON_CMD="
if defined CCTV_PYTHON if exist "%CCTV_PYTHON%" set "CCTV_PYTHON_CMD=%CCTV_PYTHON%"
if not defined CCTV_PYTHON_CMD if exist ".venv\Scripts\python.exe" set "CCTV_PYTHON_CMD=%CD%\.venv\Scripts\python.exe"
if not defined CCTV_PYTHON_CMD if exist "venv\Scripts\python.exe" set "CCTV_PYTHON_CMD=%CD%\venv\Scripts\python.exe"
if not defined CCTV_PYTHON_CMD set "CCTV_PYTHON_CMD=python"

echo ====================================================================
echo   SENTINEL GCS — GOVERNMENT AND DEFENCE TACTICAL PORTAL
echo ====================================================================
echo.

echo [1/2] Opening Government Tactical GCS Portal in default browser...
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
