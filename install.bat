@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d %~dp0
echo ==== Projector 台式机服务端安装 ====
echo.

rem 1. 找 Python（优先 py 启动器，避开 Windows 应用商店的假 python）
set "SYS_PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1 && set "SYS_PY=py -3"
if not defined SYS_PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1 && set "SYS_PY=python"
)
if not defined SYS_PY (
  echo [×] 没找到 Python 3.10 以上版本。
  echo     请到 https://www.python.org/downloads/ 下载安装，安装时勾选 "Add python.exe to PATH"，然后重新运行本脚本。
  pause
  exit /b 1
)
echo [√] Python：%SYS_PY%

rem 2. 检查 Claude Code
where claude >nul 2>&1
if errorlevel 1 (
  echo [×] 没找到 claude 命令。请先安装 Claude Code：https://docs.claude.com/zh-CN/docs/claude-code/setup
  echo     装好后在命令行运行 claude -p "你好"，能回答再重新运行本脚本。
  pause
  exit /b 1
)
echo [√] Claude Code 已安装

rem 3. 建虚拟环境并装依赖
if not exist .venv\Scripts\python.exe (
  echo 正在创建 Python 虚拟环境 .venv ...
  %SYS_PY% -m venv .venv || (echo [×] 创建虚拟环境失败 & pause & exit /b 1)
)
echo 正在安装依赖 ...
.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt || (echo [×] 安装依赖失败，检查网络后重试 & pause & exit /b 1)
echo [√] 依赖已安装

rem 4. 生成 config.bat
if exist config.bat (
  echo [√] 已有 config.bat，保留不改。要重新配置就删掉它再运行本脚本。
) else (
  echo.
  echo 下面填几项配置，直接回车用括号里的默认值。
  set "WD="
  set /p "WD=Claude 的工作目录（D:\）："
  if "!WD!"=="" set "WD=D:\"
  > config.bat (
    echo @echo off
    echo rem 由 install.bat 生成，可以直接用记事本修改
    echo set "CENTER_PORT=8080"
    echo set "CLAUDE_WORK_DIR=!WD!"
    echo rem 用 DeepSeek 等第三方模型、且没有设成系统环境变量时，去掉下面几行开头的 rem 再填写
    echo rem set "ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic"
    echo rem set "ANTHROPIC_AUTH_TOKEN=你的 DeepSeek API Key"
    echo rem set "ANTHROPIC_MODEL=deepseek-chat"
  )
  echo [√] 已生成 config.bat
)

echo.
echo 安装完成。下一步：
echo   1. 双击 start.bat 启动；第一次运行防火墙弹窗时选 "允许"（专用网络）。
echo   2. 在这台电脑的浏览器打开 http://localhost:8080 ，第一次会让你创建管理员账号；
echo      然后在 "管理" 里给同事建账号。同事的桌面软件会自动找到这台电脑。
echo   3. 想开机自动在后台运行，就装 Projector 桌面软件并打开 "服务端模式"。
echo   4. 可选：装 LibreOffice（https://zh-cn.libreoffice.org），网页里就能预览 PPT、老版 Word 等文件。
pause
