"""公共部分：配置、数据库、账号和登录校验。server.py、files_api.py、skills_api.py 都从这里取。"""
import glob
import hashlib
import ipaddress
import json
import os
import re
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
# 每个人的工作区放在文件库（D 盘）里，文件夹名一看就知道是谁的哪个会话：
#   D:\Projector工作区\张三(zhang)\2026-10-24 九月采购合同汇总\
OLD_WS = (DATA / "workspaces").resolve()
if os.environ.get("WORKSPACE_ROOT"):
    WS_ROOT = Path(os.environ["WORKSPACE_ROOT"])
elif FILE_ROOT.is_dir():
    WS_ROOT = FILE_ROOT / "Projector工作区"
else:
    WS_ROOT = OLD_WS
WS_ROOT.mkdir(parents=True, exist_ok=True)
WS_ROOT = WS_ROOT.resolve()
TRASH = WS_ROOT / "回收站"
TRASH_DAYS = 30
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
    CREATE TABLE IF NOT EXISTS trash (
        id TEXT PRIMARY KEY, name TEXT, orig_path TEXT, trash_path TEXT, is_dir INTEGER, size INTEGER,
        deleted_by INTEGER, deleted_at REAL, reason TEXT);
    CREATE TABLE IF NOT EXISTS file_ops (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT, path TEXT, detail TEXT, created REAL);
    """
)
for _table, _col in (("sessions", "bypass INTEGER DEFAULT 0"), ("sessions", "created_by TEXT DEFAULT ''"),
                     ("sessions", "user_id INTEGER"), ("sessions", "work_dir TEXT"), ("users", "avatar TEXT"),
                     ("library", "version INTEGER DEFAULT 1"), ("submissions", "changelog TEXT"),
                     ("users", "ws_name TEXT"), ("sessions", "ws_dir TEXT")):
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


_ws_lock = threading.Lock()


def safe_name(name: str, limit: int = 60) -> str:
    """能当 Windows 文件夹名的字符串"""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "").strip(" .")
    return name[:limit].strip(" .") or "未命名"


def user_ws_dir(u: dict, create: bool = True) -> Path:
    """某个人的工作区文件夹：显示名(用户名)，第一次用到时定下来，之后改显示名也不变"""
    with _ws_lock:
        name = (db("SELECT ws_name FROM users WHERE id=?", (u["id"],), one=True) or {}).get("ws_name")
        if not name:
            base = safe_name(f"{u['display_name']}({u['username']})")
            name, n = base, 2
            while (WS_ROOT / name).exists() or db("SELECT 1 FROM users WHERE ws_name=?", (name,), one=True):
                name, n = f"{base}-{n}", n + 1
            db("UPDATE users SET ws_name=? WHERE id=?", (name, u["id"]))
    d = WS_ROOT / name
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def ws_owner(folder: str) -> int | None:
    """工作区第一层文件夹名 → 用户 id"""
    row = db("SELECT id FROM users WHERE ws_name=?", (folder,), one=True)
    return row["id"] if row else None


def session_workspace(sess: dict, create: bool = True) -> Path | None:
    """会话的工作区文件夹：日期 会话标题；第一次用到时才建，之后会话改名也不变（免得路径失效）"""
    row = db("SELECT id, user_id, title, created, ws_dir FROM sessions WHERE id=?", (sess["id"],), one=True) or sess
    owner = get_user(row["user_id"])
    if not owner:
        return None
    base = user_ws_dir(owner, create)
    name = row.get("ws_dir")
    if not name:
        if not create:
            return None
        with _ws_lock:
            name = (db("SELECT ws_dir FROM sessions WHERE id=?", (row["id"],), one=True) or {}).get("ws_dir")
            if not name:
                day = time.strftime("%Y-%m-%d", time.localtime(row.get("created") or time.time()))
                stem = safe_name(f"{day} {row.get('title') or '会话'}")
                name, n = stem, 2
                while (base / name).exists():
                    name, n = f"{stem} ({n})", n + 1
                db("UPDATE sessions SET ws_dir=? WHERE id=?", (name, row["id"]))
    d = base / name
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _migrate_workspaces() -> None:
    """老版本的工作区（data/workspaces/用户id/会话id）搬到新位置"""
    if OLD_WS == WS_ROOT or not OLD_WS.is_dir():
        return
    import shutil
    for udir in OLD_WS.iterdir():
        if not (udir.is_dir() and udir.name.isdigit()):
            continue
        for sdir in udir.iterdir():
            sess = db("SELECT * FROM sessions WHERE id=?", (sdir.name,), one=True)
            if not sess or not sdir.is_dir():
                continue
            dest = session_workspace(sess)
            for child in sdir.iterdir():
                target = dest / child.name
                if not target.exists():
                    shutil.move(str(child), str(target))
            try:
                sdir.rmdir()
            except OSError:
                pass
        try:
            udir.rmdir()  # 搬空了就删掉
        except OSError:
            pass


def log_file_op(user: dict, action: str, path, detail: str = "") -> None:
    db("INSERT INTO file_ops (user_id, action, path, detail, created) VALUES (?,?,?,?,?)",
       (user.get("id"), action, str(path), detail[:500], time.time()))


_migrate_workspaces()
