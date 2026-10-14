@echo off
rem Pulls every update from GitHub, even when local files were edited (they are saved aside, never lost).
rem Usage: double-click, or  .\update.bat   (start.bat calls it automatically)
setlocal
cd /d "%~dp0"
set "BRANCH=claude/charming-brown-rrrbc9"

where git >nul 2>&1 || (echo ERROR: git is not installed. & goto :end)

echo Fetching updates for %BRANCH% ...
git fetch origin %BRANCH% || (echo ERROR: could not reach GitHub - check the internet connection. & goto :end)

for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "STAMP=%%t"

rem 1) Local edits to tracked files -> saved in a stash (your .env is never touched: it is ignored by git)
git diff --quiet && git diff --cached --quiet
if errorlevel 1 (
  echo Local changes found - saving them as "local-%STAMP%" ^(see: git stash list^)
  git stash push -m "local-%STAMP%" >nul
)

rem 2) Make sure we are on the project branch
git checkout -q %BRANCH% 2>nul || git checkout -q -b %BRANCH% origin/%BRANCH%

rem 3) Fast-forward; if local commits diverged, keep them on a backup branch and take the server version
git merge -q --ff-only origin/%BRANCH% 2>nul
if errorlevel 1 (
  echo Local commits differ from the server - kept on branch backup-%STAMP%
  git branch backup-%STAMP%
  git reset -q --hard origin/%BRANCH%
)

for /f "delims=" %%c in ('git log -1 --format^="%%h %%s"') do echo Now at: %%c
echo Update complete.

:end
if /i not "%~1"=="nopause" pause
