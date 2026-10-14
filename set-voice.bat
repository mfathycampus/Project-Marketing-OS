@echo off
rem Choose the narrator voice (free edge-tts / Azure) and speed. Example: ar-SA-ZariyahNeural  and  +10%
cd /d "%~dp0backend"
set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Run start.bat once first.
  pause
  exit /b 1
)
echo Voices: ar-SA-HamedNeural ar-SA-ZariyahNeural ar-AE-HamdanNeural ar-AE-FatimaNeural ar-KW-FahedNeural ar-KW-NouraNeural ar-QA-MoazNeural ar-QA-AmalNeural
"%PY%" -m app.setenv TTS_VOICE TTS_RATE
pause
