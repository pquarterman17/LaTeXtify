@echo off
setlocal
cd /d "%~dp0"
if not exist ".runtime\python\python.exe" (
  echo FAIL: bundled Python is missing. Extract the complete ZIP again.
  pause
  exit /b 2
)
".runtime\python\python.exe" "verify_installation.py"
set "RESULT=%ERRORLEVEL%"
echo.
if not "%RESULT%"=="0" echo Antivirus quarantine or an incomplete extraction may be responsible.
pause
exit /b %RESULT%
