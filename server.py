"""台式机中心服务：管理多个 Claude 会话，供局域网网页端和小智语音共同使用。

运行：python server.py   （配置见 start.bat 里的环境变量）
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
DATA.mkdir(exist_ok=True)

WORK_DIR = os.environ.get("CLAUDE_WORK_DIR", "D:\\")
CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
PORT = int(os.environ.get("CENTER_PORT", "8080"))
TASK_TIMEOUT = float(os.environ.get("CLAUDE_TASK_TIMEOUT", "1800"))

# 默认只读；allow_edit 时才允许改文件。始终禁止 Bash（删除、运行程序都要靠它）。
# 用 DeepSeek 等第三方模型时 WebSearch 不可用，所以不放进来。
READ_TOOLS = ["Read", "Glob", "Grep", "WebFetch"]
EDIT_TOOLS = READ_TOOLS + ["Edit", "Write"]

WEB_HINT = "用户在电脑网页上和你对话，工作目录是用户的文件库。回答用中文。"
VOICE_HINT = (
    "用户是通过一块 2 寸小屏幕的语音助手给你下指令的，指令经过语音识别和另一个助手转述，可能有错字。"
    "你的最终回答会显示在小屏幕上并被朗读：用中文给出 150 字以内的结论，"
    "不要用 Markdown 表格、代码块或长列表。较长的内容在有写权限时写进文件，只告诉用户路径；"
    "需要修改文件但没有写权限时，直接说明需要用户确认开启修改。"
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


def db(sql: str, args=(), one=False):
    with _db_lock:
        cur = _db.execute(sql, args)
        _db.commit()
        rows = [dict(r) for r in cur.fetchall()]
    return (rows[0] if rows else None) if one else rows


def session_view(s: dict) -> dict:
    return {**s, "running": s["id"] in RUNS}


def get_session(sid: str) -> dict:
    s = db("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
    if not s:
        raise HTTPException(404, "会话不存在")
    return s


def create_session(title: str, source: str) -> dict:
    now = time.time()
    sid = uuid.uuid4().hex[:12]
    db("INSERT INTO sessions VALUES (?,?,?,?,?,?)", (sid, title, source, None, now, now))
    s = get_session(sid)
    broadcast({"type": "session", "session": session_view(s)})
    return s


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
    broadcast({"type": "session", "session": session_view(get_session(sid))})
    return msg


# ---------- 事件推送（SSE） ----------

_subscribers: set[asyncio.Queue] = set()


def broadcast(event: dict) -> None:
    for q in list(_subscribers):
        q.put_nowait(event)


# ---------- 调用 Claude Code（以后换别的 agent 工具，只改这一段） ----------


def _tool_detail(name: str, args: dict) -> str:
    for key in ("file_path", "path", "pattern", "url"):
        if args.get(key):
            return str(args[key])
    return ""


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        # claude 可能是 .cmd 包装的 node 进程，要连子进程一起结束
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()


def run_claude(run: "Run", prompt: str, resume_id: str | None, allow_edit: bool, hint: str, emit) -> dict:
    """在线程里运行 claude，逐行解析 stream-json 事件，通过 emit 回传；返回最终结果。"""
    cmd = [
        CLAUDE_BIN, "-p",
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        "--allowedTools", ",".join(EDIT_TOOLS if allow_edit else READ_TOOLS),
        "--disallowedTools", "Bash",
        "--append-system-prompt", hint,
    ]
    if resume_id:
        cmd += ["--resume", resume_id]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", cwd=WORK_DIR,
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
                              "detail": _tool_detail(block.get("name"), block.get("input") or {})})
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


async def start_turn(sid: str, text: str, allow_edit: bool, hint: str) -> Run:
    if not CLAUDE_BIN:
        raise HTTPException(500, "台式机上找不到 claude 命令，请先安装 Claude Code")
    if sid in RUNS:
        raise HTTPException(409, "这个会话还有任务在进行，请等它完成或先停止")
    sess = get_session(sid)
    run = RUNS[sid] = Run(sid)
    add_message(sid, "user", text, {"allow_edit": allow_edit})
    asyncio.create_task(_drive(run, sess, text, allow_edit, hint))
    return run


async def _drive(run: Run, sess: dict, text: str, allow_edit: bool, hint: str) -> None:
    loop = asyncio.get_running_loop()

    def emit(ev):
        loop.call_soon_threadsafe(_on_event, run, ev)

    started = time.time()
    try:
        final = await asyncio.to_thread(run_claude, run, text, sess["claude_session"], allow_edit, hint, emit)
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

    del RUNS[run.sid]
    run.done.set()
    broadcast({"type": "session", "session": session_view(get_session(run.sid))})


# ---------- HTTP 接口 ----------

app = FastAPI()


def auth(request: Request) -> None:
    given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    given = given or request.query_params.get("token", "")
    if not secrets.compare_digest(given, TOKEN):
        raise HTTPException(401, "访问密码不对")


class NewSession(BaseModel):
    title: str = "新会话"


class Rename(BaseModel):
    title: str


class Send(BaseModel):
    text: str
    allow_edit: bool = False


class VoiceTask(BaseModel):
    task: str
    allow_edit: bool = False
    continue_last: bool = False
    wait: float = 40


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/sessions", dependencies=[Depends(auth)])
def list_sessions():
    return [session_view(s) for s in db("SELECT * FROM sessions ORDER BY updated DESC")]


@app.post("/api/sessions", dependencies=[Depends(auth)])
def new_session(body: NewSession):
    return session_view(create_session(body.title.strip()[:40] or "新会话", "web"))


@app.get("/api/sessions/{sid}", dependencies=[Depends(auth)])
def read_session(sid: str):
    s = get_session(sid)
    msgs = db("SELECT * FROM messages WHERE session_id=? ORDER BY id", (sid,))
    for m in msgs:
        m["meta"] = json.loads(m["meta"])
    run = RUNS.get(sid)
    live = {"text": run.text, "tools": run.tools} if run else None
    return {"session": session_view(s), "messages": msgs, "live": live}


@app.patch("/api/sessions/{sid}", dependencies=[Depends(auth)])
def rename_session(sid: str, body: Rename):
    get_session(sid)
    db("UPDATE sessions SET title=? WHERE id=?", (body.title.strip()[:40] or "未命名", sid))
    s = session_view(get_session(sid))
    broadcast({"type": "session", "session": s})
    return s


@app.delete("/api/sessions/{sid}", dependencies=[Depends(auth)])
def delete_session(sid: str):
    get_session(sid)
    if sid in RUNS:
        raise HTTPException(409, "任务进行中，请先停止")
    db("DELETE FROM messages WHERE session_id=?", (sid,))
    db("DELETE FROM sessions WHERE id=?", (sid,))
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
    await start_turn(sid, text, body.allow_edit, VOICE_HINT if s["source"] == "voice" else WEB_HINT)
    return {"ok": True}


@app.post("/api/sessions/{sid}/stop", dependencies=[Depends(auth)])
def stop_session(sid: str):
    run = RUNS.get(sid)
    if run and run.proc:
        run.stopped = True
        _kill_tree(run.proc)
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


# 给小智 MCP 工具用：每个新话题开一个语音会话，追问时接着最近的语音会话
@app.post("/api/voice", dependencies=[Depends(auth)])
async def voice_task(body: VoiceTask):
    task = body.task.strip()
    last = db("SELECT * FROM sessions WHERE source='voice' ORDER BY updated DESC LIMIT 1", one=True)
    if last and last["id"] in RUNS:
        return {"status": "busy", "reply": "上一个语音任务还在进行，请等它完成后再下新任务。"}
    sess = last if (body.continue_last and last) else create_session("🎤 " + task[:18], "voice")
    run = await start_turn(sess["id"], task, body.allow_edit, VOICE_HINT)
    try:
        await asyncio.wait_for(asyncio.shield(run.done.wait()), body.wait)
    except asyncio.TimeoutError:
        return {"status": "running", "reply": "任务已开始，可能需要几分钟。稍后问我“任务进度”就能看到结果。"}
    return {"status": "done" if run.ok else "failed", "reply": run.reply}


@app.get("/api/voice/status", dependencies=[Depends(auth)])
def voice_status():
    last = db("SELECT * FROM sessions WHERE source='voice' ORDER BY updated DESC LIMIT 1", one=True)
    if not last:
        return {"status": "none", "reply": "还没有执行过语音任务。"}
    run = RUNS.get(last["id"])
    if run:
        tools = f"，已经做了 {len(run.tools)} 步操作" if run.tools else ""
        return {"status": "running", "reply": f"任务还在进行{tools}。"}
    msg = db("SELECT * FROM messages WHERE session_id=? AND role!='user' ORDER BY id DESC LIMIT 1",
             (last["id"],), one=True)
    return {"status": "done", "reply": msg["content"] if msg else "没有结果。"}


if __name__ == "__main__":
    print(f"中心服务启动：http://本机IP:{PORT}   访问密码：{TOKEN}")
    print(f"工作目录 {WORK_DIR}，claude 路径 {CLAUDE_BIN}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
