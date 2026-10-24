@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-ConsoleHawk.ps1" %*
if errorlevel 1 (
  echo.
  echo ConsoleHawk could not start. See the message above.
  pause
  exit /b 1
)
