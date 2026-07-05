@echo off
title AP Police - Centralised Drone Monitoring Portal (LITE)
echo ====================================================================
echo   AP POLICE CENTRALIZED DRONE MONITORING PORTAL (LITE PORTAL)
echo ====================================================================
echo.

:: 1. Launching Web Browser
echo [1/2] Opening Web Portal in your default browser...
start http://127.0.0.1:8000/
echo.

:: 2. Starting FastAPI server
echo [2/2] Starting Lite FastAPI server...
echo --------------------------------------------------------------------
echo To stop the portal, close this window or press Ctrl+Break/Ctrl+C.
echo --------------------------------------------------------------------
echo.
python lite_server.py
