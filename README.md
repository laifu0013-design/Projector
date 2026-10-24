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
 └─ server.py + approval_mcp.py   中心服务：多会话、权限确认，调用 Claude Code（工作目录 D:\）
       ▲
       │ http://台式机IP:8080
局域网里的其他电脑或手机：浏览器打开网页端（可在 Edge 里“安装为应用”）
```

## 功能

- 网页端：多个会话、回复逐字输出、显示 Claude 读了哪些文件和做了哪些操作、可以中途停止，聊天记录保存在 `data/center.db`。每个人登录时填自己的名字，消息和确认操作都会记录是谁做的。
- 完整过程：点“完整过程”按钮，可以查看 Claude Code 自己保存的每一步思考、工具调用和返回内容。
- 语音：每个新话题自动建一个 🎤 会话，追问时接着上一个；网页端能实时看到，也可以在电脑上接着聊。
- 文件：点顶栏“📁 文件”浏览台式机工作目录（D 盘），可以按文件名搜索；Claude 回复里提到的文件路径也能直接点开。只读，不能改、删台式机上的文件。
  - 直接预览：图片、PDF、视频、音频、文本和代码（自动识别 GBK 编码）、Markdown、Word（.docx）、Excel（.xlsx/.xls/.csv）
  - PPT、老版 Word（.doc）、WPS 等：台式机装了 [LibreOffice](https://zh-cn.libreoffice.org/download/download/) 就能预览（服务端自动转成 PDF），没装就提示用本机程序打开
  - 预览不了的文件：桌面软件里点“用本机程序打开”，会下载一份副本，用这台电脑上的 Word、WPS 等打开（改了不会存回台式机）；浏览器里点“下载”
  - “交给 Claude”：把文件路径填进输入框，接着说要怎么处理

## 权限

| 操作 | 网页会话 | 语音会话 |
|---|---|---|
| 读文件、搜索、`ls` / `cat` / `git status` 等安全命令 | 自动放行 | 自动放行 |
| 新建、修改文件 | 自动放行 | 要确认 |
| 删除、移动、运行程序、其他命令 | 要确认 | 要确认 |

- 确认请求会在网页上弹出卡片（浏览器在后台时会弹系统通知）。语音这边，小智会把要做的操作念出来，你说“同意”或“拒绝”。
- 30 分钟内没人确认，就自动拒绝。
- 网页会话可以打开“完全放行”，之后所有操作都不再询问。这个开关只对当前会话有效，语音会话不能打开。
- 实现方式：Claude Code 的 `--permission-prompt-tool` 参数加上 `approval_mcp.py`。

## 在台式机上安装

1. 安装 [Python 3.10+](https://www.python.org/downloads/)，安装时勾选 “Add python.exe to PATH”。
2. 安装 Claude Code 并完成登录或配置（用 DeepSeek 的话，按 DeepSeek 文档设置 `ANTHROPIC_BASE_URL` 等环境变量，也可以写进 `config.bat`）。在命令行里运行 `claude -p "你好"`，能正常回答就说明可以用了。
3. 到仓库的 [Releases](https://github.com/luckytiger125/Projector/releases) 页下载最新的 `Projector-server-vX.Y.Z.zip`，解压到任意文件夹（路径最好不带中文）。
4. 双击 `install.bat`：它会检查 Python 和 Claude Code、建好 `.venv` 装依赖，并引导你生成 `config.bat`：
   - MCP 接入点：在 xiaozhi.me 控制台的智能体配置里找到 “MCP 接入点”，复制完整地址；不用语音可以留空
   - 网页端访问密码：自己设定，留空会自动生成（启动时显示在服务窗口里）
5. 双击 `start.bat`。会打开中心服务窗口；填了 MCP 接入点的话，还会连上小智。
6. 第一次运行时 Windows 防火墙会弹窗，选择允许 **专用网络**，局域网里的其他电脑才能访问。

## 使用

- 台式机本机：浏览器打开 `http://localhost:8080`。
- 其他电脑：浏览器打开 `http://台式机IP:8080`（台式机 IP 可以用 `ipconfig` 查看），输入名字和访问密码。
- 想像普通软件一样用：安装下面的“桌面软件”。临时也可以在 Edge 里打开网页 → 右上角“…” → “应用” → “将此站点作为应用安装”。
- 板子：直接说，比如“让电脑帮我找一下 D 盘里上个月的合同”。耗时长的任务会先回复“已开始”，稍后问“任务进度”就能听到结果。

## 桌面软件（Windows）

`desktop/` 是一个用 Tauri 做的桌面壳子，窗口里就是上面的网页端，另外多了托盘图标和系统通知。

**下载安装**

1. 到仓库的 [Releases](https://github.com/luckytiger125/Projector/releases) 页，下载最新版本里的安装包，任选一个：
   - `Projector_x.x.x_x64-setup.exe`：推荐，装到当前用户下，不需要管理员权限
   - `Projector_x.x.x_x64_zh-CN.msi`：适合统一部署
2. 安装包没有签名，Windows 可能提示“已保护你的电脑”，点“更多信息” → “仍要运行”。
3. 第一次打开填服务器地址：台式机本机填 `http://localhost:8080`，同事填 `http://台式机IP:8080`。之后打开软件直接进入网页端，登录方式和网页一样。

**使用**

- 关闭窗口不会退出，而是缩到右下角托盘；单击托盘图标显示/隐藏窗口。
- 托盘菜单：显示/隐藏窗口、重新加载、修改服务器地址、服务端模式、打开日志文件夹、退出。
- 有待确认的操作时，窗口不在前台也会弹 Windows 通知。

**服务端模式（只在台式机上打开）**

打开后不再需要 `start.bat`：软件开机自动启动（缩在托盘），在后台无窗口地运行 `python server.py` 和 `python mcp_pipe.py claude_mcp.py`，进程崩溃会自动重启；托盘菜单里能看到两个进程是否在运行。

1. 先按上面“在台式机上安装”运行 `install.bat`，并确认双击 `start.bat` 能正常用（之后关掉 `start.bat` 的窗口，避免端口冲突）。
2. 在设置页勾选“服务端模式”，项目文件夹填本仓库所在的文件夹（`server.py` 和 `config.bat` 所在的位置）。也可以从托盘菜单勾选。
3. 环境变量读取 `config.bat` 里的 `set` 行；改了 `config.bat` 后在托盘里关掉再打开服务端模式即可生效。想指定 Python，可以在 `config.bat` 里加 `set "PYTHON=C:\路径\python.exe"`；项目文件夹里有 `.venv` 时会自动使用。
4. 进程输出写在日志文件夹的 `server.log` 和 `mcp_pipe.log` 里（托盘 → 打开日志文件夹）。

**自己构建**

推送到 `main` 且改动了 `desktop/` 时，GitHub Actions 会在 Windows 上自动构建安装包（在 Actions 页的 Artifacts 里）；也可以手动点 “Run workflow”。推送 `v` 开头的标签（比如 `git tag v0.1.1 && git push origin v0.1.1`）会自动发 Release。发版前记得同时改 `desktop/src-tauri/tauri.conf.json` 里的版本号。在 Mac 上开发调试：

```bash
cd desktop && npm install && npm run tauri dev
```

## 板子固件

界面、唤醒词“小橙子”、按键和皮肤的改动见 [firmware/README.md](firmware/README.md)。

## 已知限制

- 语音指令会被小智的大模型转述一次，原话可能被改写；Claude 实际收到的内容可以在对应的 🎤 会话里查看。
- 接 DeepSeek 等第三方模型时，Claude Code 的 WebSearch（联网搜索）用不了。
- 小智云端只在你说话时才会调用工具，所以任务完成、或者中途需要确认时，没法主动通知到板子，需要问一下“任务进度”。要做到主动推送，得自己部署 xiaozhi-esp32-server。
- 所有人共用同一个 D 盘工作目录，多个会话同时改同一个文件时可能会冲突。
