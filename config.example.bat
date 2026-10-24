@echo off
rem 复制本文件为 config.bat 再填写；config.bat 不会提交到 git

rem 小智后台复制的 MCP 接入点完整地址
set "MCP_ENDPOINT=在这里粘贴完整的接入点地址"

rem 网页端访问密码（局域网里其他电脑打开网页时要输入）
set "CENTER_TOKEN=改成你自己的密码"

set "CENTER_PORT=8080"
set "CLAUDE_WORK_DIR=D:\"

rem 用 DeepSeek 等第三方模型、且没有设成系统环境变量时，去掉下面几行开头的 rem 再填写
rem set "ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic"
rem set "ANTHROPIC_AUTH_TOKEN=你的 DeepSeek API Key"
rem set "ANTHROPIC_MODEL=deepseek-chat"
