@echo off
chcp 65001 >nul
cd /d %~dp0
if not exist config.bat (
  echo 请先运行 install.bat，或把 config.example.bat 复制为 config.bat 并填写配置
  pause
  exit /b 1
)
call config.bat
set "PY=python"
if exist .venv\Scripts\python.exe set "PY=.venv\Scripts\python.exe"
if defined PYTHON set "PY=%PYTHON%"
set PYTHONUTF8=1
start "Claude 中心服务" cmd /k "%PY%" server.py
pause
