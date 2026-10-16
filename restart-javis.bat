@echo off
chcp 65001 >nul
title Khoi dong lai Javis OS
cd /d "%~dp0"

echo ============================================================
echo   Khoi dong lai Javis OS (Restart)
echo ============================================================
echo.

if "%JAVIS_PORT%"=="" set JAVIS_PORT=7777
set "URL=http://localhost:%JAVIS_PORT%"

echo [1/3] Dang tat instance Javis OS cu...
call "%~dp0stop-javis.bat"

echo.
echo [2/3] Dang bat lai Javis OS chay NEN (port %JAVIS_PORT%)...
wscript //nologo start-javis.vbs

echo.
echo [3/3] Dang kiem tra ket noi den %URL%...

REM Kiem tra neu he thong co curl
where curl.exe >nul 2>&1
if not %errorlevel%==0 (
  echo Khong tim thay curl. Cho 10 giay de server khoi dong...
  timeout /t 10 >nul
  goto :ready
)

set /a _count=0
set /a _max=45

:check_loop
set /a _count+=1
curl.exe -sf -m 2 -o nul "%URL%/health" 2>nul
if %errorlevel%==0 goto :ready

if %_count% geq %_max% goto :timeout

<nul set /p =.
timeout /t 1 >nul
goto :check_loop

:ready
echo.
echo ============================================================
echo   [OK] Javis OS da san sang tai %URL%!
echo   Hay mo %URL% va bam Ctrl+Shift+R de lam moi.
echo ============================================================
echo.
timeout /t 3 >nul
exit /b 0

:timeout
echo.
echo ============================================================
echo   [CANH BAO] Qua thoi gian cho (%_max%s), chua the ket noi %URL%.
echo   Server co the dang khoi dong cham hoac gap su co.
echo   Kiem tra loi tai file: server\javis.log
echo ============================================================
echo.
pause
exit /b 1
