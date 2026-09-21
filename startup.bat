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
if errorlevel 1 goto install_uv
goto have_uv

:install_uv
echo uv is not installed -- installing it now...
>> "%LOG%" echo --- installing uv via official installer ---
powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex" >> "%LOG%" 2>&1
REM The installer adds uv to the user PATH but this shell does not see it yet.
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>&1
if errorlevel 1 goto no_uv

:have_uv
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
>> "%LOG%" echo ERROR: automatic uv install failed.
>> "%LOG%" echo.
>> "%LOG%" echo This is the SOURCE launcher and its first run requires internet access.
>> "%LOG%" echo For a firewalled or air-gapped computer, use the release asset named
>> "%LOG%" echo LaTeXtify-Windows-Portable.zip instead of the source-code zip.
>> "%LOG%" echo Transfer that zip from a connected computer, extract the whole
>> "%LOG%" echo folder, then double-click LaTeXtify.exe. No Python or install is needed.
>> "%LOG%" echo.
>> "%LOG%" echo On a connected computer, install uv from https://docs.astral.sh/uv/
>> "%LOG%" echo and run this launcher again.
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
