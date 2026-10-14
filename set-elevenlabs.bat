@echo off
rem Saves ELEVENLABS_API_KEY (hidden), lists the voices in your account, then saves ELEVENLABS_VOICE_ID.
cd /d "%~dp0backend"
set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Run start.bat once first.
  pause
  exit /b 1
)
"%PY%" -m app.setenv ELEVENLABS_API_KEY
echo.
"%PY%" -m app.video.voice
echo.
"%PY%" -m app.setenv ELEVENLABS_VOICE_ID
pause
