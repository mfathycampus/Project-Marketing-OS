@echo off
rem Saves AZURE_SPEECH_KEY / AZURE_SPEECH_REGION (and optionally TTS_VOICE) into .env. The key is hidden while you paste.
cd /d "%~dp0backend"
set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Run start.bat once first.
  pause
  exit /b 1
)
"%PY%" -m app.setenv AZURE_SPEECH_KEY AZURE_SPEECH_REGION TTS_VOICE
pause
