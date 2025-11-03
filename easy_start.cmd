@echo off
setlocal

:: Allow callers to override pause behavior (set EASY_START_PAUSE=0 to skip)
if defined EASY_START_PAUSE (
  set "PAUSE_ON_EXIT=%EASY_START_PAUSE%"
) else (
  set "PAUSE_ON_EXIT=1"
)

set "REPO_ROOT=%~dp0"
if "%REPO_ROOT:~-1%"=="\" set "REPO_ROOT=%REPO_ROOT:~0,-1%"

pushd "%REPO_ROOT%" >nul 2>&1
if errorlevel 1 (
  echo [%date% %time%] [easy_start] Failed to change directory to "%REPO_ROOT%".
  if "%PAUSE_ON_EXIT%"=="1" pause
  endlocal
  exit /b 1
)

set "PS_BIN="
where powershell.exe >nul 2>&1 && set "PS_BIN=powershell.exe"
if not defined PS_BIN (
  where pwsh.exe >nul 2>&1 && set "PS_BIN=pwsh.exe"
)
if not defined PS_BIN (
  echo [%date% %time%] [easy_start] Neither powershell.exe nor pwsh.exe was found on PATH.
  popd >nul
  if "%PAUSE_ON_EXIT%"=="1" pause
  endlocal
  exit /b 1
)

echo [%date% %time%] [easy_start] Launching %PS_BIN% scripts\easy_start.ps1
%PS_BIN% -NoProfile -ExecutionPolicy Bypass -File "%REPO_ROOT%\scripts\easy_start.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

if %EXIT_CODE% NEQ 0 (
  echo [%date% %time%] [easy_start] Script exited with code %EXIT_CODE%.
) else (
  echo [%date% %time%] [easy_start] Script completed successfully.
)

if "%PAUSE_ON_EXIT%"=="1" (
  echo [%date% %time%] [easy_start] Press any key to close this window...
  pause >nul
)

popd >nul

endlocal
exit /b %EXIT_CODE%
