@echo off
setlocal
cd /d "%~dp0"
if not exist "LaTeXtify.exe" (
  echo FAIL: LaTeXtify.exe is missing. Extract the complete ZIP again.
  pause
  exit /b 2
)
"LaTeXtify.exe" --verify-installation
set "RESULT=%ERRORLEVEL%"
type "latextify-startup.log"
echo.
if not "%RESULT%"=="0" echo Antivirus quarantine or an incomplete extraction may be responsible.
pause
exit /b %RESULT%
