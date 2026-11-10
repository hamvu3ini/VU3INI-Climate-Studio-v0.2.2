@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The app is not set up yet. Running setup first...
  call setup.bat
)
start "VU3INI Climate Studio" /min ".venv\Scripts\python.exe" "%~dp0launcher.py"
