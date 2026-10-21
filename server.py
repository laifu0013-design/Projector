"""台式机中心服务：管理多个 Claude 会话，供局域网网页端和小智语音共同使用。

运行：python server.py   （配置见 config.bat 里的环境变量）
"""
import asyncio
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
(DATA / "mcp").mkdir(parents=True, exist_ok=True)

WORK_DIR = os.environ.get("CLAUDE_WORK_DIR", "D:\\")
CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
PORT = int(os.environ.get("CENTER_PORT", "8080"))
TASK_TIMEOUT = float(os.environ.get("CLAUDE_TASK_TIMEOUT", "3600"))
APPROVAL_TIMEOUT = float(os.environ.get("APPROVAL_TIMEOUT", "1800"))

# 自动放行的操作；其余操作（删除、移动、运行程序、其他命令等）都要有人确认。
# 用 DeepSeek 等第三方模型时 WebSearch 不可用，所以不放进来。
READ_TOOLS = ["Read", "Glob", "Grep", "WebFetch"]
SAFE_BASH = [
    "Bash(ls:*)", "Bash(pwd)", "Bash(cat:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(wc:*)",
    "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)",
]
EDIT_TOOLS = ["Edit", "Write"]

WEB_HINT = (
    "用户在电脑网页上和你对话，工作目录是用户的文件库。回答用中文。"
    "删除、移动文件和执行命令会弹窗让用户确认；被拒绝时不要换个方式重试同样的操作，先问用户。"
)
VOICE_HINT = (
    "用户是通过一块 2 寸小屏幕的语音助手给你下指令的，指令经过语音识别和另一个助手转述，可能有错字。"
    "你的最终回答会显示在小屏幕上并被朗读：用中文给出 150 字以内的结论，"
    "不要用 Markdown 表格、代码块或长列表。较长的内容写进文件，只告诉用户路径。"
    "修改文件和执行命令需要用户口头确认，尽量把需要确认的操作合并成一次；被拒绝时不要重试同样的操作。"
)


def _load_token() -> str:
    token = os.environ.get("CENTER_TOKEN", "").strip()
    if token:
        return token
    path = DATA / "token.txt"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(9), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


TOKEN = _load_token()

# ---------- 存储 ----------

_db = sqlite3.connect(DATA / "center.db", check_same_thread=False)
_db.row_factory = sqlite3.Row
_db_lock = threading.Lock()
_db.executescript(
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY, title TEXT, source TEXT,
        claude_session TEXT, created REAL, updated REAL);
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
        content TEXT, meta TEXT, created REAL);
    CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id);
    """
)
for _col in ("bypass INTEGER DEFAULT 0", "created_by TEXT DEFAULT ''"):
    try:
        _db.execute(f"ALTER TABLE sessions ADD COLUMN {_col}")
    except sqlite3.OperationalError:
        pass  # 列已存在


def db(sql: str, args=(), one=False):
    with _db_lock:
        cur = _db.execute(sql, args)
        _db.commit()
        rows = [dict(r) for r in cur.fetchall()]
    return (rows[0] if rows else None) if one else rows


def session_view(s: dict) -> dict:
    return {**s, "running": s["id"] in RUNS, "pending": len(pending_for(s["id"]))}


def get_session(sid: str) -> dict:
    s = db("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
    if not s:
        raise HTTPException(404, "会话不存在")
    return s


def push_session(sid: str) -> None:
    s = db("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
    if s:
        broadcast({"type": "session", "session": session_view(s)})


def create_session(title: str, source: str, created_by: str = "") -> dict:
    now = time.time()
    sid = uuid.uuid4().hex[:12]
    db(
        "INSERT INTO sessions (id, title, source, claude_session, created, updated, bypass, created_by) "
        "VALUES (?,?,?,?,?,?,0,?)",
        (sid, title, source, None, now, now, created_by),
    )
    push_session(sid)
    return get_session(sid)


def add_message(sid: str, role: str, content: str, meta: dict | None = None) -> dict:
    now = time.time()
    db(
        "INSERT INTO messages (session_id, role, content, meta, created) VALUES (?,?,?,?,?)",
        (sid, role, content, json.dumps(meta or {}, ensure_ascii=False), now),
    )
    db("UPDATE sessions SET updated=? WHERE id=?", (now, sid))
    msg = db("SELECT * FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,), one=True)
    msg["meta"] = json.loads(msg["meta"])
    broadcast({"type": "message", "session_id": sid, "message": msg})
    push_session(sid)
    return msg


# ---------- 事件推送（SSE） ----------

_subscribers: set[asyncio.Queue] = set()


def broadcast(event: dict) -> None:
    for q in list(_subscribers):
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
    for key in ("file_path", "path", "pattern", "url", "command"):
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


def build_command(sess: dict, hint: str) -> list[str]:
    cmd = [
        CLAUDE_BIN, "-p",
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        "--append-system-prompt", hint,
    ]
    if sess["bypass"]:
        cmd += ["--permission-mode", "bypassPermissions"]
    else:
        # 语音会话改文件也要确认；网页会话改文件自动放行
        auto = READ_TOOLS + SAFE_BASH + ([] if sess["source"] == "voice" else EDIT_TOOLS)
        mcp_config = DATA / "mcp" / f"{sess['id']}.json"
        mcp_config.write_text(json.dumps({"mcpServers": {"center": {
            "command": sys.executable,
            "args": [str(BASE / "approval_mcp.py")],
            "env": {
                "CENTER_URL": f"http://127.0.0.1:{PORT}",
                "CENTER_TOKEN": TOKEN,
                "CENTER_SESSION": sess["id"],
                "PYTHONUTF8": "1",
            },
        }}}), encoding="utf-8")
        cmd += [
            "--mcp-config", str(mcp_config),
            "--permission-prompt-tool", "mcp__center__approve",
            "--allowedTools", *auto,
        ]
    if sess["claude_session"]:
        cmd += ["--resume", sess["claude_session"]]
    return cmd


def run_claude(run: "Run", prompt: str, cmd: list[str], emit) -> dict:
    """在线程里运行 claude，逐行解析 stream-json 事件，通过 emit 回传；返回最终结果。"""
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", cwd=WORK_DIR,
        # 等人确认可能要很久，放宽 MCP 工具调用的超时
        env={**os.environ, "MCP_TOOL_TIMEOUT": str(int(APPROVAL_TIMEOUT * 1000) + 60000)},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    run.proc = proc
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
            elif kind == "assistant":
                for block in ev.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        emit({"type": "tool", "name": block.get("name"),
                              "detail": _tool_detail(block.get("input") or {})})
            elif kind == "result":
                final.update(ev)
        proc.wait()
    finally:
        timer.cancel()
    if not final.get("type"):
        err = proc.stderr.read().strip()[-300:]
        final = {**final, "is_error": True, "result": err or f"claude 异常退出（退出码 {proc.returncode}）"}
    return final


# ---------- 会话运行 ----------


class Run:
    def __init__(self, sid: str):
        self.sid = sid
        self.proc: subprocess.Popen | None = None
        self.text = ""
        self.tools: list[dict] = []
        self.reply = ""
        self.ok = False
        self.stopped = False
        self.timed_out = False
        self.done = asyncio.Event()


RUNS: dict[str, Run] = {}


def _on_event(run: Run, ev: dict) -> None:
    if ev["type"] == "delta_reset":
        run.text = ""
    elif ev["type"] == "delta":
        run.text += ev["text"]
    elif ev["type"] == "tool":
        run.tools.append({"name": ev["name"], "detail": ev["detail"]})
    broadcast({**ev, "session_id": run.sid})


async def start_turn(sid: str, text: str, author: str = "") -> Run:
    if not CLAUDE_BIN:
        raise HTTPException(500, "台式机上找不到 claude 命令，请先安装 Claude Code")
    if sid in RUNS:
        raise HTTPException(409, "这个会话还有任务在进行，请等它完成或先停止")
    sess = get_session(sid)
    run = RUNS[sid] = Run(sid)
    add_message(sid, "user", text, {"author": author, "bypass": bool(sess["bypass"])})
    hint = VOICE_HINT if sess["source"] == "voice" else WEB_HINT
    asyncio.create_task(_drive(run, sess, text, build_command(sess, hint)))
    return run


async def _drive(run: Run, sess: dict, text: str, cmd: list[str]) -> None:
    loop = asyncio.get_running_loop()

    def emit(ev):
        loop.call_soon_threadsafe(_on_event, run, ev)

    started = time.time()
    try:
        final = await asyncio.to_thread(run_claude, run, text, cmd, emit)
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

    for a in pending_for(run.sid):
        if not a["future"].done():
            a["future"].set_result((False, "任务已结束"))
    del RUNS[run.sid]
    run.done.set()
    push_session(run.sid)


# ---------- HTTP 接口 ----------

app = FastAPI()


def auth(request: Request) -> None:
    given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    given = given or request.query_params.get("token", "")
    if not secrets.compare_digest(given, TOKEN):
        raise HTTPException(401, "访问密码不对")


class NewSession(BaseModel):
    title: str = "新会话"
    author: str = ""


class UpdateSession(BaseModel):
    title: str | None = None
    bypass: bool | None = None


class Send(BaseModel):
    text: str
    author: str = ""


class ApprovalRequest(BaseModel):
    session_id: str
    tool_name: str
    input: dict


class Decision(BaseModel):
    allow: bool
    author: str = ""


class VoiceTask(BaseModel):
    task: str
    continue_last: bool = False
    wait: float = 40


class VoiceConfirm(BaseModel):
    allow: bool
    wait: float = 40


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/sessions", dependencies=[Depends(auth)])
def list_sessions():
    return [session_view(s) for s in db("SELECT * FROM sessions ORDER BY updated DESC")]


@app.post("/api/sessions", dependencies=[Depends(auth)])
def new_session(body: NewSession):
    return session_view(create_session(body.title.strip()[:40] or "新会话", "web", body.author.strip()[:20]))


@app.get("/api/sessions/{sid}", dependencies=[Depends(auth)])
def read_session(sid: str):
    s = get_session(sid)
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


@app.patch("/api/sessions/{sid}", dependencies=[Depends(auth)])
def update_session(sid: str, body: UpdateSession):
    s = get_session(sid)
    if body.title is not None:
        db("UPDATE sessions SET title=? WHERE id=?", (body.title.strip()[:40] or "未命名", sid))
    if body.bypass is not None:
        if body.bypass and s["source"] == "voice":
            raise HTTPException(400, "语音会话不能完全放行")
        db("UPDATE sessions SET bypass=? WHERE id=?", (int(body.bypass), sid))
    push_session(sid)
    return session_view(get_session(sid))


@app.delete("/api/sessions/{sid}", dependencies=[Depends(auth)])
def delete_session(sid: str):
    get_session(sid)
    if sid in RUNS:
        raise HTTPException(409, "任务进行中，请先停止")
    db("DELETE FROM messages WHERE session_id=?", (sid,))
    db("DELETE FROM sessions WHERE id=?", (sid,))
    (DATA / "mcp" / f"{sid}.json").unlink(missing_ok=True)
    broadcast({"type": "session_deleted", "session_id": sid})
    return {"ok": True}


@app.post("/api/sessions/{sid}/messages", dependencies=[Depends(auth)])
async def send_message(sid: str, body: Send):
    s = get_session(sid)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "内容为空")
    if s["title"] == "新会话":
        db("UPDATE sessions SET title=? WHERE id=?", (text[:20], sid))
    await start_turn(sid, text, body.author.strip()[:20])
    return {"ok": True}


@app.post("/api/sessions/{sid}/stop", dependencies=[Depends(auth)])
def stop_session(sid: str):
    run = RUNS.get(sid)
    if run and run.proc:
        run.stopped = True
        for a in pending_for(sid):
            if not a["future"].done():
                a["future"].set_result((False, "用户停止了任务"))
        _kill_tree(run.proc)
    return {"ok": True}


@app.get("/api/sessions/{sid}/transcript", dependencies=[Depends(auth)])
def transcript(sid: str):
    """读取 Claude Code 自己保存的完整记录（每一步思考、工具调用和返回）。"""
    s = get_session(sid)
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
    return {"steps": steps, "file": str(files[0])}


# 给 approval_mcp.py 用：挂起直到有人在网页或语音里做出决定
@app.post("/api/approvals", dependencies=[Depends(auth)])
async def request_approval(body: ApprovalRequest):
    if body.session_id not in RUNS:
        return {"allow": False, "message": "任务已结束"}
    aid = uuid.uuid4().hex[:8]
    future = asyncio.get_running_loop().create_future()
    a = APPROVALS[aid] = {
        "id": aid, "session_id": body.session_id, "tool": body.tool_name, "input": body.input,
        "summary": approval_summary(body.tool_name, body.input), "created": time.time(), "future": future,
    }
    broadcast({"type": "approval", "approval": approval_public(a)})
    push_session(body.session_id)
    try:
        allow, message = await asyncio.wait_for(future, APPROVAL_TIMEOUT)
    except asyncio.TimeoutError:
        allow, message = False, "等待用户确认超时"
        add_message(body.session_id, "event", f"⌛ 确认超时，已拒绝：{a['summary']}")
    finally:
        APPROVALS.pop(aid, None)
        broadcast({"type": "approval_done", "id": aid, "session_id": body.session_id})
        push_session(body.session_id)
    return {"allow": allow, "message": message}


@app.post("/api/approvals/{aid}", dependencies=[Depends(auth)])
def decide_approval(aid: str, body: Decision):
    resolve_approval(aid, body.allow, body.author.strip()[:20])
    return {"ok": True}


@app.get("/api/events", dependencies=[Depends(auth)])
async def events(request: Request):
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.add(q)

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
            _subscribers.discard(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- 给小智 MCP 工具用 ----------
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


@app.post("/api/voice", dependencies=[Depends(auth)])
async def voice_task(body: VoiceTask):
    task = body.task.strip()
    last = latest_voice_session()
    if last and last["id"] in RUNS:
        state = await wait_voice(last["id"], 0)
        if state["status"] == "running":
            return {"status": "busy", "reply": "上一个语音任务还在进行，请等它完成后再下新任务。"}
        return state  # 有操作在等确认，先把它告诉用户
    sess = last if (body.continue_last and last) else create_session("🎤 " + task[:18], "voice", "语音")
    await start_turn(sess["id"], task, "语音")
    return await wait_voice(sess["id"], body.wait)


@app.post("/api/voice/confirm", dependencies=[Depends(auth)])
async def voice_confirm(body: VoiceConfirm):
    last = latest_voice_session()
    pending = pending_for(last["id"]) if last else []
    if not pending:
        return {"status": "none", "reply": "现在没有等待确认的操作。"}
    resolve_approval(pending[0]["id"], body.allow, "语音")
    await asyncio.sleep(0.5)  # 给 Claude 一点时间继续，免得马上读到同一个状态
    return await wait_voice(last["id"], body.wait)


@app.get("/api/voice/status", dependencies=[Depends(auth)])
async def voice_status():
    last = latest_voice_session()
    if not last:
        return {"status": "none", "reply": "还没有执行过语音任务。"}
    return await wait_voice(last["id"], 0)


if __name__ == "__main__":
    print(f"中心服务启动：http://本机IP:{PORT}   访问密码：{TOKEN}")
    print(f"工作目录 {WORK_DIR}，claude 路径 {CLAUDE_BIN}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
