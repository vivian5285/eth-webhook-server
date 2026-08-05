@echo off
setlocal
set DATE=%date:~0,4%%date:~5,2%%date:~8,2%
set TIME=%time:~0,2%%time:~3,2%%time:~6,2%
set DATETIME=%DATE%_%TIME%
echo === PUSH TO GITHUB: %DATETIME% ===
echo.
echo [1] Local git status:
git status --short
echo.
echo [2] Git diff summary:
git diff --stat
echo.
echo [3] Commit changes:
git add -A
git status --short
echo.
echo Commit message:
set /p msg="Enter commit message (or press Enter for auto message): "
if "%msg%"=="" set msg=feat: update symbol_config BCHUSDT whitelist
git commit -m "%msg%"
echo.
echo [4] Push to origin main:
git push origin main
echo.
echo [5] PUSH COMPLETE at %DATETIME%
echo.
echo === NOW RUN DEPLOY SCRIPTS ON VPS ===
echo [5003] trading: cd ~/binance-engine && git pull
echo [5007] binanceB: cd ~/binance-engine && git pull
endlocal
