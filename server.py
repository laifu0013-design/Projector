"""台式机中心服务：账号、多会话、权限确认、调用 Claude Code；供局域网网页端、桌面软件和小智语音共同使用。

运行：python server.py              （配置见 config.bat 里的环境变量）
重置密码：python server.py reset-password 用户名 新密码
"""
import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import files_api
import skills_api
from store import (AVATARS, BASE, CLAUDE_HOME, DATA, DISCOVERY_PORT, FILE_ROOT, INTERNAL_TOKEN, PORT, WS_ROOT,
                   adopt_orphans, avatar_of, get_setting, set_setting, board_user, check_password, current_user, db, find_claude, first_admin,
                   get_user, hash_password, is_admin, is_local, issue_token, jdump, posix_rule_path,
                   require_admin, require_internal, session_for, session_workspace, user_public)

VERSION = "0.5.0"
LOGS = DATA / "logs"
LOGS.mkdir(exist_ok=True)
TASK_TIMEOUT = float(os.environ.get("CLAUDE_TASK_TIMEOUT", "3600"))
APPROVAL_TIMEOUT = float(os.environ.get("APPROVAL_TIMEOUT", "1800"))

# 自动放行的操作；其余操作（删除、移动、运行程序、其他命令等）都要有人确认。
# 用 DeepSeek 等第三方模型时 WebSearch 不可用，所以不放进来。
READ_TOOLS = ["Read", "Glob", "Grep", "WebFetch", "Skill"]
SAFE_BASH = [
    "Bash(ls:*)", "Bash(pwd)", "Bash(cd:*)", "Bash(cat:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(wc:*)",
    "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)",
]
EDIT_TOOLS = ["Edit", "Write"]
# 技能工具：提交的技能要管理员审核才生效，所以自动放行
SKILL_TOOLS = ["mcp__center__skill_list", "mcp__center__skill_checkout", "mcp__center__skill_submit"]
DEFAULT_BASH_ALLOW = ["python", "py", "pip", "unzip", "tar"]  # 严格档下额外自动放行的命令
# 标准档下仍要确认的危险命令（删除、移动、格式化、改系统、强制推送等）
DEFAULT_ASK_CMDS = ["rm", "rmdir", "del", "erase", "rd", "mv", "move", "Remove-Item", "ri", "Move-Item",
                    "format", "diskpart", "shutdown", "reboot", "reg", "taskkill", "chmod", "chown", "icacls", "takeown",
                    "git push", "git reset", "git clean", "git checkout --", "net user", "sc"]


def perm_mode() -> str:
    """权限档位：standard 标准（默认，只有危险命令要确认）/ strict 严格（只读命令以外都要确认）"""
    return get_setting("perm_mode", "standard")


def _cmd_rules(cmds) -> list[str]:
    rules = []
    for cmd in cmds:
        cmd = cmd.strip()
        if cmd and not any(c in cmd for c in "()\n"):
            rules += [f"Bash({cmd})", f"Bash({cmd}:*)"]
    return rules


def bash_allow_rules() -> list[str]:
    """严格档：管理员设置的自动放行命令 → Claude Code 规则，比如 python → Bash(python:*)"""
    return _cmd_rules(get_setting("bash_allow", DEFAULT_BASH_ALLOW))

WEB_HINT = (
    "用户在电脑上和你对话，用户名：{user}。工作目录是团队共用的文件库，回答用中文。"
    "删除、移动文件和执行命令会弹窗让用户确认；被拒绝时不要换个方式重试同样的操作，先问用户。"
    "用户上传的本机文件在“工作区”里（消息里会给出完整路径），处理结果如果要还给用户，直接改那些文件或在同一文件夹里新建，"
    "用户可以一键同步回自己的电脑。不要访问其他用户的工作区。"
    "用户想新建或修改团队技能（skill）时：先用 skill_checkout 取出（新建时 name 留空拿到草稿文件夹），"
    "在工作区里改好，再用 skill_submit 提交并写清更新说明；提交后要管理员审核才生效，不要直接改技能库或插件目录。"
    "这台电脑是 Windows，命令通过 Git Bash 执行：python 不能用时试 py；没有 unzip 时用 tar -xf 文件.zip 或 "
    "powershell -Command \"Expand-Archive 文件.zip 目标文件夹\"；文件路径用正斜杠或加引号。"
)
VOICE_HINT = (
    "用户是通过一块 2 寸小屏幕的语音助手给你下指令的，指令经过语音识别和另一个助手转述，可能有错字。"
    "你的最终回答会显示在小屏幕上并被朗读：用中文给出 150 字以内的结论，"
    "不要用 Markdown 表格、代码块或长列表。较长的内容写进文件，只告诉用户路径。"
    "修改文件和执行命令需要用户口头确认，尽量把需要确认的操作合并成一次；被拒绝时不要重试同样的操作。"
)

app = FastAPI()
app.include_router(files_api.router)
app.include_router(skills_api.router)


# ---------- 会话 ----------

def session_view(s: dict) -> dict:
    owner = get_user(s.get("user_id")) or {}
    return {**s, "running": s["id"] in RUNS, "pending": len(pending_for(s["id"])),
            "owner": owner.get("display_name", ""), "owner_avatar": avatar_of(owner) if owner else "orange",
            "work_dir": s.get("work_dir") or str(FILE_ROOT)}


def push_session(sid: str) -> None:
    s = db("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
    if s:
        broadcast({"type": "session", "session": session_view(s)}, s["user_id"])


def create_session(title: str, source: str, owner: dict, work_dir: str | None = None) -> dict:
    now = time.time()
    sid = uuid.uuid4().hex[:12]
    db("INSERT INTO sessions (id, title, source, claude_session, created, updated, bypass, created_by, user_id, work_dir) "
       "VALUES (?,?,?,?,?,?,0,?,?,?)",
       (sid, title, source, None, now, now, owner["display_name"], owner["id"], work_dir))
    push_session(sid)
    return db("SELECT * FROM sessions WHERE id=?", (sid,), one=True)


def add_message(sid: str, role: str, content: str, meta: dict | None = None) -> dict:
    now = time.time()
    db("INSERT INTO messages (session_id, role, content, meta, created) VALUES (?,?,?,?,?)",
       (sid, role, content, jdump(meta or {}), now))
    db("UPDATE sessions SET updated=? WHERE id=?", (now, sid))
    msg = db("SELECT * FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,), one=True)
    msg["meta"] = json.loads(msg["meta"])
    owner = (db("SELECT user_id FROM sessions WHERE id=?", (sid,), one=True) or {}).get("user_id")
    broadcast({"type": "message", "session_id": sid, "message": msg}, owner)
    push_session(sid)
    return msg


def can_bypass(user: dict) -> bool:
    return user.get("role") == "admin" or bool(user.get("can_bypass"))


# ---------- 事件推送（SSE）：每个人只收到自己会话的事件，管理员收到所有人的 ----------

_subscribers: dict[asyncio.Queue, dict] = {}


def broadcast(event: dict, owner_id) -> None:
    for q, u in list(_subscribers.items()):
        if owner_id is None or is_admin(u) or u["id"] == owner_id:
            q.put_nowait(event)


# ---------- 权限确认 ----------

APPROVALS: dict[str, dict] = {}


def pending_for(sid: str) -> list[dict]:
    return [a for a in APPROVALS.values() if a["session_id"] == sid]


def approval_public(a: dict) -> dict:
    return {k: v for k, v in a.items() if k != "future"}


def approval_summary(tool: str, inp: dict) -> str:
    if tool == "Bash":
        return f"运行命令：{inp.get('command', '')}"
    if tool == "Write":
        return f"新建或覆盖文件：{inp.get('file_path', '')}"
    if tool == "Edit":
        return f"修改文件：{inp.get('file_path', '')}"
    detail = next((str(inp[k]) for k in ("file_path", "path", "url", "command") if inp.get(k)), "")
    return f"使用 {tool}" + (f"：{detail}" if detail else "")


def resolve_approval(aid: str, allow: bool, by: str = "", message: str = "") -> dict:
    a = APPROVALS.get(aid)
    if not a:
        raise HTTPException(404, "这个确认请求已经处理过或已过期")
    if not a["future"].done():
        a["future"].set_result((allow, message or ("" if allow else "用户拒绝了这个操作，不要重试同样的操作")))
    who = f"（{by}）" if by else ""
    add_message(a["session_id"], "event", f"{'✔ 已同意' if allow else '✘ 已拒绝'}{who}：{a['summary']}")
    return a


# ---------- 调用 Claude Code（以后换别的 agent 工具，只改这一段） ----------

def _tool_detail(args: dict) -> str:
    for key in ("file_path", "path", "pattern", "url", "command", "skill"):
        if args.get(key):
            return str(args[key])[:200]
    return ""


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        # claude 可能是 .cmd 包装的 node 进程，要连子进程一起结束
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()


def _hint_file(sess: dict, owner: dict) -> Path:
    hint = VOICE_HINT if sess["source"] == "voice" else WEB_HINT.format(user=owner["display_name"])
    path = DATA / "mcp" / f"hint_{sess['id']}.txt"
    path.write_text(hint, encoding="utf-8")
    return path


def _deny_rules(owner: dict) -> list[str]:
    """软隔离：不让 Claude 用文件工具碰聊天记录库、待审核的 skill 和别人的工作区。"""
    paths = [DATA / "center.db", DATA / "internal_token.txt", DATA / "mcp", DATA / "skills" / "submissions"]
    for u in db("SELECT id FROM users WHERE id != ?", (owner["id"],)):
        paths += [WS_ROOT / str(u["id"]), DATA / "plugins" / str(u["id"])]
    rules = []
    for p in paths:
        pat = posix_rule_path(p) + ("/**" if p.suffix == "" else "")
        rules += [f"Read({pat})", f"Edit({pat})", f"Write({pat})"]
    return rules


def build_command(claude_bin: str, sess: dict, owner: dict, extra_dirs: list[Path]) -> list[str]:
    cmd = [
        claude_bin, "-p",
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        # 提示词写进文件再传：Windows 上 claude 常是 .cmd 包装，中文参数经过 cmd 容易乱码
        "--append-system-prompt-file", str(_hint_file(sess, owner)),
        "--disallowedTools", *_deny_rules(owner),
    ]
    for d in dict.fromkeys(str(p) for p in extra_dirs):
        cmd += ["--add-dir", d]
    if plugin := skills_api.plugin_dir_for(owner):
        cmd += ["--plugin-dir", str(plugin)]
    # 工具服务（权限确认 + 技能工具）每次都加载，完全放行时也能用技能工具
    mcp_config = DATA / "mcp" / f"{sess['id']}.json"
    mcp_config.write_text(json.dumps({"mcpServers": {"center": {
        "command": sys.executable,
        "args": [str(BASE / "approval_mcp.py")],
        "env": {
            "CENTER_URL": f"http://127.0.0.1:{PORT}",
            "CENTER_TOKEN": INTERNAL_TOKEN,
            "CENTER_SESSION": sess["id"],
            "PYTHONUTF8": "1",
        },
    }}}), encoding="utf-8")
    cmd += ["--mcp-config", str(mcp_config)]
    if sess["bypass"] and can_bypass(owner) and sess["source"] != "voice":
        cmd += ["--permission-mode", "bypassPermissions"]
    elif perm_mode() == "strict":
        # 严格：只读命令 + 白名单自动放行；语音会话改文件也要确认
        auto = READ_TOOLS + SAFE_BASH + SKILL_TOOLS + bash_allow_rules() + ([] if sess["source"] == "voice" else EDIT_TOOLS)
        cmd += ["--permission-prompt-tool", "mcp__center__approve", "--allowedTools", *auto]
    else:
        # 标准：读、改、运行命令都自动放行，只有危险命令走确认（ask 规则优先于 allow）
        settings = DATA / "mcp" / f"settings_{sess['id']}.json"
        settings.write_text(json.dumps({"permissions": {"ask": _cmd_rules(get_setting("ask_cmds", DEFAULT_ASK_CMDS))}},
                                       ensure_ascii=False), encoding="utf-8")
        auto = READ_TOOLS + ["Bash", "PowerShell", "NotebookEdit", "MultiEdit"] + EDIT_TOOLS + SKILL_TOOLS
        cmd += ["--settings", str(settings), "--permission-prompt-tool", "mcp__center__approve", "--allowedTools", *auto]
    if sess["claude_session"]:
        cmd += ["--resume", sess["claude_session"]]
    return cmd


def run_claude(run: "Run", prompt: str, cmd: list[str], cwd: str, emit) -> dict:
    """在线程里运行 claude，逐行解析 stream-json 事件，通过 emit 回传；返回最终结果。"""
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", cwd=cwd,
        # 等人确认可能要很久，放宽 MCP 工具调用的超时；Windows 上 Python 启动慢，工具服务的启动等待也放宽
        env={**os.environ, "MCP_TOOL_TIMEOUT": str(int(APPROVAL_TIMEOUT * 1000) + 60000), "MCP_TIMEOUT": "60000"},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    run.proc = proc
    # stderr 要单独读走，否则输出多了会把管道塞满，claude 卡住不动
    err_tail: list[str] = []

    log_path = LOGS / f"claude_{run.sid}.log"
    log = open(log_path, "a", encoding="utf-8", errors="replace")
    log.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} ====\n{' '.join(cmd[:3])} …\n")

    def drain():
        for line in proc.stderr:  # claude 和确认工具的报错都在这里，写进日志方便排查
            err_tail.append(line)
            del err_tail[:-50]
            log.write(line)
            log.flush()
    drainer = threading.Thread(target=drain, daemon=True)
    drainer.start()
    timer = threading.Timer(TASK_TIMEOUT, lambda: (setattr(run, "timed_out", True), _kill_tree(proc)))
    timer.start()
    # 指令走 stdin，避免 Windows 命令行对中文和引号的转义问题
    proc.stdin.write(prompt)
    proc.stdin.close()
    final: dict = {}
    try:
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = ev.get("type")
            if kind == "system" and ev.get("session_id"):
                final["session_id"] = ev["session_id"]
            elif kind == "stream_event":
                inner = ev.get("event", {})
                if inner.get("type") == "message_start":
                    emit({"type": "delta_reset"})
                elif inner.get("type") == "content_block_delta":
                    text = inner.get("delta", {}).get("text")
                    if text:
                        emit({"type": "delta", "text": text})
            elif kind == "user":
                for block in ev.get("message", {}).get("content", []) or []:
                    if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                        text = json.dumps(block.get("content"), ensure_ascii=False)
                        if "Permission prompt tool" in text and "warn_perm" not in run.warnings:
                            run.warnings.append("warn_perm")
                            log.write("确认通道出错：" + text[:500] + "\n")
            elif kind == "assistant":
                for block in ev.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        emit({"type": "tool", "name": block.get("name"),
                              "detail": _tool_detail(block.get("input") or {})})
            elif kind == "result":
                final.update(ev)
        proc.wait()
        drainer.join(2)
    finally:
        timer.cancel()
        log.write(f"退出码 {proc.returncode}\n")
        log.close()
    if not final.get("type"):
        err = "".join(err_tail).strip()[-300:]
        final = {**final, "is_error": True, "result": err or f"claude 异常退出（退出码 {proc.returncode}）"}
    return final


# ---------- 会话运行 ----------

class Run:
    def __init__(self, sid: str, owner_id: int):
        self.sid = sid
        self.owner_id = owner_id
        self.proc: subprocess.Popen | None = None
        self.text = ""
        self.tools: list[dict] = []
        self.reply = ""
        self.ok = False
        self.stopped = False
        self.timed_out = False
        self.warnings: list[str] = []
        self.done = asyncio.Event()


RUNS: dict[str, Run] = {}


def _on_event(run: Run, ev: dict) -> None:
    if ev["type"] == "delta_reset":
        run.text = ""
    elif ev["type"] == "delta":
        run.text += ev["text"]
    elif ev["type"] == "tool":
        run.tools.append({"name": ev["name"], "detail": ev["detail"]})
    broadcast({**ev, "session_id": run.sid}, run.owner_id)


def build_prompt(text: str, refs: list[str], skill: str) -> str:
    head = []
    if skill:
        head.append(f"【请使用技能】{skills_api.PLUGIN_NAME}:{skill}（用 Skill 工具调用它来完成这次任务）")
    if refs:
        head.append("【用户引用的文件/文件夹】\n" + "\n".join(f"- {r}" for r in refs))
    return ("\n\n".join(head) + "\n\n" + text) if head else text


async def start_turn(sess: dict, text: str, author: dict, refs: list[str] | None = None, skill: str = "") -> Run:
    claude_bin = find_claude()
    if not claude_bin:
        raise HTTPException(500, "台式机上找不到 claude 命令：请先安装 Claude Code，或在 config.bat 里设置 CLAUDE_BIN")
    sid = sess["id"]
    if sid in RUNS:
        raise HTTPException(409, "这个会话还有任务在进行，请等它完成或先停止")
    owner = get_user(sess["user_id"]) or author
    refs = refs or []
    cwd = sess.get("work_dir") or str(FILE_ROOT)
    if not Path(cwd).is_dir():
        raise HTTPException(400, f"工作文件夹不存在：{cwd}")
    extra = [session_workspace(sess)]
    for r in refs:
        p = Path(r)
        extra.append(p if p.is_dir() else p.parent)
    run = RUNS[sid] = Run(sid, sess["user_id"])
    meta = {"author": author["display_name"], "bypass": bool(sess["bypass"])}
    if refs:
        meta["refs"] = refs
    if skill:
        meta["skill"] = skill
    add_message(sid, "user", text, meta)
    cmd = build_command(claude_bin, sess, owner, extra)
    asyncio.create_task(_drive(run, build_prompt(text, refs, skill), cmd, cwd))
    return run


async def _drive(run: Run, prompt: str, cmd: list[str], cwd: str) -> None:
    loop = asyncio.get_running_loop()

    def emit(ev):
        loop.call_soon_threadsafe(_on_event, run, ev)

    started = time.time()
    try:
        final = await asyncio.to_thread(run_claude, run, prompt, cmd, cwd, emit)
    except Exception as e:
        final = {"is_error": True, "result": f"启动 claude 失败：{e}"}
    await asyncio.sleep(0)  # 让线程里排队的事件先处理完
    if final.get("session_id"):
        db("UPDATE sessions SET claude_session=? WHERE id=?", (final["session_id"], run.sid))

    meta = {"tools": run.tools, "seconds": round(time.time() - started), "cost": final.get("total_cost_usd")}
    if run.stopped:
        run.reply = "已停止。"
        add_message(run.sid, "error", run.reply + (f"\n\n{run.text}" if run.text else ""), meta)
    elif run.timed_out:
        run.reply = f"超过 {int(TASK_TIMEOUT // 60)} 分钟没完成，已停止。"
        add_message(run.sid, "error", run.reply, meta)
    elif final.get("is_error"):
        run.reply = f"出错了：{final.get('result') or final.get('subtype')}"
        add_message(run.sid, "error", run.reply, meta)
    else:
        run.ok = True
        run.reply = final.get("result") or run.text
        add_message(run.sid, "assistant", run.reply, meta)

    if "warn_perm" in run.warnings:
        add_message(run.sid, "event", "⚠ 这一轮里权限确认通道出了故障，需要确认的操作没有执行。重发一次通常就好；"
                                      f"反复出现请把 data/logs/claude_{run.sid}.log 发给管理员。")
    for a in pending_for(run.sid):
        if not a["future"].done():
            a["future"].set_result((False, "任务已结束"))
    del RUNS[run.sid]
    run.done.set()
    push_session(run.sid)


# ---------- 登录和账号 ----------

class Credentials(BaseModel):
    username: str
    password: str
    display_name: str = ""


class MePatch(BaseModel):
    avatar: str | None = None
    display_name: str | None = None


class ChangePassword(BaseModel):
    old: str
    new: str


def _check_new_password(pw: str) -> None:
    if len(pw) < 6:
        raise HTTPException(400, "密码至少 6 位")


def me_view(u: dict) -> dict:
    return {**user_public(u), "can_bypass": can_bypass(u)}


@app.get("/api/setup")
def setup_state(request: Request):
    return {"needed": first_admin() is None, "local": is_local(request), "version": VERSION}


@app.post("/api/setup")
def setup_admin(body: Credentials, request: Request):
    """第一次运行：只能在台式机本机上创建第一个管理员。"""
    if first_admin():
        raise HTTPException(409, "已经有管理员了")
    if not is_local(request):
        raise HTTPException(403, "请在台式机本机上打开 http://localhost:%d 创建管理员" % PORT)
    _check_new_password(body.password)
    username = body.username.strip()
    if not username or db("SELECT 1 FROM users WHERE username=?", (username,), one=True):
        raise HTTPException(400, "用户名不能为空或已存在")
    db("INSERT INTO users (username, display_name, pw_hash, role, created) VALUES (?,?,?,?,?)",
       (username, body.display_name.strip() or username, hash_password(body.password), "admin", time.time()))
    adopt_orphans()
    u = db("SELECT * FROM users WHERE username=?", (username,), one=True)
    return {"token": issue_token(u["id"]), "user": me_view(u)}


@app.post("/api/login")
async def login(body: Credentials):
    u = db("SELECT * FROM users WHERE username=?", (body.username.strip(),), one=True)
    if not u or u["disabled"] or u["role"] not in ("admin", "user") or not check_password(body.password, u["pw_hash"]):
        await asyncio.sleep(1)  # 放慢猜密码
        raise HTTPException(401, "用户名或密码不对")
    return {"token": issue_token(u["id"]), "user": me_view(u)}


@app.post("/api/logout")
def logout(request: Request, user: dict = Depends(current_user)):
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    db("DELETE FROM tokens WHERE token=?", (token,))
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    return me_view(user)


@app.patch("/api/me")
def update_me(body: MePatch, user: dict = Depends(current_user)):
    if body.avatar is not None:
        if body.avatar not in AVATARS:
            raise HTTPException(400, "只能从预设头像里选")
        db("UPDATE users SET avatar=? WHERE id=?", (body.avatar, user["id"]))
    if body.display_name is not None and body.display_name.strip():
        db("UPDATE users SET display_name=? WHERE id=?", (body.display_name.strip()[:32], user["id"]))
    return me_view(get_user(user["id"]))


@app.post("/api/me/password")
def change_password(body: ChangePassword, user: dict = Depends(current_user)):
    if not check_password(body.old, user["pw_hash"]):
        raise HTTPException(400, "原密码不对")
    _check_new_password(body.new)
    db("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(body.new), user["id"]))
    return {"ok": True}


# ---------- 管理员：用户 ----------

class UserPatch(BaseModel):
    display_name: str | None = None
    avatar: str | None = None
    password: str | None = None
    role: str | None = None
    can_bypass: bool | None = None
    disabled: bool | None = None


def _target_user(uid: int) -> dict:
    u = get_user(uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    return u


@app.get("/api/admin/users")
def admin_users(admin: dict = Depends(require_admin)):
    rows = db("SELECT u.*, (SELECT COUNT(*) FROM sessions s WHERE s.user_id=u.id) AS sessions, "
              "(SELECT MAX(last_used) FROM tokens t WHERE t.user_id=u.id) AS last_seen FROM users u ORDER BY u.id")
    return [{**user_public(r), "sessions": r["sessions"], "last_seen": r["last_seen"]} for r in rows]


@app.post("/api/admin/users")
def admin_create_user(body: Credentials, admin: dict = Depends(require_admin)):
    username = body.username.strip()
    if not re.fullmatch(r"\S{1,32}", username):
        raise HTTPException(400, "用户名不能有空格，最多 32 个字")
    if db("SELECT 1 FROM users WHERE username=?", (username,), one=True):
        raise HTTPException(400, "这个用户名已经有了")
    _check_new_password(body.password)
    db("INSERT INTO users (username, display_name, pw_hash, role, created) VALUES (?,?,?,?,?)",
       (username, body.display_name.strip() or username, hash_password(body.password), "user", time.time()))
    return user_public(db("SELECT * FROM users WHERE username=?", (username,), one=True))


@app.patch("/api/admin/users/{uid}")
def admin_update_user(uid: int, body: UserPatch, admin: dict = Depends(require_admin)):
    u = _target_user(uid)
    if u["role"] == "device" and (body.role or body.password or body.disabled):
        raise HTTPException(400, "板子账号不能登录，只能改名字、头像和技能")
    if body.display_name is not None:
        db("UPDATE users SET display_name=? WHERE id=?", (body.display_name.strip()[:32] or u["username"], uid))
    if body.avatar is not None:
        if body.avatar not in AVATARS:
            raise HTTPException(400, "只能从预设头像里选")
        db("UPDATE users SET avatar=? WHERE id=?", (body.avatar, uid))
    if body.password:
        _check_new_password(body.password)
        db("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(body.password), uid))
        db("DELETE FROM tokens WHERE user_id=?", (uid,))  # 重置密码后要重新登录
    if body.role in ("admin", "user") and body.role != u["role"]:
        admins = db("SELECT COUNT(*) AS n FROM users WHERE role='admin' AND disabled=0", one=True)["n"]
        if u["role"] == "admin" and admins <= 1:
            raise HTTPException(400, "至少要保留一个管理员")
        db("UPDATE users SET role=? WHERE id=?", (body.role, uid))
    if body.can_bypass is not None:
        db("UPDATE users SET can_bypass=? WHERE id=?", (int(body.can_bypass), uid))
        if not body.can_bypass:
            db("UPDATE sessions SET bypass=0 WHERE user_id=?", (uid,))
    if body.disabled is not None:
        if body.disabled and uid == admin["id"]:
            raise HTTPException(400, "不能停用自己")
        db("UPDATE users SET disabled=? WHERE id=?", (int(body.disabled), uid))
        if body.disabled:
            db("DELETE FROM tokens WHERE user_id=?", (uid,))
    return user_public(get_user(uid))


@app.delete("/api/admin/users/{uid}")
def admin_delete_user(uid: int, admin: dict = Depends(require_admin)):
    u = _target_user(uid)
    if u["role"] == "device" or uid == admin["id"]:
        raise HTTPException(400, "不能删除板子账号或自己")
    if any(r.owner_id == uid for r in RUNS.values()):
        raise HTTPException(409, "这个用户还有任务在进行")
    for s in db("SELECT id FROM sessions WHERE user_id=?", (uid,)):
        db("DELETE FROM messages WHERE session_id=?", (s["id"],))
    for sql in ("DELETE FROM sessions WHERE user_id=?", "DELETE FROM links WHERE user_id=?",
                "DELETE FROM tokens WHERE user_id=?", "DELETE FROM skill_access WHERE user_id=?",
                "DELETE FROM users WHERE id=?"):
        db(sql, (uid,))
    shutil.rmtree(WS_ROOT / str(uid), ignore_errors=True)
    shutil.rmtree(DATA / "plugins" / str(uid), ignore_errors=True)
    return {"ok": True}


class SettingsBody(BaseModel):
    perm_mode: str | None = None
    bash_allow: list[str] | None = None
    ask_cmds: list[str] | None = None


def _clean_cmds(cmds: list[str]) -> list[str]:
    out = []
    for c in cmds:
        c = " ".join(c.split())[:60]
        if c and not any(x in c for x in "()") and c not in out:
            out.append(c)
    return out[:100]


@app.get("/api/admin/settings")
def admin_get_settings(admin: dict = Depends(require_admin)):
    return {"perm_mode": perm_mode(), "bash_allow": get_setting("bash_allow", DEFAULT_BASH_ALLOW),
            "ask_cmds": get_setting("ask_cmds", DEFAULT_ASK_CMDS), "safe": [r[5:-1] for r in SAFE_BASH],
            "default_ask": DEFAULT_ASK_CMDS, "default_allow": DEFAULT_BASH_ALLOW}


@app.put("/api/admin/settings")
def admin_put_settings(body: SettingsBody, admin: dict = Depends(require_admin)):
    if body.perm_mode in ("standard", "strict"):
        set_setting("perm_mode", body.perm_mode)
    if body.bash_allow is not None:
        set_setting("bash_allow", _clean_cmds(body.bash_allow))
    if body.ask_cmds is not None:
        set_setting("ask_cmds", _clean_cmds(body.ask_cmds))
    return admin_get_settings(admin)


def lan_urls() -> list[str]:
    """台式机在局域网里的地址，给手机扫码用。"""
    ips = []
    try:  # 找默认出口网卡的地址（不会真的发数据）
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith(("127.", "169.254.")) and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    def rank(ip):  # 家里和公司常见的局域网网段排前面，代理软件的虚拟网卡（198.18.x）排最后
        if ip.startswith(("192.168.", "10.")) or (ip.startswith("172.") and 16 <= int(ip.split(".")[1]) <= 31):
            return 0
        return 2 if ip.startswith(("198.18.", "198.19.")) else 1
    return [f"http://{ip}:{PORT}" for ip in sorted(ips, key=rank)]


@app.get("/api/lan")
def lan(user: dict = Depends(current_user)):
    return {"urls": lan_urls(), "host": f"http://{socket.gethostname()}:{PORT}"}


@app.get("/manifest.webmanifest")
def manifest():
    """手机“添加到主屏幕”用"""
    from fastapi.responses import JSONResponse
    return JSONResponse({
        "name": "Projector · Claude 工作台", "short_name": "Projector", "start_url": "/", "scope": "/",
        "display": "standalone", "background_color": "#f4efe6", "theme_color": "#f4efe6", "lang": "zh-CN",
        "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
                  {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
    }, media_type="application/manifest+json")


@app.get("/api/admin/sessions")
def admin_sessions(admin: dict = Depends(require_admin)):
    return [session_view(s) for s in db("SELECT * FROM sessions ORDER BY updated DESC")]


# ---------- 会话接口 ----------

class NewSession(BaseModel):
    title: str = "新会话"
    work_dir: str | None = None


class UpdateSession(BaseModel):
    title: str | None = None
    bypass: bool | None = None
    work_dir: str | None = None


class Send(BaseModel):
    text: str
    refs: list[str] = []
    skill: str = ""


class ApprovalRequest(BaseModel):
    session_id: str
    tool_name: str
    input: dict


class Decision(BaseModel):
    allow: bool


class VoiceTask(BaseModel):
    task: str
    continue_last: bool = False
    wait: float = 40


class VoiceConfirm(BaseModel):
    allow: bool
    wait: float = 40


def _check_work_dir(path: str, user: dict) -> str:
    p = files_api.resolve_path(path, user)
    if not p.is_dir():
        raise HTTPException(400, "工作文件夹要选一个文件夹")
    return str(p)


def _owner_only(sess: dict, user: dict) -> None:
    if sess["user_id"] != user["id"]:
        raise HTTPException(403, "这是别人的会话，只能查看")


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.get("/api/sessions")
def list_sessions(user: dict = Depends(current_user)):
    return [session_view(s) for s in db("SELECT * FROM sessions WHERE user_id=? ORDER BY updated DESC", (user["id"],))]


@app.post("/api/sessions")
def new_session(body: NewSession, user: dict = Depends(current_user)):
    work_dir = _check_work_dir(body.work_dir, user) if body.work_dir else None
    return session_view(create_session(body.title.strip()[:40] or "新会话", "web", user, work_dir))


@app.get("/api/sessions/{sid}")
def read_session(sid: str, user: dict = Depends(current_user)):
    s = session_for(sid, user)
    msgs = db("SELECT * FROM messages WHERE session_id=? ORDER BY id", (sid,))
    for m in msgs:
        m["meta"] = json.loads(m["meta"])
    run = RUNS.get(sid)
    live = {"text": run.text, "tools": run.tools} if run else None
    return {
        "session": session_view(s),
        "messages": msgs,
        "live": live,
        "approvals": [approval_public(a) for a in pending_for(sid)],
    }


@app.patch("/api/sessions/{sid}")
def update_session(sid: str, body: UpdateSession, user: dict = Depends(current_user)):
    s = session_for(sid, user)
    _owner_only(s, user)
    if body.title is not None:
        db("UPDATE sessions SET title=? WHERE id=?", (body.title.strip()[:40] or "未命名", sid))
    if body.bypass is not None:
        if body.bypass and s["source"] == "voice":
            raise HTTPException(400, "语音会话不能完全放行")
        if body.bypass and not can_bypass(user):
            raise HTTPException(403, "你没有“完全放行”的权限，请找管理员开通")
        db("UPDATE sessions SET bypass=? WHERE id=?", (int(body.bypass), sid))
    if body.work_dir is not None:
        if s["claude_session"]:
            raise HTTPException(409, "会话开始后不能换工作文件夹（Claude 的记忆跟着文件夹走），请新建一个会话")
        db("UPDATE sessions SET work_dir=? WHERE id=?", (_check_work_dir(body.work_dir, user), sid))
    push_session(sid)
    return session_view(session_for(sid, user))


@app.delete("/api/sessions/{sid}")
def delete_session(sid: str, user: dict = Depends(current_user)):
    s = session_for(sid, user)
    if sid in RUNS:
        raise HTTPException(409, "任务进行中，请先停止")
    db("DELETE FROM messages WHERE session_id=?", (sid,))
    db("DELETE FROM sessions WHERE id=?", (sid,))
    db("DELETE FROM links WHERE session_id=?", (sid,))
    shutil.rmtree(WS_ROOT / str(s["user_id"]) / sid, ignore_errors=True)
    for f in (f"{sid}.json", f"hint_{sid}.txt", f"settings_{sid}.json"):
        (DATA / "mcp" / f).unlink(missing_ok=True)
    broadcast({"type": "session_deleted", "session_id": sid}, s["user_id"])
    return {"ok": True}


@app.post("/api/sessions/{sid}/messages")
async def send_message(sid: str, body: Send, user: dict = Depends(current_user)):
    s = session_for(sid, user)
    _owner_only(s, user)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "内容为空")
    refs = [str(files_api.resolve_path(r, user)) for r in body.refs[:30]]
    skill = body.skill.strip()
    if skill and skill not in skills_api.user_skill_names(user):
        raise HTTPException(403, "你没有这个技能的使用权限")
    if s["title"] == "新会话":
        db("UPDATE sessions SET title=? WHERE id=?", (text[:20], sid))
    await start_turn(s, text, user, refs, skill)
    return {"ok": True}


@app.post("/api/sessions/{sid}/stop")
def stop_session(sid: str, user: dict = Depends(current_user)):
    session_for(sid, user)
    run = RUNS.get(sid)
    if run and run.proc:
        run.stopped = True
        for a in pending_for(sid):
            if not a["future"].done():
                a["future"].set_result((False, "用户停止了任务"))
        _kill_tree(run.proc)
    return {"ok": True}


@app.get("/api/sessions/{sid}/transcript")
def transcript(sid: str, user: dict = Depends(current_user)):
    """读取 Claude Code 自己保存的完整记录（每一步思考、工具调用和返回）。"""
    s = session_for(sid, user)
    if not s["claude_session"]:
        return {"steps": [], "note": "这个会话还没有记录"}
    files = list((CLAUDE_HOME / "projects").glob(f"*/{s['claude_session']}.jsonl"))
    if not files:
        return {"steps": [], "note": "找不到 Claude Code 的记录文件"}
    steps = []
    with files[0].open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("type") not in ("user", "assistant") or e.get("isMeta"):
                continue
            content = e.get("message", {}).get("content")
            if isinstance(content, str):
                steps.append({"kind": e["type"], "text": content})
                continue
            for b in content or []:
                t = b.get("type")
                if t == "text":
                    steps.append({"kind": e["type"], "text": b.get("text", "")})
                elif t == "thinking" and b.get("thinking"):
                    steps.append({"kind": "thinking", "text": b["thinking"]})
                elif t == "tool_use":
                    steps.append({"kind": "tool_use", "name": b.get("name"),
                                  "text": json.dumps(b.get("input"), ensure_ascii=False, indent=1)})
                elif t == "tool_result":
                    c = b.get("content")
                    if isinstance(c, list):
                        c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
                    text = str(c or "")
                    steps.append({"kind": "tool_result", "error": bool(b.get("is_error")),
                                  "text": text[:3000] + ("……（已截断）" if len(text) > 3000 else "")})
    return {"steps": steps}


# 给 approval_mcp.py 用：挂起直到有人在网页或语音里做出决定
@app.post("/api/approvals")
async def request_approval(body: ApprovalRequest, _: dict = Depends(require_internal)):
    run = RUNS.get(body.session_id)
    if not run:
        return {"allow": False, "message": "任务已结束"}
    aid = uuid.uuid4().hex[:8]
    future = asyncio.get_running_loop().create_future()
    a = APPROVALS[aid] = {
        "id": aid, "session_id": body.session_id, "tool": body.tool_name, "input": body.input,
        "summary": approval_summary(body.tool_name, body.input), "created": time.time(), "future": future,
    }
    broadcast({"type": "approval", "approval": approval_public(a)}, run.owner_id)
    push_session(body.session_id)
    try:
        allow, message = await asyncio.wait_for(future, APPROVAL_TIMEOUT)
    except asyncio.TimeoutError:
        allow, message = False, "等待用户确认超时"
        add_message(body.session_id, "event", f"⌛ 确认超时，已拒绝：{a['summary']}")
    finally:
        APPROVALS.pop(aid, None)
        broadcast({"type": "approval_done", "id": aid, "session_id": body.session_id}, run.owner_id)
        push_session(body.session_id)
    return {"allow": allow, "message": message}


@app.post("/api/approvals/{aid}")
def decide_approval(aid: str, body: Decision, user: dict = Depends(current_user)):
    a = APPROVALS.get(aid)
    if not a:
        raise HTTPException(404, "这个确认请求已经处理过或已过期")
    session_for(a["session_id"], user)  # 只有会话主人和管理员能确认
    resolve_approval(aid, body.allow, user["display_name"])
    return {"ok": True}


@app.get("/api/events")
async def events(request: Request, user: dict = Depends(current_user)):
    q: asyncio.Queue = asyncio.Queue()
    _subscribers[q] = user

    async def stream():
        try:
            yield ": connected\n\n"
            while not await request.is_disconnected():
                try:
                    ev = await asyncio.wait_for(q.get(), 15)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            _subscribers.pop(q, None)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- 给小智 MCP 工具用（语音会话都归“板子”账号） ----------
# 每个新话题开一个语音会话，追问时接着最近的语音会话

def latest_voice_session() -> dict | None:
    return db("SELECT * FROM sessions WHERE source='voice' ORDER BY updated DESC LIMIT 1", one=True)


async def wait_voice(sid: str, timeout: float) -> dict:
    """等到任务完成或出现待确认操作，最多等 timeout 秒。"""
    end = time.time() + timeout
    while True:
        run = RUNS.get(sid)
        pending = pending_for(sid)
        if pending:
            return {"status": "confirm",
                    "reply": f"需要确认：{pending[0]['summary']}。请问用户是否同意，用户回答后调用 claude_confirm。"}
        if not run:
            msg = db("SELECT * FROM messages WHERE session_id=? AND role IN ('assistant','error') "
                     "ORDER BY id DESC LIMIT 1", (sid,), one=True)
            ok = bool(msg and msg["role"] == "assistant")
            return {"status": "done" if ok else "failed", "reply": msg["content"] if msg else "没有结果。"}
        if time.time() >= end:
            tools = f"，已经做了 {len(run.tools)} 步操作" if run.tools else ""
            return {"status": "running",
                    "reply": f"任务还在进行{tools}，可能需要几分钟。稍后问我“任务进度”就能看到结果。"}
        await asyncio.sleep(0.3)


@app.post("/api/voice")
async def voice_task(body: VoiceTask, _: dict = Depends(require_internal)):
    task = body.task.strip()
    last = latest_voice_session()
    if last and last["id"] in RUNS:
        state = await wait_voice(last["id"], 0)
        if state["status"] == "running":
            return {"status": "busy", "reply": "上一个语音任务还在进行，请等它完成后再下新任务。"}
        return state  # 有操作在等确认，先把它告诉用户
    board = board_user()
    sess = last if (body.continue_last and last) else create_session("🎤 " + task[:18], "voice", board)
    await start_turn(sess, task, board)
    return await wait_voice(sess["id"], body.wait)


@app.post("/api/voice/confirm")
async def voice_confirm(body: VoiceConfirm, _: dict = Depends(require_internal)):
    last = latest_voice_session()
    pending = pending_for(last["id"]) if last else []
    if not pending:
        return {"status": "none", "reply": "现在没有等待确认的操作。"}
    resolve_approval(pending[0]["id"], body.allow, "语音")
    await asyncio.sleep(0.5)  # 给 Claude 一点时间继续，免得马上读到同一个状态
    return await wait_voice(last["id"], body.wait)


@app.get("/api/voice/status")
async def voice_status(_: dict = Depends(require_internal)):
    last = latest_voice_session()
    if not last:
        return {"status": "none", "reply": "还没有执行过语音任务。"}
    return await wait_voice(last["id"], 0)


# ---------- 局域网自动发现：桌面软件广播询问，这里回复自己的名字和端口 ----------

def discovery_responder() -> None:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", DISCOVERY_PORT))
    except OSError as e:
        print(f"  自动发现端口 {DISCOVERY_PORT} 被占用，同事的软件需要手动填地址：{e}")
        return
    reply = json.dumps({"app": "projector", "name": socket.gethostname(), "port": PORT, "version": VERSION},
                       ensure_ascii=False).encode()
    while True:
        try:
            data, addr = sock.recvfrom(256)
            if data.startswith(b"PROJECTOR_DISCOVER"):
                sock.sendto(reply, addr)
        except OSError:
            time.sleep(1)


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "reset-password":
        if len(sys.argv) != 4:
            sys.exit("用法：python server.py reset-password 用户名 新密码")
        u = db("SELECT * FROM users WHERE username=?", (sys.argv[2],), one=True)
        if not u:
            sys.exit("没有这个用户")
        db("UPDATE users SET pw_hash=?, disabled=0 WHERE id=?", (hash_password(sys.argv[3]), u["id"]))
        db("DELETE FROM tokens WHERE user_id=?", (u["id"],))
        print(f"已重置 {u['username']} 的密码")
        return
    host = socket.gethostname()
    claude_bin = find_claude()
    print(f"Projector 中心服务 v{VERSION}")
    print(f"  本机打开：http://localhost:{PORT}    其他电脑：http://{host}:{PORT}（或在桌面软件里自动查找）")
    if not first_admin():
        print(f"  还没有管理员：请在本机浏览器打开 http://localhost:{PORT} 创建")
    print(f"  工作目录 {FILE_ROOT}")
    print(f"  claude：{claude_bin or '没找到！请安装 Claude Code，或在 config.bat 里设置 CLAUDE_BIN'}")
    print(f"  Office 预览：{files_api.SOFFICE or '没装 LibreOffice，PPT 等只能用本机程序打开'}")
    threading.Thread(target=discovery_responder, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
