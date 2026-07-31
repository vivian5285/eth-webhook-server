@echo off
chcp 65001 >nul
echo ================================================
echo 本地代码清理 - 删除临时文件
echo ================================================

echo 删除临时文件...
del /q "_diff_*.txt" 2>nul
del /q "*.bak" 2>nul
del /q "*_orig.py" 2>nul
del /q ".git_commit_msg.txt" 2>nul

echo.
echo 清理完成！
pause
