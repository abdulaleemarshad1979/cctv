@echo off
setlocal
title AP Police - Centralised Drone Monitoring Portal (LITE)
cd /d "%~dp0"

set "CCTV_PYTHON_CMD="
if defined CCTV_PYTHON if exist "%CCTV_PYTHON%" set "CCTV_PYTHON_CMD=%CCTV_PYTHON%"
if not defined CCTV_PYTHON_CMD if exist ".venv\Scripts\python.exe" set "CCTV_PYTHON_CMD=%CD%\.venv\Scripts\python.exe"
if not defined CCTV_PYTHON_CMD if exist "venv\Scripts\python.exe" set "CCTV_PYTHON_CMD=%CD%\venv\Scripts\python.exe"
if not defined CCTV_PYTHON_CMD set "CCTV_PYTHON_CMD=python"

echo ====================================================================
echo   AP POLICE CENTRALIZED DRONE MONITORING PORTAL (LITE PORTAL)
echo ====================================================================
echo.

echo [1/3] Checking the project Python environment...
"%CCTV_PYTHON_CMD%" -c "import importlib.util,sys;sys.exit(0 if all(importlib.util.find_spec(x) for x in ('fastapi','uvicorn')) else 1)" >nul 2>&1
if errorlevel 1 goto :missing_web_dependencies

"%CCTV_PYTHON_CMD%" -c "import importlib.util,sys;sys.exit(0 if all(importlib.util.find_spec(x) for x in ('cv2','torch')) else 1)" >nul 2>&1
if errorlevel 1 (
  echo [WARNING] OpenCV or PyTorch is missing from this Python environment.
  echo           Real video still works in Viewing Mode, but Counting Mode
  echo           will remain unavailable until the AI packages are installed.
  echo.
  echo   To enable counting, run:
  echo   "%CCTV_PYTHON_CMD%" -m pip install -r requirements.txt
  echo.
) else (
  echo [OK] Web, video, and AI packages are available.
)

echo [2/3] Opening Web Portal in your default browser...
start "" http://127.0.0.1:8000/
echo.

echo [3/3] Starting Lite FastAPI server with:
echo       %CCTV_PYTHON_CMD%
echo --------------------------------------------------------------------
echo To stop the portal, close this window or press Ctrl+Break/Ctrl+C.
echo --------------------------------------------------------------------
echo.
"%CCTV_PYTHON_CMD%" lite_server.py
goto :end

:missing_web_dependencies
echo [ERROR] The selected Python cannot start the web portal.
echo.
echo Selected Python: %CCTV_PYTHON_CMD%
echo Install the project packages with:
echo   "%CCTV_PYTHON_CMD%" -m pip install -r requirements.txt
echo.
echo For a clean project environment, run:
echo   python -m venv .venv
echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
pause

:end
endlocal
