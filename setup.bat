@echo off
REM ==========================================================================
REM  VU3INI Climate Studio - one-time setup (no admin rights needed)
REM  Creates a private Python 3.12 environment inside this folder, installs the
REM  packages, and picks the right PyTorch build for this PC:
REM    NVIDIA GPU found  -> PyTorch for CUDA 12.8 (needed by RTX 50-series)
REM    no NVIDIA GPU     -> CPU PyTorch
REM  Run it again after a GPU/driver change, or to repair the install.
REM ==========================================================================
setlocal
cd /d "%~dp0"
set "APPDIR=%~dp0"
set "TOOLS=%APPDIR%tools"
set "UV=%TOOLS%\uv.exe"
set "UV_PYTHON_INSTALL_DIR=%APPDIR%python"
set "UV_CACHE_DIR=%APPDIR%.uv-cache"

echo.
echo  VU3INI Climate Studio setup
echo  ---------------------------
if not exist "%TOOLS%" mkdir "%TOOLS%"

if not exist "%UV%" (
  echo  Downloading the uv installer tool...
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference='Stop'; $u='https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip';" ^
    "$z=Join-Path $env:TEMP 'vu3ini_uv.zip'; $d=Join-Path $env:TEMP 'vu3ini_uv';" ^
    "if (Test-Path $d) { Remove-Item $d -Recurse -Force }; Invoke-WebRequest $u -OutFile $z; Expand-Archive $z -DestinationPath $d -Force;" ^
    "$exe=Get-ChildItem $d -Recurse -Filter uv.exe | Select-Object -First 1; Copy-Item $exe.FullName -Destination '%UV%' -Force;" ^
    "Remove-Item $d -Recurse -Force; Remove-Item $z -Force"
  if errorlevel 1 (
    echo  Could not download uv. Check the internet connection, then run setup.bat again.
    goto :fail
  )
)
if not exist "%UV%" (
  echo  uv.exe is missing from the tools folder.
  goto :fail
)

echo  Creating the Python 3.12 environment (first time only)...
"%UV%" python install 3.12
if errorlevel 1 goto :fail
if not exist "%APPDIR%.venv\Scripts\python.exe" (
  "%UV%" venv --python 3.12 "%APPDIR%.venv"
  if errorlevel 1 goto :fail
)
set "PY=%APPDIR%.venv\Scripts\python.exe"

echo  Installing app packages (this can take a few minutes)...
"%UV%" pip install --python "%PY%" -r "%APPDIR%requirements.txt"
if errorlevel 1 goto :fail

echo  Checking for an NVIDIA GPU...
where nvidia-smi >nul 2>nul
if %errorlevel%==0 (
  nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
  echo  Installing PyTorch with CUDA 12.8 support...
  "%UV%" pip install --python "%PY%" --reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu128
) else (
  echo  No NVIDIA GPU found. Installing CPU PyTorch...
  "%UV%" pip install --python "%PY%" --reinstall torch torchvision --index-url https://download.pytorch.org/whl/cpu
)
if errorlevel 1 (
  echo  PyTorch install failed. The app still works; only the Prithvi AI maps need PyTorch.
)

echo  Installing TerraTorch for the Prithvi AI models...
"%UV%" pip install --python "%PY%" terratorch
if errorlevel 1 (
  echo  TerraTorch install failed. The app still works; only the Prithvi AI maps need it.
)

echo  Checking that PyTorch can see the GPU...
"%PY%" -c "import torch;print('PyTorch', torch.__version__, '| CUDA build', torch.version.cuda, '| GPU usable:', torch.cuda.is_available(), '|', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU only')"

echo.
echo  Setup finished. Start the app with "VU3INI Climate Studio.bat".
echo.
pause
exit /b 0

:fail
echo.
echo  Setup failed at the step shown above. Run setup.bat again; it continues from where it stopped.
echo  If it keeps failing, send the text above to the developer.
pause
exit /b 1
