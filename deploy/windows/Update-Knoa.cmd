@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Update-Knoa.ps1"
set "RESULT=%ERRORLEVEL%"
echo.
if "%RESULT%"=="0" (
  echo Knoa update and service restart completed.
) else (
  echo Knoa update failed with exit code %RESULT%.
  echo Please check the detailed update logs in: %ProgramData%\Knoa\Logs\Updates\
  echo If the update was blocked, please right-click and run as Administrator.
)
pause
exit /b %RESULT%
