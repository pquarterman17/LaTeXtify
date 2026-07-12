@echo off
REM ============================================================
REM  LaTeXtify launcher (Windows)
REM  Double-click this file to start the LaTeXtify web GUI.
REM  On failure it writes latextify-startup.log NEXT TO this
REM  file and prints it, so the error is easy to copy-paste.
REM ============================================================
setlocal
cd /d "%~dp0"
set "LOG=%~dp0latextify-startup.log"
set "VENVPY=%~dp0.venv\Scripts\python.exe"

> "%LOG%" echo LaTeXtify startup log
>> "%LOG%" echo Run at %DATE% %TIME%
>> "%LOG%" echo ============================================================

REM Fast path: if the environment already works, skip dependency setup.
if not exist "%VENVPY%" goto setup
"%VENVPY%" -c "import latextify.gui.server" >nul 2>&1
if not errorlevel 1 goto launch

:setup
where uv >nul 2>&1
if errorlevel 1 goto no_uv
echo Setting up LaTeXtify - first run installs dependencies, please wait...
>> "%LOG%" echo --- uv sync --extra gui ---
uv sync --extra gui >> "%LOG%" 2>&1

:launch
if not exist "%VENVPY%" goto fail
echo Starting LaTeXtify. Your browser should open at http://127.0.0.1:8501
echo Keep this window open while you use it. Close it or press Ctrl+C to stop.
>> "%LOG%" echo --- launch: python -m latextify gui ---
"%VENVPY%" -m latextify gui %* >> "%LOG%" 2>&1
if errorlevel 1 goto fail
echo LaTeXtify has stopped.
exit /b 0

:no_uv
>> "%LOG%" echo ERROR: 'uv' is not installed or not on your PATH, and the
>> "%LOG%" echo environment (.venv) is not set up yet.
>> "%LOG%" echo Install uv from https://docs.astral.sh/uv/ then run this again.
goto fail

:fail
echo.
echo ============================================================
echo  LaTeXtify could not start.
echo  The full error log was saved next to this script:
echo.
echo    %LOG%
echo.
echo  Copy everything between the dashed lines below and share it:
echo  ------------------------------------------------------------
type "%LOG%"
echo  ------------------------------------------------------------
echo ============================================================
pause
exit /b 1
