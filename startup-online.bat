@echo off
REM Explicitly opt into the source launcher's network bootstrap.
set "LATEXTIFY_ALLOW_DOWNLOADS=1"
call "%~dp0startup.bat" %*
exit /b %ERRORLEVEL%
