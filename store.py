"""公共部分：配置、数据库、账号和登录校验。server.py、files_api.py、skills_api.py 都从这里取。"""
import glob
import hashlib
import ipaddress
import json
import os
import secrets
import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path

from fastapi import Depends, HTTPException, Request

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
for _d in ("mcp", "workspaces", "skills/library", "skills/submissions", "skills/versions", "plugins", "preview"):
    (DATA / _d).mkdir(parents=True, exist_ok=True)

WORK_DIR = os.environ.get("CLAUDE_WORK_DIR", "D:\\")
FILE_ROOT = Path(os.environ.get("FILES_ROOT") or WORK_DIR).resolve()
WS_ROOT = (DATA / "workspaces").resolve()
LIBRARY = (DATA / "skills" / "library").resolve()
SUBMISSIONS = (DATA / "skills" / "submissions").resolve()
VERSIONS = (DATA / "skills" / "versions").resolve()
CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
PORT = int(os.environ.get("CENTER_PORT", "8080"))
DISCOVERY_PORT = int(os.environ.get("DISCOVERY_PORT", "8090"))

BOARD_USERNAME = "board"
TOKEN_IDLE_DAYS = 90


# ---------- 找 claude 命令 ----------

def _claude_candidates() -> list[str]:
    home = Path.home()
    env = os.environ.get
    if sys.platform == "win32":
        appdata = env("APPDATA") or str(home / "AppData" / "Roaming")
        local = env("LOCALAPPDATA") or str(home / "AppData" / "Local")
        pf = env("ProgramFiles") or r"C:\Program Files"
        pats = [
            str(home / ".local" / "bin" / "claude.exe"),            # 官方安装脚本
            str(Path(local) / "Programs" / "claude" / "claude.exe"),
            str(Path(local) / "Microsoft" / "WinGet" / "Links" / "claude.exe"),
            str(Path(appdata) / "npm" / "claude.cmd"),              # npm install -g
            str(Path(pf) / "nodejs" / "claude.cmd"),
            str(Path(env("NVM_SYMLINK") or pf + r"\nodejs") / "claude.cmd"),
            str(Path(env("NVM_HOME") or appdata + r"\nvm") / "*" / "claude.cmd"),
            str(Path(local) / "Volta" / "bin" / "claude.exe"),
            str(home / "scoop" / "shims" / "claude.exe"),
            str(home / ".bun" / "bin" / "claude.exe"),
        ]
    else:
        pats = [
            str(home / ".local" / "bin" / "claude"),
            str(home / ".claude" / "local" / "claude"),
            "/opt/homebrew/bin/claude", "/usr/local/bin/claude",
            str(home / ".npm-global" / "bin" / "claude"),
            str(home / ".nvm" / "versions" / "node" / "*" / "bin" / "claude"),
            str(home / ".volta" / "bin" / "claude"),
            str(home / ".bun" / "bin" / "claude"),
        ]
    return pats


def find_claude() -> str | None:
    """先看环境变量 CLAUDE_BIN 和 PATH；找不到再去常用安装位置找（开机自启时 PATH 常常不全）。"""
    if env := os.environ.get("CLAUDE_BIN"):
        return env if Path(env).is_file() else shutil.which(env)
    if found := shutil.which("claude"):
        return found
    for pat in _claude_candidates():
        hits = sorted(glob.glob(pat), reverse=True)  # nvm 多个版本时取版本号最大的
        for h in hits:
            if Path(h).is_file():
                return h
    return None


# ---------- 数据库 ----------

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
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE COLLATE NOCASE, display_name TEXT,
        pw_hash TEXT, role TEXT, can_bypass INTEGER DEFAULT 0, disabled INTEGER DEFAULT 0, created REAL);
    CREATE TABLE IF NOT EXISTS tokens (token TEXT PRIMARY KEY, user_id INTEGER, created REAL, last_used REAL);
    CREATE TABLE IF NOT EXISTS links (
        id TEXT PRIMARY KEY, session_id TEXT, user_id INTEGER, name TEXT, created REAL, manifest TEXT);
    CREATE TABLE IF NOT EXISTS library (
        name TEXT PRIMARY KEY, description TEXT, everyone INTEGER DEFAULT 0, submitted_by INTEGER, updated REAL);
    CREATE TABLE IF NOT EXISTS skill_access (name TEXT, user_id INTEGER, PRIMARY KEY (name, user_id));
    CREATE TABLE IF NOT EXISTS submissions (
        id TEXT PRIMARY KEY, name TEXT, description TEXT, status TEXT, submitted_by INTEGER,
        created REAL, reviewed_by INTEGER, reviewed_at REAL, note TEXT);
    CREATE TABLE IF NOT EXISTS skill_versions (
        name TEXT, version INTEGER, submission_id TEXT, submitted_by INTEGER, approved_by INTEGER,
        changelog TEXT, created REAL, PRIMARY KEY (name, version));
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
    """
)
for _table, _col in (("sessions", "bypass INTEGER DEFAULT 0"), ("sessions", "created_by TEXT DEFAULT ''"),
                     ("sessions", "user_id INTEGER"), ("sessions", "work_dir TEXT"), ("users", "avatar TEXT"),
                     ("library", "version INTEGER DEFAULT 1"), ("submissions", "changelog TEXT")):
    try:
        _db.execute(f"ALTER TABLE {_table} ADD COLUMN {_col}")
    except sqlite3.OperationalError:
        pass  # 列已存在


def db(sql: str, args=(), one=False):
    with _db_lock:
        cur = _db.execute(sql, args)
        _db.commit()
        rows = [dict(r) for r in cur.fetchall()]
    return (rows[0] if rows else None) if one else rows


def get_setting(key: str, default=None):
    row = db("SELECT value FROM settings WHERE key=?", (key,), one=True)
    return json.loads(row["value"]) if row else default


def set_setting(key: str, value) -> None:
    db("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))


# ---------- 账号 ----------

# 预设头像（图案在 static/ui.js 里），只能从这些里选，不能上传
AVATARS = ["orange", "lemon", "lime", "grapefruit", "peach", "cherry", "grape", "blueberry",
           "kiwi", "avocado", "watermelon", "pineapple", "strawberry", "coconut", "pear", "plum"]


def avatar_of(u: dict) -> str:
    if u.get("avatar") in AVATARS:
        return u["avatar"]
    if u.get("role") == "device":
        return "orange"  # 板子就是小橙子
    return AVATARS[(u.get("id") or 0) % len(AVATARS)]

def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
    except (ValueError, AttributeError):
        return False
    got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1)
    return secrets.compare_digest(got.hex(), h)


def user_public(u: dict) -> dict:
    out = {k: u[k] for k in ("id", "username", "display_name", "role", "can_bypass", "disabled", "created")}
    out["avatar"] = avatar_of(u)
    return out


def get_user(uid) -> dict | None:
    return db("SELECT * FROM users WHERE id=?", (uid,), one=True)


def board_user() -> dict:
    u = db("SELECT * FROM users WHERE username=?", (BOARD_USERNAME,), one=True)
    if not u:
        db("INSERT INTO users (username, display_name, pw_hash, role, created) VALUES (?,?,?,?,?)",
           (BOARD_USERNAME, "板子", "", "device", time.time()))
        u = db("SELECT * FROM users WHERE username=?", (BOARD_USERNAME,), one=True)
    return u


def first_admin() -> dict | None:
    return db("SELECT * FROM users WHERE role='admin' AND disabled=0 ORDER BY id LIMIT 1", one=True)


def adopt_orphans() -> None:
    """升级前没有账号的老会话：语音的给板子账号，其他给第一个管理员。"""
    db("UPDATE sessions SET user_id=? WHERE user_id IS NULL AND source='voice'", (board_user()["id"],))
    if admin := first_admin():
        db("UPDATE sessions SET user_id=? WHERE user_id IS NULL", (admin["id"],))


board_user()
adopt_orphans()


def _load_internal_token() -> str:
    """给 approval_mcp.py 和 claude_mcp.py 用的内部令牌，不给人用。"""
    path = DATA / "internal_token.txt"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(24), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


INTERNAL_TOKEN = _load_internal_token()
INTERNAL = {"id": 0, "username": "internal", "display_name": "系统", "role": "internal", "can_bypass": 0}


def issue_token(uid: int) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    db("INSERT INTO tokens (token, user_id, created, last_used) VALUES (?,?,?,?)", (token, uid, now, now))
    return token


def _token_from(request: Request) -> str:
    given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    return given or request.query_params.get("token", "")


def current_user(request: Request) -> dict:
    token = _token_from(request)
    if not token:
        raise HTTPException(401, "请先登录")
    if secrets.compare_digest(token, INTERNAL_TOKEN):
        return INTERNAL
    row = db("SELECT u.*, t.last_used FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?", (token,), one=True)
    now = time.time()
    if not row or row["disabled"] or row["role"] == "device" or now - row["last_used"] > TOKEN_IDLE_DAYS * 86400:
        raise HTTPException(401, "登录已失效，请重新登录")
    if now - row["last_used"] > 600:
        db("UPDATE tokens SET last_used=? WHERE token=?", (now, token))
    return row


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user


def require_internal(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "internal":
        raise HTTPException(403, "只供内部使用")
    return user


def is_admin(user: dict) -> bool:
    return user["role"] in ("admin", "internal")


def is_local(request: Request) -> bool:
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except (ValueError, AttributeError):
        return False


def can_see_session(user: dict, sess: dict) -> bool:
    return is_admin(user) or sess.get("user_id") == user["id"]


def jdump(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def posix_rule_path(p: Path) -> str:
    """Claude Code 权限规则里的绝对路径写法：Windows 的 D:\\a\\b 写成 //d/a/b。"""
    s = p.as_posix()
    if len(s) > 1 and s[1] == ":":
        s = "/" + s[0].lower() + s[2:]
    return "/" + s


def session_for(sid: str, user: dict) -> dict:
    """取会话并检查权限：只能看自己的，管理员能看所有人的。"""
    s = db("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
    if not s or not can_see_session(user, s):
        raise HTTPException(404, "会话不存在")
    return s


def session_workspace(sess: dict) -> Path:
    d = WS_ROOT / str(sess["user_id"]) / sess["id"]
    d.mkdir(parents=True, exist_ok=True)
    return d
