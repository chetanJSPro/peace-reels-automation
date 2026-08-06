@echo off
rem Hardcoded (not %~dp0) so this still works when copied/run from the Desktop.
cd /d "c:\Users\cheta\Downloads\peace_reels_automation\peace_reels_automation"
set PY=C:\pra_venv\Scripts\python.exe

rem Start the dashboard server in the background if it isn't already running.
"%PY%" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/status', timeout=1)" 2>nul
if errorlevel 1 (
  start "Peace Reels Dashboard" /min "%PY%" "src\dashboard.py"
  timeout /t 3 /nobreak >nul
)

rem Open the dashboard in your browser.
start "" "http://127.0.0.1:8787"

rem Trigger a generation+upload run and let the dashboard show live progress.
"%PY%" -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8787/api/run', method='POST'), timeout=5)"
