@echo off
REM QBot 跨平台启动脚本 (Windows)
SETLOCAL EnableDelayedExpansion

echo ========================================
echo    QBot 启动脚本 (Windows)
echo ========================================
echo.

REM 检查 Python
echo [检查] Python 版本...
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] Python 未安装，请先安装 Python 3.7+
    pause
    exit /b 1
)
python --version

REM 检查配置文件
echo.
echo [检查] 配置文件...
if not exist "config.py" (
    echo [警告] config.py 不存在
    if exist "config.py.example" (
        echo [信息] 从模板复制配置文件...
        copy config.py.example config.py
        echo [完成] 已创建 config.py
        echo [提示] 请编辑 config.py 填入你的配置后再运行
        pause
        exit /b 0
    ) else (
        echo [错误] config.py.example 不存在
        pause
        exit /b 1
    )
)

REM 创建虚拟环境
echo.
echo [检查] 虚拟环境...
if not exist "venv" (
    echo [信息] 创建虚拟环境...
    python -m venv venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
)

REM 激活虚拟环境
echo [信息] 激活虚拟环境...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [错误] 激活虚拟环境失败
    pause
    exit /b 1
)

REM 升级 pip
echo.
echo [信息] 升级 pip...
python -m pip install --upgrade pip

REM 安装依赖
echo.
echo [信息] 安装依赖...
pip install -r requirements.txt
if errorlevel 1 (
    echo [错误] 安装依赖失败
    pause
    exit /b 1
)

REM 创建必要的目录
echo.
echo [信息] 创建必要的目录...
if not exist "exports" mkdir exports
if not exist "logs" mkdir logs
if not exist "backups" mkdir backups

REM 启动机器人
echo.
echo ========================================
echo    启动 QBot...
echo ========================================
echo.
python main.py

REM 如果程序异常退出
if errorlevel 1 (
    echo.
    echo [错误] 程序异常退出
    pause
)

ENDLOCAL
