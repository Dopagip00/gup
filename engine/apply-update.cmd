@echo off
if "%~1"=="" (
  echo Usage: apply-update.cmd "C:\path\code-update.zip"
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0apply-update.ps1" -Package "%~1"
if errorlevel 1 pause
