@echo off
REM ==========================================================================
REM  Build the VU3INI Climate Studio Windows installer (setup .exe)
REM
REM    packaging\build_installer.bat          standard installer (AI downloaded at install)
REM    packaging\build_installer.bat full     full offline installer (AI included, several GB)
REM
REM  Needs internet while building. Creates:
REM    build\stage\            the finished app folder (own Python + all libraries)
REM    dist\VU3INI_Climate_Studio_Setup_v<version>[_Full].exe
REM  Inno Setup (free) is installed automatically with winget if it is missing.
REM ==========================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0.."
set "ROOT=%CD%"
set "BUILD=%ROOT%\build"
set "STAGE=%BUILD%\stage"
set "RT=%STAGE%\runtime"
set "FULL=0"
if /i "%~1"=="full" set "FULL=1"

echo.
echo  Building the VU3INI Climate Studio installer  (full=%FULL%)
echo  ===========================================================

REM ---- 1. uv (Python and package installer) -------------------------------
set "UV=%ROOT%\tools\uv.exe"
if not exist "%UV%" (
  echo  [1/7] Downloading uv...
  if not exist "%ROOT%\tools" mkdir "%ROOT%\tools"
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference='Stop'; $u='https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip';" ^
    "$z=Join-Path $env:TEMP 'vu3ini_uv.zip'; $d=Join-Path $env:TEMP 'vu3ini_uv';" ^
    "if (Test-Path $d) { Remove-Item $d -Recurse -Force }; Invoke-WebRequest $u -OutFile $z; Expand-Archive $z -DestinationPath $d -Force;" ^
    "$exe=Get-ChildItem $d -Recurse -Filter uv.exe | Select-Object -First 1; Copy-Item $exe.FullName -Destination '%UV%' -Force"
  if errorlevel 1 goto :fail
) else (
  echo  [1/7] uv found.
)

REM ---- 2. clean stage + standalone Python 3.12 ----------------------------
echo  [2/7] Preparing a standalone Python 3.12...
if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%"
set "UV_PYTHON_INSTALL_DIR=%BUILD%\pythons"
set "UV_PYTHON_INSTALL_BIN=0"
"%UV%" python install 3.12
if errorlevel 1 goto :fail
set "PYSRC="
for /d %%d in ("%BUILD%\pythons\cpython-3.12*-windows-x86_64*") do set "PYSRC=%%d"
if not defined PYSRC (
  echo  Could not find the downloaded Python in %BUILD%\pythons
  goto :fail
)
robocopy "%PYSRC%" "%RT%" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :fail
if exist "%RT%\Lib\EXTERNALLY-MANAGED" del /q "%RT%\Lib\EXTERNALLY-MANAGED"
if not exist "%RT%\python.exe" (
  echo  python.exe missing in %RT%
  goto :fail
)

REM ---- 3. libraries ----------------------------------------------------------
echo  [3/7] Installing libraries into the app's own Python (several minutes)...
"%UV%" pip install --python "%RT%\python.exe" --break-system-packages -r "%ROOT%\requirements.txt"
if errorlevel 1 goto :fail
if "%FULL%"=="1" (
  echo        Full build: adding PyTorch CUDA 12.8 and TerraTorch ^(works on GPU and CPU^)...
  "%UV%" pip install --python "%RT%\python.exe" --break-system-packages torch torchvision --index-url https://download.pytorch.org/whl/cu128
  if errorlevel 1 goto :fail
  "%UV%" pip install --python "%RT%\python.exe" --break-system-packages terratorch --extra-index-url https://download.pytorch.org/whl/cu128 --index-strategy unsafe-best-match
  if errorlevel 1 goto :fail
)

REM ---- 4. app files ------------------------------------------------------------
echo  [4/7] Copying the app...
for %%f in (app core analyses station ai compute reports i18n storage assets tests) do (
  robocopy "%ROOT%\%%f" "%STAGE%\%%f" /E /XD __pycache__ .pytest_cache /XF t_*.txt t_*.csv /NFL /NDL /NJH /NJS /NP >nul
  if errorlevel 8 goto :fail
)
mkdir "%STAGE%\packaging" 2>nul
copy /y "%ROOT%\packaging\install_ai.py" "%STAGE%\packaging\" >nul
copy /y "%ROOT%\packaging\NOTICE.txt" "%STAGE%\packaging\" >nul
copy /y "%ROOT%\launcher.py" "%STAGE%\" >nul
copy /y "%ROOT%\README.md" "%STAGE%\" >nul
copy /y "%ROOT%\requirements.txt" "%STAGE%\" >nul
mkdir "%STAGE%\tools" 2>nul
copy /y "%UV%" "%STAGE%\tools\" >nul

REM ---- 5. precompile + self-test ---------------------------------------------
echo  [5/7] Precompiling and testing...
for %%f in (app core analyses station ai compute reports i18n storage) do "%RT%\python.exe" -m compileall -q "%STAGE%\%%f" >nul
set "VCS_DATA_DIR=%BUILD%\selftest_data"
"%RT%\python.exe" -c "import sys; sys.path.insert(0, r'%STAGE%'); import app.main, analyses, station.reports, reports.builder; print('        App imports OK')"
if errorlevel 1 goto :fail
pushd "%STAGE%"
"%RT%\python.exe" -m pytest -q tests
if errorlevel 1 (
  popd
  echo  Tests failed; not building the installer.
  goto :fail
)
popd
set "VCS_DATA_DIR="
"%RT%\python.exe" -c "import sys; sys.path.insert(0, r'%STAGE%'); from core.paths import APP_VERSION; open(r'%BUILD%\version.txt', 'w').write(APP_VERSION)"
if errorlevel 1 goto :fail
set /p VERSION=<"%BUILD%\version.txt"
if not defined VERSION (
  echo  Could not read the app version.
  goto :fail
)
echo        Version %VERSION%

REM ---- 6. Inno Setup ------------------------------------------------------------
echo  [6/7] Finding Inno Setup...
call :find_iscc
if not defined ISCC (
  echo        Installing Inno Setup with winget...
  winget install -e --id JRSoftware.InnoSetup --accept-package-agreements --accept-source-agreements --silent
  call :find_iscc
)
if not defined ISCC (
  echo  Inno Setup 6 was not found. Install it from https://jrsoftware.org/isdl.php and run this again.
  goto :fail
)

REM ---- 7. compile installer -------------------------------------------------------
echo  [7/7] Building the installer (compression takes a while)...
if not exist "%ROOT%\dist" mkdir "%ROOT%\dist"
echo        Using "%ISCC%"
"%ISCC%" "/DMyVersion=%VERSION%" "/DFull=%FULL%" "/DRootDir=%ROOT%" "/O%ROOT%\dist" "%ROOT%\packaging\VU3INI_Climate_Studio.iss"
if errorlevel 1 goto :fail

echo.
echo  Done. Installer in: %ROOT%\dist
dir /b "%ROOT%\dist\VU3INI_Climate_Studio_Setup_v%VERSION%*"
echo.
pause
exit /b 0

:find_iscc
set "ISCC="
for %%p in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe") do (
  if exist %%p set "ISCC=%%~p"
)
exit /b 0

:fail
echo.
echo  Build failed at the step shown above. Send this window's text to the developer.
pause
exit /b 1
