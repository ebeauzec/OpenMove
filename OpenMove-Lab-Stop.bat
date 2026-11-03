@echo off
REM Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
REM Proprietary software; see LICENSE.
REM Stops the OpenStack lab gracefully (instances, services, queue, database, volume groups), then the WSL distro.
REM The lab's data is kept. Start it again with OpenMove-Lab.bat. Close OpenMove first if it is open.
setlocal
cd /d "%~dp0"
title Stop the OpenMove lab
call "%~dp0OpenMove-Lab.bat" down
echo.
pause
endlocal
