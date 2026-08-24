@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0service.ps1" %*
exit /b %ERRORLEVEL%
