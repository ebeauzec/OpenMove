@echo off
REM Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
REM Proprietary software; see LICENSE.
REM Starts OpenMove. Double-click this file. It opens the interface in your browser.
REM It uses OpenMove.exe if that sits next to this file, otherwise Python 3 and the source.
REM Extra options are passed on, for example:  OpenMove.bat --port 9000 --no-browser
setlocal
cd /d "%~dp0"
title OpenMove

if exist "%~dp0OpenMove.exe" (
    "%~dp0OpenMove.exe" %*
    goto :finished
)
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m openmove --supervise %*
    goto :finished
)
where python >nul 2>nul
if %errorlevel%==0 (
    python -m openmove --supervise %*
    goto :finished
)
echo.
echo Python 3 was not found, and OpenMove.exe is not next to this file.
echo Install Python 3 from python.org (tick "Add python.exe to PATH"), or use the OpenMove.exe package.
echo.
pause
exit /b 1

:finished
if errorlevel 1 (
    echo.
    echo OpenMove stopped with an error. The message above says why.
    pause
)
endlocal
