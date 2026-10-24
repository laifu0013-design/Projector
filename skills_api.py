"""技能库：用户提交 skill → 管理员审核入库 → 分配给用户 → 运行时只加载分配给这个人的。

运行时的做法：把这个人能用的 skill 复制成一个 Claude Code 插件（data/plugins/<用户id>/team），
用 --plugin-dir 加载；所以台式机 ~/.claude/skills 里的 skill 会对所有人生效，应该导入技能库后移走。
"""
import io
import json
import re
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from store import (CLAUDE_HOME, DATA, LIBRARY, SUBMISSIONS, current_user, db, is_admin, require_admin)

router = APIRouter()

MAX_SKILL_BYTES = 50 * 1024 * 1024
MAX_SKILL_FILES = 500
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
PLUGIN_NAME = "team"
_build_locks: dict[int, threading.Lock] = {}


# ---------- 工具函数 ----------

def parse_frontmatter(text: str) -> dict:
    text = text.lstrip("﻿")
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out = {}
    for line in text[3:end].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip("\"'")
    return out


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-")
    return s[:64] or "skill"


def _safe_parts(rel: str) -> list[str]:
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or ":" in p for p in parts):
        raise HTTPException(400, f"文件路径不对：{rel}")
    return parts


def _dir_stats(d: Path) -> tuple[int, int]:
    files = [p for p in d.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def read_files(d: Path, limit_each: int = 200_000) -> list[dict]:
    """审核用：列出 skill 里的所有文件，文本文件带内容。"""
    out = []
    for p in sorted(d.rglob("*")):
        if not p.is_file():
            continue
        item = {"path": p.relative_to(d).as_posix(), "size": p.stat().st_size, "text": None}
        raw = p.read_bytes()[:limit_each + 1]
        if b"\x00" not in raw[:8000]:
            try:
                item["text"] = raw[:limit_each].decode("utf-8")
                item["truncated"] = len(raw) > limit_each
            except UnicodeDecodeError:
                pass
        out.append(item)
    return out


def user_skill_names(user: dict) -> list[str]:
    if is_admin(user):
        return [r["name"] for r in db("SELECT name FROM library ORDER BY name")]
    rows = db("SELECT name FROM library WHERE everyone=1 UNION "
              "SELECT name FROM skill_access WHERE user_id=? ORDER BY name", (user["id"],))
    return [r["name"] for r in rows if (LIBRARY / r["name"]).is_dir()]


def plugin_dir_for(user: dict) -> Path | None:
    """把这个人能用的 skill 拼成一个插件目录；内容没变就不重建。没有可用 skill 时返回 None。"""
    names = user_skill_names(user)
    if not names:
        return None
    marks = {r["name"]: r["updated"] for r in db("SELECT name, updated FROM library")}
    sig = json.dumps([[n, marks.get(n)] for n in names])
    root = DATA / "plugins" / str(user["id"]) / PLUGIN_NAME
    lock = _build_locks.setdefault(user["id"], threading.Lock())
    with lock:
        sig_file = root.parent / "signature.json"
        if root.is_dir() and sig_file.exists() and sig_file.read_text(encoding="utf-8") == sig:
            return root
        shutil.rmtree(root, ignore_errors=True)
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(
            {"name": PLUGIN_NAME, "version": "1.0.0", "description": "团队技能库（管理员分配）"}, ensure_ascii=False),
            encoding="utf-8")
        for n in names:
            shutil.copytree(LIBRARY / n, root / "skills" / n)
        sig_file.write_text(sig, encoding="utf-8")
    return root


def _normalize(d: Path) -> Path:
    """允许 SKILL.md 在压缩包根目录，或者在唯一的一层子文件夹里；后一种情况把内容提上来。"""
    for junk in ("__MACOSX", ".DS_Store"):
        p = d / junk
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
    if (d / "SKILL.md").is_file():
        return d
    kids = [p for p in d.iterdir()]
    if len(kids) == 1 and kids[0].is_dir() and (kids[0] / "SKILL.md").is_file():
        inner = kids[0]
        tmp = d.with_name(d.name + ".tmp")
        inner.rename(tmp)
        d.rmdir()
        tmp.rename(d)
        return d
    raise HTTPException(400, "没找到 SKILL.md：skill 文件夹里必须有 SKILL.md")


# ---------- 提交 ----------

def _submission(sub_id: str, user: dict, need_draft: bool = False) -> dict:
    s = db("SELECT * FROM submissions WHERE id=?", (sub_id,), one=True)
    if not s or (not is_admin(user) and s["submitted_by"] != user["id"]):
        raise HTTPException(404, "找不到这个提交")
    if need_draft and s["status"] != "draft":
        raise HTTPException(409, "已经提交过了，不能再改")
    return s


@router.get("/api/skills/mine")
def skills_mine(user: dict = Depends(current_user)):
    names = set(user_skill_names(user))
    return [r for r in db("SELECT name, description FROM library ORDER BY name") if r["name"] in names]


@router.get("/api/skills/submissions")
def submissions_list(user: dict = Depends(current_user)):
    sql = ("SELECT s.*, u.display_name AS submitter FROM submissions s LEFT JOIN users u ON u.id=s.submitted_by "
           "WHERE s.status != 'draft'")
    if is_admin(user):
        return db(sql + " ORDER BY s.created DESC")
    return db(sql + " AND s.submitted_by=? ORDER BY s.created DESC", (user["id"],))


@router.post("/api/skills/submissions")
def submissions_create(user: dict = Depends(current_user)):
    sub_id = uuid.uuid4().hex[:10]
    (SUBMISSIONS / sub_id).mkdir(parents=True)
    db("INSERT INTO submissions (id, status, submitted_by, created) VALUES (?,?,?,?)",
       (sub_id, "draft", user["id"], time.time()))
    return {"id": sub_id}


@router.put("/api/skills/submissions/{sub_id}/file")
async def submissions_file(sub_id: str, path: str, request: Request, user: dict = Depends(current_user)):
    _submission(sub_id, user, need_draft=True)
    d = SUBMISSIONS / sub_id
    data = await request.body()
    n, total = _dir_stats(d)
    if n + 1 > MAX_SKILL_FILES or total + len(data) > MAX_SKILL_BYTES:
        raise HTTPException(413, "skill 太大了（最多 500 个文件、50 MB）")
    dest = d.joinpath(*_safe_parts(path))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return {"ok": True}


@router.put("/api/skills/submissions/{sub_id}/zip")
async def submissions_zip(sub_id: str, request: Request, user: dict = Depends(current_user)):
    _submission(sub_id, user, need_draft=True)
    d = SUBMISSIONS / sub_id
    data = await request.body()
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(400, "不是有效的 zip 文件")
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_SKILL_FILES or sum(i.file_size for i in infos) > MAX_SKILL_BYTES:
        raise HTTPException(413, "skill 太大了（最多 500 个文件、50 MB）")
    for info in infos:
        name = info.filename
        if info.flag_bits & 0x800 == 0:  # Windows 自带压缩的中文文件名是 GBK
            try:
                name = name.encode("cp437").decode("gbk")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
        dest = d.joinpath(*_safe_parts(name))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(zf.read(info))
    return {"ok": True}


@router.post("/api/skills/submissions/{sub_id}/submit")
def submissions_submit(sub_id: str, user: dict = Depends(current_user)):
    _submission(sub_id, user, need_draft=True)
    d = _normalize(SUBMISSIONS / sub_id)
    meta = parse_frontmatter((d / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
    name = meta.get("name") or ""
    if not NAME_RE.match(name):
        name = slugify(name)
    desc = meta.get("description", "")[:500]
    if not desc:
        raise HTTPException(400, "SKILL.md 开头要写 description（什么时候用这个 skill），否则 Claude 不知道何时调用")
    db("UPDATE submissions SET status='pending', name=?, description=?, created=? WHERE id=?",
       (name, desc, time.time(), sub_id))
    return db("SELECT * FROM submissions WHERE id=?", (sub_id,), one=True)


@router.get("/api/skills/submissions/{sub_id}/files")
def submissions_files(sub_id: str, user: dict = Depends(current_user)):
    s = _submission(sub_id, user)
    exists = bool(s["name"] and db("SELECT 1 FROM library WHERE name=?", (s["name"],), one=True))
    return {"submission": s, "updates_existing": exists, "files": read_files(SUBMISSIONS / sub_id)}


class Review(BaseModel):
    approve: bool
    note: str = ""


@router.post("/api/skills/submissions/{sub_id}/review")
def submissions_review(sub_id: str, body: Review, admin: dict = Depends(require_admin)):
    s = _submission(sub_id, admin)
    if s["status"] != "pending":
        raise HTTPException(409, "这个提交不是待审核状态")
    now = time.time()
    if body.approve:
        dest = LIBRARY / s["name"]
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(SUBMISSIONS / sub_id, dest)
        if db("SELECT 1 FROM library WHERE name=?", (s["name"],), one=True):
            db("UPDATE library SET description=?, submitted_by=?, updated=? WHERE name=?",
               (s["description"], s["submitted_by"], now, s["name"]))
        else:
            db("INSERT INTO library (name, description, everyone, submitted_by, updated) VALUES (?,?,0,?,?)",
               (s["name"], s["description"], s["submitted_by"], now))
            # 提交人默认能用自己提交的 skill
            db("INSERT OR IGNORE INTO skill_access (name, user_id) VALUES (?,?)", (s["name"], s["submitted_by"]))
    db("UPDATE submissions SET status=?, reviewed_by=?, reviewed_at=?, note=? WHERE id=?",
       ("approved" if body.approve else "rejected", admin["id"], now, body.note[:500], sub_id))
    return {"ok": True}


@router.delete("/api/skills/submissions/{sub_id}")
def submissions_delete(sub_id: str, user: dict = Depends(current_user)):
    s = _submission(sub_id, user)
    if s["status"] == "approved" and not is_admin(user):
        raise HTTPException(409, "已入库的提交只有管理员能删除")
    shutil.rmtree(SUBMISSIONS / sub_id, ignore_errors=True)
    db("DELETE FROM submissions WHERE id=?", (sub_id,))
    return {"ok": True}


# ---------- 技能库管理（管理员） ----------

@router.get("/api/admin/library")
def library_list(admin: dict = Depends(require_admin)):
    rows = db("SELECT l.*, u.display_name AS submitter FROM library l LEFT JOIN users u ON u.id=l.submitted_by "
              "ORDER BY l.name")
    access = {}
    for a in db("SELECT name, user_id FROM skill_access"):
        access.setdefault(a["name"], []).append(a["user_id"])
    for r in rows:
        r["users"] = access.get(r["name"], [])
    return rows


@router.get("/api/admin/library/{name}/files")
def library_files(name: str, admin: dict = Depends(require_admin)):
    if not NAME_RE.match(name) or not (LIBRARY / name).is_dir():
        raise HTTPException(404, "技能库里没有这个 skill")
    return {"files": read_files(LIBRARY / name)}


class Access(BaseModel):
    everyone: bool
    users: list[int] = []


@router.put("/api/admin/library/{name}/access")
def library_access(name: str, body: Access, admin: dict = Depends(require_admin)):
    if not db("SELECT 1 FROM library WHERE name=?", (name,), one=True):
        raise HTTPException(404, "技能库里没有这个 skill")
    db("UPDATE library SET everyone=? WHERE name=?", (int(body.everyone), name))
    db("DELETE FROM skill_access WHERE name=?", (name,))
    for uid in set(body.users):
        db("INSERT INTO skill_access (name, user_id) VALUES (?,?)", (name, uid))
    return {"ok": True}


@router.delete("/api/admin/library/{name}")
def library_delete(name: str, admin: dict = Depends(require_admin)):
    if not NAME_RE.match(name):
        raise HTTPException(400, "名字不对")
    shutil.rmtree(LIBRARY / name, ignore_errors=True)
    db("DELETE FROM library WHERE name=?", (name,))
    db("DELETE FROM skill_access WHERE name=?", (name,))
    return {"ok": True}


def _local_skills_dir() -> Path:
    return CLAUDE_HOME / "skills"


@router.get("/api/admin/local-skills")
def local_skills(admin: dict = Depends(require_admin)):
    """台式机 ~/.claude/skills 里的 skill：它们对所有人都生效，建议导入技能库后移走。"""
    d = _local_skills_dir()
    out = []
    if d.is_dir():
        for p in sorted(d.iterdir()):
            if (p / "SKILL.md").is_file():
                meta = parse_frontmatter((p / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
                out.append({"name": p.name, "description": meta.get("description", "")[:300],
                            "in_library": bool(db("SELECT 1 FROM library WHERE name=?", (slugify(p.name),), one=True))})
    return {"dir": str(d), "skills": out}


class ImportReq(BaseModel):
    names: list[str]


@router.post("/api/admin/local-skills/import")
def local_skills_import(body: ImportReq, admin: dict = Depends(require_admin)):
    d = _local_skills_dir()
    done = []
    for n in body.names:
        src = d / n
        if "/" in n or "\\" in n or n.startswith(".") or not (src / "SKILL.md").is_file():
            continue
        meta = parse_frontmatter((src / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
        name = slugify(meta.get("name") or n)
        shutil.rmtree(LIBRARY / name, ignore_errors=True)
        shutil.copytree(src, LIBRARY / name)
        db("INSERT OR REPLACE INTO library (name, description, everyone, submitted_by, updated) VALUES "
           "(?,?,COALESCE((SELECT everyone FROM library WHERE name=?),0),?,?)",
           (name, meta.get("description", "")[:500], name, admin["id"], time.time()))
        done.append(name)
    return {"imported": done}
