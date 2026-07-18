@echo off
rem tools/relay_to_cloud.bat
rem =========================
rem Windows version of the relay script.
rem Runs on a Windows machine at the camera site to relay the camera's RTSP feed.

if "%~1"=="" goto usage
if "%~2"=="" goto usage

set SRC=%~1
set DST=%~2

where ffmpeg >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ffmpeg not found. Install ffmpeg and add it to your PATH first.
    exit /b 1
)

echo Relaying:
echo   from (camera, local):  %SRC%
echo   to   (your server):    %DST%
echo Press Ctrl+C to stop.
echo.

:loop
ffmpeg -hide_banner -loglevel warning ^
  -rtsp_transport tcp ^
  -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 ^
  -i "%SRC%" ^
  -c copy ^
  -f flv ^
  "%DST%"
echo [relay] stream dropped — retrying in 3s...
timeout /t 3 >nul
goto loop

:usage
echo Usage: %0 ^<camera_rtsp_url^> ^<remote_rtmp_push_url^>
echo e.g.:  %0 rtsp://admin:pass@192.168.1.108:554/11 rtmp://myserver:1935/live/cam1
exit /b 1
