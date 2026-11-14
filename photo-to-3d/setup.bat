@echo off
cd /d "%~dp0"
echo Setting up Photo to 3D Print. This takes 5-15 minutes the first time.
where py >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Install Python 3.11 from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during install. Then run this again.
  pause
  exit /b 1
)
py -3.11 -c "" >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 not found, using your default Python instead.
  py -m venv .venv
) else (
  py -3.11 -m venv .venv
)
.venv\Scripts\python install.py
pause
