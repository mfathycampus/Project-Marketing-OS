@echo off
rem Saves LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET into .env (secret is hidden while you paste).
cd /d "%~dp0backend"
set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Run start.bat once first.
  pause
  exit /b 1
)
"%PY%" -m app.setenv LINKEDIN_CLIENT_ID LINKEDIN_CLIENT_SECRET
pause
