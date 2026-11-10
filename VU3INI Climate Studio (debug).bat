@echo off
REM Starts the app with a visible console that shows every message. Use this if the normal start fails.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The app is not set up yet. Run setup.bat first.
  pause
  exit /b 1
)
echo Checking the installation...
".venv\Scripts\python.exe" -c "import sys; sys.path.insert(0, '.'); import solara, ee, app.main; print('Imports OK')"
if errorlevel 1 (
  echo.
  echo The check above failed. Send this window's text to the developer.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "%~dp0launcher.py" --debug
echo.
echo The app has stopped. Send this window's text to the developer if something went wrong.
pause
