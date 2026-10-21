# Projector：ESP32 语音 + 局域网网页，远程指挥台式机上的 Claude

```
微雪 ESP32-S3-Touch-AMOLED-2.16（小智固件）
   │ 语音
   ▼
小智云端（xiaozhi.me）：语音识别 → 大模型 → 调用 MCP 工具
   │ MCP 接入点（台式机主动连出，不需要公网 IP）
   ▼
台式机 Windows
 ├─ mcp_pipe.py + claude_mcp.py   小智 MCP 工具，转发到中心服务
 └─ server.py                     中心服务：多会话管理，调用 Claude Code（工作目录 D:\）
       ▲
       │ http://台式机IP:8080
局域网里的其他电脑或手机：浏览器打开网页端（可在 Edge 里“安装为应用”）
```

## 功能

- 网页端：多个会话、回复逐字输出、显示 Claude 读了哪些文件和做了哪些操作、可以中途停止，聊天记录保存在 `data/center.db`。
- 语音：每个新话题自动建一个 🎤 会话，追问时接着上一个；网页端能实时看到，也可以在电脑上接着聊。
- 权限：默认只读（Read / Glob / Grep / WebFetch）。只有勾选“允许修改文件”，或语音里口头确认后，才开放 Edit / Write。始终禁止 Bash，所以无法删除文件或运行程序。

## 在台式机上安装

1. 安装 [Python 3.10+](https://www.python.org/downloads/)，安装时勾选 “Add python.exe to PATH”。
2. 安装 Claude Code 并完成登录或配置（用 DeepSeek 的话，按 DeepSeek 文档设置 `ANTHROPIC_BASE_URL` 等环境变量）。在命令行里运行 `claude -p "你好"`，能正常回答就说明可以用了。
3. 在本目录运行：
   ```bat
   pip install -r requirements.txt
   ```
4. 把 `config.example.bat` 复制为 `config.bat`，填入以下内容：
   - `MCP_ENDPOINT`：在 xiaozhi.me 控制台的智能体配置里找到 “MCP 接入点”，复制完整地址
   - `CENTER_TOKEN`：网页端的访问密码，自己设定
5. 双击 `start.bat`。会打开两个窗口：中心服务，以及 MCP 连接。
6. 第一次运行时 Windows 防火墙会弹窗，选择允许 **专用网络**，局域网里的其他电脑才能访问。

## 使用

- 其他电脑：浏览器打开 `http://台式机IP:8080`（台式机 IP 可以用 `ipconfig` 查看），输入访问密码。
- 板子：直接说，比如“让电脑帮我找一下 D 盘里上个月的合同”。耗时长的任务会先回复“已开始”，稍后问“任务进度”就能听到结果。

## 已知限制

- 语音指令会被小智的大模型转述一次，原话可能被改写；Claude 实际收到的内容可以在对应的 🎤 会话里查看。
- 修改权限是按“整条消息”开放的，还不能逐个文件确认。
- 接 DeepSeek 等第三方模型时，Claude Code 的 WebSearch（联网搜索）用不了。
- 任务完成后不会主动推送到板子上，需要问一下进度。
