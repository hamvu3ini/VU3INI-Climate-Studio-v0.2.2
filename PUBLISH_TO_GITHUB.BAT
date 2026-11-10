@echo off
REM ==========================================================================
REM  VU3INI Climate Studio - publish this folder to GitHub
REM
REM  First run : installs Git and GitHub CLI if missing (winget), signs you in
REM              to GitHub in the browser, creates the repository and pushes.
REM  Later runs: commits your changes and pushes them to the same repository.
REM  Optional  : creates a GitHub Release and attaches the installer from dist\
REM
REM  Never uploaded (see .gitignore): .venv, python, tools, build, dist, caches.
REM  Your settings, keys and station data live in %APPDATA% and are never in
REM  this folder.
REM ==========================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Publish VU3INI Climate Studio to GitHub

echo.
echo  Publish VU3INI Climate Studio to GitHub
echo  ========================================
echo.

REM ---- 1. Git ------------------------------------------------------------------
where git >nul 2>nul
if errorlevel 1 (
  echo  [1/6] Git is not installed. Installing it with winget...
  winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements --silent
  set "PATH=%ProgramFiles%\Git\cmd;%PATH%"
  where git >nul 2>nul
  if errorlevel 1 (
    echo  Git could not be installed. Install it from https://git-scm.com/download/win and run this again.
    goto :fail
  )
) else (
  echo  [1/6] Git found.
)

REM ---- 2. GitHub CLI -------------------------------------------------------------
where gh >nul 2>nul
if errorlevel 1 (
  echo  [2/6] GitHub CLI is not installed. Installing it with winget...
  winget install -e --id GitHub.cli --accept-package-agreements --accept-source-agreements --silent
  set "PATH=%ProgramFiles%\GitHub CLI;%PATH%"
  where gh >nul 2>nul
  if errorlevel 1 (
    echo  GitHub CLI could not be installed. Install it from https://cli.github.com and run this again.
    goto :fail
  )
) else (
  echo  [2/6] GitHub CLI found.
)

REM ---- 3. Sign in ------------------------------------------------------------------
gh auth status >nul 2>nul
if errorlevel 1 (
  echo  [3/6] Signing in to GitHub. A browser window will open; follow the steps there.
  gh auth login --hostname github.com --git-protocol https --web
  if errorlevel 1 goto :fail
) else (
  echo  [3/6] Already signed in to GitHub.
)
gh auth setup-git >nul 2>nul
for /f "usebackq delims=" %%u in (`gh api user --jq .login`) do set "GHUSER=%%u"
if not defined GHUSER (
  echo  Could not read your GitHub user name.
  goto :fail
)
echo        GitHub account: %GHUSER%

REM ---- 4. Local repository ------------------------------------------------------------
echo  [4/6] Preparing the local repository...
if not exist ".git" (
  git init -b main >nul
  if errorlevel 1 goto :fail
)
git config user.name >nul 2>nul
if errorlevel 1 (
  set "GNAME="
  set /p "GNAME=       Your name for commits [Sasanka Boruah]: "
  if not defined GNAME set "GNAME=Sasanka Boruah"
  git config user.name "!GNAME!"
)
git config user.email >nul 2>nul
if errorlevel 1 (
  git config user.email "%GHUSER%@users.noreply.github.com"
  echo        Commit e-mail set to %GHUSER%@users.noreply.github.com ^(keeps your address private^)
)

REM Make sure local-only folders are never uploaded.
if not exist ".gitignore" (
  > .gitignore (
    echo __pycache__/
    echo *.pyc
    echo .venv/
    echo python/
    echo tools/
    echo .uv-cache/
    echo build/
    echo dist/
    echo logs/
    echo .pytest_cache/
    echo tests/data/t_*
  )
)
findstr /c:".venv/" .gitignore >nul || echo .venv/>>.gitignore
findstr /c:"build/" .gitignore >nul || echo build/>>.gitignore
findstr /c:"dist/" .gitignore >nul || echo dist/>>.gitignore
findstr /c:"tools/" .gitignore >nul || echo tools/>>.gitignore
findstr /c:"python/" .gitignore >nul || echo python/>>.gitignore
REM If local-only folders were ever added to Git, stop tracking them (files stay on the PC).
git rm -r -q --cached .venv python tools build dist >nul 2>nul

REM ---- 5. Commit -------------------------------------------------------------------------
set "VERSION="
if exist "core\paths.py" (
  for /f "tokens=2 delims==" %%v in ('findstr /b /c:"APP_VERSION" core\paths.py') do set "VERSION=%%~v"
  set "VERSION=!VERSION: =!"
  set "VERSION=!VERSION:"=!"
)
git add -A
git diff --cached --quiet
if errorlevel 1 (
  set "MSG="
  set /p "MSG=  [5/6] Describe this update [VU3INI Climate Studio v!VERSION!]: "
  if not defined MSG set "MSG=VU3INI Climate Studio v!VERSION!"
  git commit -q -m "!MSG!"
  if errorlevel 1 goto :fail
  echo        Committed: !MSG!
) else (
  echo  [5/6] No new changes to commit.
)

REM ---- 6. Create the GitHub repository (first time) or push --------------------------------
git remote get-url origin >nul 2>nul
if errorlevel 1 (
  set "REPO="
  set /p "REPO=  [6/6] Repository name [vu3ini-climate-studio]: "
  if not defined REPO set "REPO=vu3ini-climate-studio"
  set "VIS="
  set /p "VIS=        Make it public? Anyone could see the code. (y/N): "
  set "VISFLAG=--private"
  if /i "!VIS!"=="y" set "VISFLAG=--public"
  echo        Creating %GHUSER%/!REPO! ^(!VISFLAG:--=!^)...
  gh repo create "!REPO!" !VISFLAG! --source . --remote origin --push ^
     --description "VU3INI Climate Studio: Google Earth Engine climate analysis, Davis Vantage Pro2 station reports and Prithvi AI maps for Windows"
  if errorlevel 1 goto :fail
) else (
  echo  [6/6] Pushing to the existing GitHub repository...
  for /f "usebackq delims=" %%b in (`git branch --show-current`) do set "BRANCH=%%b"
  git push -u origin !BRANCH!
  if errorlevel 1 (
    echo        Push was refused. If the repository was changed on GitHub, run:  git pull --rebase  then this file again.
    goto :fail
  )
)

REM ---- Optional release with the installer --------------------------------------------------
set "SETUP="
if defined VERSION if exist "dist\VU3INI_Climate_Studio_Setup_v!VERSION!.exe" set "SETUP=dist\VU3INI_Climate_Studio_Setup_v!VERSION!.exe"
if defined SETUP (
  gh release view "v!VERSION!" >nul 2>nul
  if errorlevel 1 (
    set "REL="
    set /p "REL=  Create release v!VERSION! with !SETUP!? (Y/n): "
    if /i not "!REL!"=="n" (
      gh release create "v!VERSION!" "!SETUP!" --title "VU3INI Climate Studio v!VERSION!" ^
         --notes "Windows 10/11 installer. Download VU3INI_Climate_Studio_Setup_v!VERSION!.exe and run it. Personal, noncommercial use only; see the licence page in the installer."
      if errorlevel 1 echo        Release could not be created; the code was still published.
    )
  ) else (
    echo        Release v!VERSION! already exists on GitHub; not changed.
  )
) else (
  echo        No installer found in dist\ for v!VERSION!; skipping the release. Build it with packaging\build_installer.bat first if you want one.
)

echo.
for /f "usebackq delims=" %%r in (`gh repo view --json url --jq .url`) do set "URL=%%r"
echo  Done. Your repository: !URL!
set "OPEN="
set /p "OPEN=  Open it in the browser? (Y/n): "
if /i not "!OPEN!"=="n" start "" "!URL!"
echo.
pause
exit /b 0

:fail
echo.
echo  Publishing stopped at the step shown above. Nothing on your PC was lost.
echo  Fix the problem shown, then run PUBLISH_TO_GITHUB.BAT again; it continues from where it stopped.
pause
exit /b 1
