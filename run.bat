@echo off
chcp 65001 >nul
setlocal

REM ============================================
REM 私人 AI 助手 - Windows 启动脚本
REM ============================================

set ENV_NAME=ai_assistant
cd /d "%~dp0"

echo ============================================
echo   私人 AI 助手 启动中...
echo ============================================

REM 尝试激活 conda 环境
where conda >nul 2>&1
if %errorlevel%==0 (
    call conda activate %ENV_NAME% 2>nul
    if errorlevel 1 (
        echo [警告] 未找到 conda 环境 '%ENV_NAME%'，使用系统默认 Python
        echo         如需创建：conda create -n %ENV_NAME% python=3.11 -y
    ) else (
        echo [OK] 已激活 conda 环境: %ENV_NAME%
    )
) else (
    echo [警告] 未找到 conda，使用系统默认 Python
)

REM 检查 Python
where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.11+
    pause
    exit /b 1
)

REM 检查关键依赖是否已安装
python -c "import PySide6" 2>nul
if errorlevel 1 (
    echo [错误] 缺少依赖，请先安装：
    echo   pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

REM 本应用连接 DeepSeek 云端 API，无需本地服务
echo.
echo [说明] 使用 DeepSeek 云端 API（请在应用设置中填写 API Key）

REM 启动 GUI
echo.
echo [启动] 桌面应用...
echo.
python main.py

if errorlevel 1 (
    echo.
    echo [错误] 应用异常退出（代码 %errorlevel%）
    pause
)

endlocal
