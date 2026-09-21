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
set "BUNDLEPY=%~dp0.runtime\python\python.exe"
set "OFFLINEKIT=%~dp0.offline-kit\install.py"

> "%LOG%" echo LaTeXtify startup log
>> "%LOG%" echo Run at %DATE% %TIME%
>> "%LOG%" echo ============================================================

REM Fast path: if the environment already works, skip dependency setup.
if not exist "%VENVPY%" goto setup
"%VENVPY%" -c "import latextify.gui.server" >nul 2>&1
if not errorlevel 1 goto launch

:setup
REM An offline repository bundle carries both of these. Prefer them before
REM looking for uv and never contact the network from this path.
if exist "%BUNDLEPY%" if exist "%OFFLINEKIT%" goto setup_offline

REM A plain source checkout is offline-first too. Downloads happen only via
REM startup-online.bat (which sets LATEXTIFY_ALLOW_DOWNLOADS=1 explicitly).
if /I not "%LATEXTIFY_ALLOW_DOWNLOADS%"=="1" goto no_offline_runtime

:setup_online
where uv >nul 2>&1
if errorlevel 1 goto install_uv
goto have_uv

:setup_offline
echo Setting up LaTeXtify entirely from bundled files - please wait...
>> "%LOG%" echo --- offline setup from .runtime and .offline-kit ---
set "LATEXTIFY_OFFLINE=1"
"%BUNDLEPY%" "%OFFLINEKIT%" --dir "%CD%" >> "%LOG%" 2>&1
if errorlevel 1 goto fail
if not exist "%VENVPY%" goto fail
"%VENVPY%" -c "import latextify.gui.server" >> "%LOG%" 2>&1
if errorlevel 1 goto fail
goto launch

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
if errorlevel 1 goto fail

:launch
if not exist "%VENVPY%" goto fail
if exist "%OFFLINEKIT%" (
  set "LATEXTIFY_OFFLINE=1"
  set "PATH=%~dp0.offline-kit\tectonic;%PATH%"
  set "TECTONIC_CACHE_DIR=%~dp0.offline-kit\tex-bundle-cache"
)
echo Starting LaTeXtify. Your browser should open shortly.
echo Keep this window open while you use it. Close it or press Ctrl+C to stop.
>> "%LOG%" echo --- launch: python -m latextify gui ---
"%VENVPY%" -m latextify gui %* >> "%LOG%" 2>&1
if errorlevel 1 goto fail
echo LaTeXtify has stopped.
exit /b 0

:no_uv
>> "%LOG%" echo ERROR: automatic uv install failed.
>> "%LOG%" echo.
>> "%LOG%" echo startup-online.bat was selected, but its uv download failed.
>> "%LOG%" echo For a firewalled computer, use LaTeXtify-Windows-Offline-Repo.zip
>> "%LOG%" echo for editable source or LaTeXtify-Windows-Portable.zip for ordinary use.
>> "%LOG%" echo.
>> "%LOG%" echo On a connected computer, install uv from https://docs.astral.sh/uv/
>> "%LOG%" echo and run startup-online.bat again.
goto fail

:no_offline_runtime
>> "%LOG%" echo ERROR: no working environment or bundled offline runtime was found.
>> "%LOG%" echo.
>> "%LOG%" echo This launcher does not download software unless you explicitly use
>> "%LOG%" echo startup-online.bat on a connected computer.
>> "%LOG%" echo.
>> "%LOG%" echo For an editable, air-gapped checkout, use the release asset named
>> "%LOG%" echo LaTeXtify-Windows-Offline-Repo.zip. It includes Python, the complete
>> "%LOG%" echo wheelhouse, Pandoc, Tectonic, the TeX cache, and this source tree.
>> "%LOG%" echo.
>> "%LOG%" echo For ordinary use, use LaTeXtify-Windows-Portable.zip instead.
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
