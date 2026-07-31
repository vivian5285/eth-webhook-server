@echo off
chcp 65001 >nul
echo ================================================
echo 本地代码清理脚本 - 删除临时文件
echo ================================================
echo.

echo [1/4] 删除_diff_临时文件...
for %%f in (_diff_*.txt) do (
    if exist "%%f" (
        echo 删除: %%f
        del "%%f"
    )
)
echo 完成
echo.

echo [2/4] 删除临时备份文件...
for %%f in (*.bak *~) do (
    if exist "%%f" (
        echo 删除: %%f
        del "%%f"
    )
)
echo 完成
echo.

echo [3/4] 删除临时测试文件...
for %%f in (test_*.py check_*.py) do (
    if exist "%%f" (
        echo 删除: %%f
        del "%%f"
    )
)
echo 完成
echo.

echo [4/4] 清理.pyc和__pycache__...
for /d /r . %%d in (__pycache__) do (
    if exist "%%d" (
        echo 删除目录: %%d
        rd /s /q "%%d"
    )
)
for %%f in (*.pyc) do (
    if exist "%%f" (
        del "%%f"
    )
)
echo 完成
echo.

echo.
echo ================================================
echo 清理完成！
echo ================================================
pause
