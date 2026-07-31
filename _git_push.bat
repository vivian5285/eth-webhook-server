@echo off
chcp 65001 >nul
echo ================================================
echo GitHub推送脚本
echo ================================================
echo.

echo [1/4] 检查Git状态...
git status --short
echo.

echo [2/4] 添加所有更改...
git add -A
echo.

echo [3/4] 提交更改...
git commit -m "v16.13.0: spec v1.0 full align + old code cleanup"
echo.

echo [4/4] 推送到GitHub...
git push origin main
echo.

echo.
echo ================================================
echo 推送完成！
echo ================================================
pause
