@echo off
chcp 65001 >nul
cd /d %~dp0
if not exist config.bat (
  echo 请先把 config.example.bat 复制为 config.bat 并填写配置
  pause
  exit /b 1
)
call config.bat
start "Claude 中心服务" cmd /k python server.py
timeout /t 3 /nobreak >nul
python mcp_pipe.py claude_mcp.py
pause
