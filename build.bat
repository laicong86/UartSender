@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo   构建 UartSender.exe  (PyInstaller 单文件)
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 没有找到 python，请先安装 Python 3.8+ 并勾选 "Add to PATH"。
    pause
    exit /b 1
)

echo [1/3] 安装/更新 PyInstaller ...
python -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo [错误] PyInstaller 安装失败，请检查网络或 pip 源。
    pause
    exit /b 1
)

echo [2/3] 清理旧的构建目录 ...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [3/3] 开始打包 ...
echo     --icon     把 app.ico 编译进 exe 资源（资源管理器里的文件图标）
echo     --add-data 把 app.ico 一起打包，程序运行时可直接读取
echo                 （即使不加，程序也会从 exe 资源里自动取图标）

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name UartSender ^
    --icon app.ico ^
    --add-data "app.ico;." ^
    main.py

if errorlevel 1 (
    echo.
    echo [错误] 打包失败，请查看上面的日志。
    pause
    exit /b 1
)

echo.
echo ============================================
echo   打包完成： %~dp0dist\UartSender.exe
echo ============================================
echo 该 exe 为单文件绿色版，可复制到任意 Windows 机器直接运行。
pause
