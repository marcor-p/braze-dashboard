@echo off
REM run.bat — Braze dashboard launcher for Windows
REM
REM Usage:
REM   run.bat
REM   run.bat --sample
REM   run.bat --extract
REM   run.bat --static
REM   run.bat --slack

setlocal
cd /d "%~dp0"
if not defined PORT set PORT=8000

where python >nul 2>nul
if errorlevel 1 ( echo ERROR: python not found. & exit /b 1 )

if not exist ".venv" ( python -m venv .venv )
call ".venv\Scripts\activate.bat"

python -c "import flask, requests" >nul 2>nul
if errorlevel 1 (
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt
)

set MODE=flask
set DO_EXTRACT=0
set DO_SAMPLE=0
set DO_SLACK=0
for %%a in (%*) do (
  if "%%a"=="--sample"  set DO_SAMPLE=1
  if "%%a"=="--extract" set DO_EXTRACT=1
  if "%%a"=="--static"  set MODE=static
  if "%%a"=="--slack"   set DO_SLACK=1
)

if "%DO_EXTRACT%"=="1" (
  python braze_extract.py
  copy /Y out\dashboard_data.json dashboard_data.json >nul
  python alerts.py
  copy /Y out\dashboard_data.json dashboard_data.json >nul
)

if "%DO_SAMPLE%"=="1" (
  python generate_sample_data.py > dashboard_data.json
  python alerts.py --json dashboard_data.json --rewrite
)

if "%DO_SLACK%"=="1" python alerts.py --slack --quiet

if not exist "dashboard_data.json" (
  python generate_sample_data.py > dashboard_data.json
  python alerts.py --json dashboard_data.json --rewrite --quiet
)

echo.
echo  Braze KPI Dashboard
echo  -^> http://localhost:%PORT%
echo  Mode: %MODE%
echo  Press Ctrl-C to stop
echo.

if "%MODE%"=="static" ( python -m http.server %PORT% ) else ( python serve.py )
