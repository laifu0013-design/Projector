@echo off
rem 复制本文件为 config.bat 再填写；config.bat 不会提交到 git

set "CENTER_PORT=8080"
set "CLAUDE_WORK_DIR=D:\"

rem 找不到 claude 命令时（比如开机自启时 PATH 不全），可以在这里写死完整路径
rem set "CLAUDE_BIN=C:\Users\你的用户名\.local\bin\claude.exe"

rem 用 DeepSeek 等第三方模型、且没有设成系统环境变量时，去掉下面几行开头的 rem 再填写
rem set "ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic"
rem set "ANTHROPIC_AUTH_TOKEN=你的 DeepSeek API Key"
rem set "ANTHROPIC_MODEL=deepseek-chat"
