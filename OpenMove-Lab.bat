@echo off
REM Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
REM Proprietary software; see LICENSE.
REM OpenMove against the OpenStack LAB on this machine (a real Keystone, Nova and Cinder in a WSL distro; see tools\lab\README.md).
REM This is not the demonstration: the engine really moves volumes, inside the lab only.
REM
REM   OpenMove-Lab.bat            start the lab if needed, then OpenMove in lab mode; opens OpenMove and the OpenStack dashboard (Horizon)
REM   OpenMove-Lab.bat up         start the lab cloud
REM   OpenMove-Lab.bat seed       create the test volumes and servers in the lab
REM   OpenMove-Lab.bat more       add the second, larger set of volumes of every class
REM   OpenMove-Lab.bat clean      remove them
REM   OpenMove-Lab.bat list       show them
REM   OpenMove-Lab.bat status     is the lab cloud answering?
REM   OpenMove-Lab.bat down       stop the lab gracefully (its data is kept); OpenMove-Lab-Stop.bat does the same by double-click
REM   OpenMove-Lab.bat reset      start OpenMove in lab mode with an empty journal and evidence
REM   OpenMove-Lab.bat --port 9000 --no-browser    any option that starts with a dash goes on to OpenMove
setlocal
cd /d "%~dp0"
title OpenMove lab

set PYTHON=
where py >nul 2>nul && set PYTHON=py -3
if "%PYTHON%"=="" where python >nul 2>nul && set PYTHON=python
if "%PYTHON%"=="" (
    echo Python 3 was not found. Install it from python.org ^(tick "Add python.exe to PATH"^).
    pause
    exit /b 1
)

set CMD=%1
if "%CMD%"=="" goto :run
if "%CMD:~0,1%"=="-" goto :run
if /i "%CMD%"=="up" goto :tool
if /i "%CMD%"=="down" goto :tool
if /i "%CMD%"=="seed" goto :tool
if /i "%CMD%"=="more" goto :tool
if /i "%CMD%"=="clean" goto :tool
if /i "%CMD%"=="list" goto :tool
if /i "%CMD%"=="status" goto :tool
if /i "%CMD%"=="reset" goto :reset
echo Unknown command "%CMD%". See the top of this file.
exit /b 2

:tool
%PYTHON% tools\lab\lab.py %CMD%
goto :finished

:reset
%PYTHON% -m openmove --lab --lab-reset
goto :finished

:run
%PYTHON% tools\lab\lab.py status >nul 2>nul
if errorlevel 1 (
    echo The lab cloud is not answering. Starting it ...
    %PYTHON% tools\lab\lab.py up
    if errorlevel 1 call :partial
    if defined LABFAILED goto :finished
)
%PYTHON% -m openmove --lab %*

goto :finished

:partial
REM the cloud may answer although a guest did not start: OpenMove can still start, and says what is missing
%PYTHON% tools\lab\lab.py status >nul 2>nul
if errorlevel 1 (
    set LABFAILED=1
    exit /b 1
)
echo.
echo The lab cloud answers, but not everything started. OpenMove starts anyway; the message above says what is missing.
exit /b 0

:finished
if errorlevel 1 (
    echo.
    echo Stopped with an error. The message above says why.
    pause
)
endlocal
