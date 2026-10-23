@echo off
REM Double-click to run this browser automation (Windows).
REM The first run sets up a private Python environment in .venv (a few minutes).
setlocal
cd /d "%~dp0"

set SCHEDULED=0
for %%a in (%*) do if "%%a"=="--scheduled" set SCHEDULED=1

for /f "skip=1 delims=" %%h in ('certutil -hashfile requirements.txt SHA1') do if not defined REQ_HASH set REQ_HASH=%%h
set READY=
if exist ".venv\.ready" set /p READY=<".venv\.ready"
if "%READY%"=="%REQ_HASH%" goto run

echo Setting things up (first run only, takes a few minutes)...
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY (
  echo Python 3.9 or newer is needed. Install it from https://www.python.org/downloads/
  echo During install, tick "Add python.exe to PATH". Then try again.
  goto fail
)
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv || goto fail
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || goto fail
".venv\Scripts\python.exe" -m playwright install chromium || goto fail
> ".venv\.ready" echo %REQ_HASH%
echo Setup complete.

:run
".venv\Scripts\python.exe" run.py %*
set CODE=%ERRORLEVEL%
if "%SCHEDULED%"=="0" pause
exit /b %CODE%

:fail
echo Setup failed. Check your internet connection and try again.
if "%SCHEDULED%"=="0" pause
exit /b 1
