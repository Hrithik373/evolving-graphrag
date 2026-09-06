@echo off
REM Start the API and the console together. Double-click, or run from a terminal.
REM Any arguments are passed through, e.g.  start.cmd --store postgres --no-seed
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo No virtualenv found. Run:  make install
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\dev.py %*
pause
