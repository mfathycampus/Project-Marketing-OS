@echo off
rem One-click start for Windows: always uses backend\.venv, so pip and the server share one Python.
setlocal
cd /d "%~dp0"

rem The whole block is parsed before it runs, so it is safe even when the update rewrites this file;
rem afterwards the (possibly new) start.bat is run fresh.
if not defined MOS_UPDATED (
  echo [1/5] Updating code...
  call "%~dp0update.bat" nopause
  set "MOS_UPDATED=1"
  call "%~f0"
  exit /b
)

cd backend
if not exist ".venv\Scripts\python.exe" (
  echo [2/5] Creating virtual environment backend\.venv ...
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo ERROR: could not create backend\.venv. Install Python 3.11+ from python.org and run again.
  pause
  exit /b 1
)
echo     Python: %PY%

echo [3/5] Installing / updating packages...
"%PY%" -m pip install --disable-pip-version-check -q -e ".[dev]"
if errorlevel 1 (
  echo ERROR: package install failed - see the message above.
  pause
  exit /b 1
)

echo     Optional: speech-to-text for "your face" videos (faster-whisper)...
"%PY%" -m pip install --disable-pip-version-check -q -e ".[dev,ugc]" || echo     (skipped - "your face" videos need it; everything else works)

echo [4/5] Chromium for high-fidelity designs (optional; built-in renderer is used if this fails)...
"%PY%" -m playwright install chromium || echo     (Chromium not installed - designs will use the built-in renderer)

echo [5/5] Stopping any old server on port 8000 and starting the new one...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r /c:":8000 .*LISTENING"') do taskkill /PID %%a /F >nul 2>&1
"%PY%" -m app.diagnose
echo.
echo Open http://localhost:8000  (keep this window open; Ctrl+C to stop)
"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
