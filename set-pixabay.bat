@echo off
rem Saves PIXABAY_API_KEY into .env (hidden while you paste).
cd /d "%~dp0backend"
set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Run start.bat once first.
  pause
  exit /b 1
)
"%PY%" -m app.setenv PIXABAY_API_KEY
pause
