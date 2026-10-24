"""技能库：用户提交 skill → 管理员审核入库 → 分配给用户 → 运行时只加载分配给这个人的。

每次审核通过都记成一个新版本（v1、v2……），文件另存一份在 data/skills/versions/<名字>/<版本>/，可以回滚。
更新技能有两条路：在网页里上传新版本；或者在聊天里让 Claude 用 skill_checkout 取出、改完用 skill_submit 提交。

运行时的做法：把这个人能用的 skill 复制成一个 Claude Code 插件（data/plugins/<用户id>/team），
用 --plugin-dir 加载；所以台式机 ~/.claude/skills 里的 skill 会对所有人生效，应该导入技能库后移走。
"""
import hashlib
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

from store import (CLAUDE_HOME, DATA, LIBRARY, SUBMISSIONS, VERSIONS, current_user, db, get_user, is_admin,
                   require_admin, require_internal, session_workspace)

router = APIRouter()

MAX_SKILL_BYTES = 50 * 1024 * 1024
MAX_SKILL_FILES = 500
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
PLUGIN_NAME = "team"
DRAFTS = "技能草稿"
_build_locks: dict[int, threading.Lock] = {}
_publish_lock = threading.Lock()


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
    """审核和查看用：列出 skill 里的所有文件，文本文件带内容。"""
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


def _digest(d: Path) -> dict:
    if not d.is_dir():
        return {}
    return {p.relative_to(d).as_posix(): hashlib.sha1(p.read_bytes()).hexdigest() for p in d.rglob("*") if p.is_file()}


def diff_dirs(old: Path, new: Path) -> dict:
    """新版本相对旧版本：哪些文件新增、修改、删除。"""
    a, b = _digest(old), _digest(new)
    return {"added": sorted(set(b) - set(a)), "removed": sorted(set(a) - set(b)),
            "modified": sorted(k for k in set(a) & set(b) if a[k] != b[k])}


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
    """允许 SKILL.md 在根目录，或者在唯一的一层子文件夹里；后一种情况把内容提上来。"""
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


def publish_version(name: str, src: Path, description: str, submitted_by, approved_by, changelog: str,
                    submission_id: str | None = None) -> int:
    """把一份 skill 文件发布成技能库里的新版本，返回版本号。"""
    with _publish_lock:
        row = db("SELECT version FROM library WHERE name=?", (name,), one=True)
        last = db("SELECT MAX(version) AS v FROM skill_versions WHERE name=?", (name,), one=True)["v"] or 0
        version = max(last, (row or {}).get("version") or 0) + 1 if (row or last) else 1
        vdir = VERSIONS / name / str(version)
        shutil.rmtree(vdir, ignore_errors=True)
        shutil.copytree(src, vdir)
        shutil.rmtree(LIBRARY / name, ignore_errors=True)
        shutil.copytree(src, LIBRARY / name)
        now = time.time()
        if row:
            db("UPDATE library SET description=?, version=?, updated=? WHERE name=?", (description, version, now, name))
        else:
            db("INSERT INTO library (name, description, everyone, submitted_by, updated, version) VALUES (?,?,0,?,?,?)",
               (name, description, submitted_by, now, version))
            if submitted_by:  # 提交人默认能用自己提交的 skill
                db("INSERT OR IGNORE INTO skill_access (name, user_id) VALUES (?,?)", (name, submitted_by))
        db("INSERT OR REPLACE INTO skill_versions (name, version, submission_id, submitted_by, approved_by, changelog, created) "
           "VALUES (?,?,?,?,?,?,?)", (name, version, submission_id, submitted_by, approved_by, changelog[:1000], now))
    return version


def _migrate_versions() -> None:
    """老版本升级上来：技能库里已有但没有版本记录的，补一个 v1。"""
    for r in db("SELECT * FROM library"):
        if not db("SELECT 1 FROM skill_versions WHERE name=?", (r["name"],), one=True) and (LIBRARY / r["name"]).is_dir():
            vdir = VERSIONS / r["name"] / "1"
            if not vdir.exists():
                shutil.copytree(LIBRARY / r["name"], vdir)
            db("INSERT OR IGNORE INTO skill_versions (name, version, submitted_by, changelog, created) VALUES (?,?,?,?,?)",
               (r["name"], 1, r["submitted_by"], "初始版本", r["updated"] or time.time()))
            db("UPDATE library SET version=1 WHERE name=?", (r["name"],))


_migrate_versions()


def can_use(user: dict, name: str) -> bool:
    return name in user_skill_names(user)


# ---------- 提交 ----------

def _submission(sub_id: str, user: dict, need_draft: bool = False) -> dict:
    s = db("SELECT * FROM submissions WHERE id=?", (sub_id,), one=True)
    if not s or (not is_admin(user) and s["submitted_by"] != user["id"]):
        raise HTTPException(404, "找不到这个提交")
    if need_draft and s["status"] != "draft":
        raise HTTPException(409, "已经提交过了，不能再改")
    return s


def _finalize(sub_id: str, changelog: str) -> dict:
    """检查 SKILL.md、读出名字和说明，把草稿变成待审核。"""
    d = _normalize(SUBMISSIONS / sub_id)
    n, total = _dir_stats(d)
    if n > MAX_SKILL_FILES or total > MAX_SKILL_BYTES:
        raise HTTPException(413, "skill 太大了（最多 500 个文件、50 MB）")
    meta = parse_frontmatter((d / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
    name = meta.get("name") or ""
    if not NAME_RE.match(name):
        name = slugify(name)
    desc = meta.get("description", "")[:500]
    if not desc:
        raise HTTPException(400, "SKILL.md 开头要写 description（什么时候用这个 skill），否则 Claude 不知道何时调用")
    db("UPDATE submissions SET status='pending', name=?, description=?, created=?, changelog=? WHERE id=?",
       (name, desc, time.time(), changelog.strip()[:1000], sub_id))
    return db("SELECT * FROM submissions WHERE id=?", (sub_id,), one=True)


@router.get("/api/skills/mine")
def skills_mine(user: dict = Depends(current_user)):
    names = set(user_skill_names(user))
    return [r for r in db("SELECT name, description, version, updated FROM library ORDER BY name") if r["name"] in names]


@router.get("/api/skills/submissions")
def submissions_list(user: dict = Depends(current_user)):
    sql = ("SELECT s.*, u.display_name AS submitter, l.version AS current_version FROM submissions s "
           "LEFT JOIN users u ON u.id=s.submitted_by LEFT JOIN library l ON l.name=s.name WHERE s.status != 'draft'")
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


class SubmitBody(BaseModel):
    changelog: str = ""


@router.post("/api/skills/submissions/{sub_id}/submit")
def submissions_submit(sub_id: str, body: SubmitBody | None = None, user: dict = Depends(current_user)):
    _submission(sub_id, user, need_draft=True)
    return _finalize(sub_id, (body.changelog if body else "") or "")


@router.get("/api/skills/submissions/{sub_id}/files")
def submissions_files(sub_id: str, user: dict = Depends(current_user)):
    s = _submission(sub_id, user)
    lib = db("SELECT version FROM library WHERE name=?", (s["name"],), one=True) if s["name"] else None
    changes = diff_dirs(LIBRARY / s["name"], SUBMISSIONS / sub_id) if lib else None
    return {"submission": s, "updates_existing": bool(lib), "current_version": (lib or {}).get("version"),
            "changes": changes, "files": read_files(SUBMISSIONS / sub_id)}


class Review(BaseModel):
    approve: bool
    note: str = ""


@router.post("/api/skills/submissions/{sub_id}/review")
def submissions_review(sub_id: str, body: Review, admin: dict = Depends(require_admin)):
    s = _submission(sub_id, admin)
    if s["status"] != "pending":
        raise HTTPException(409, "这个提交不是待审核状态")
    version = None
    if body.approve:
        version = publish_version(s["name"], SUBMISSIONS / sub_id, s["description"], s["submitted_by"], admin["id"],
                                  s["changelog"] or "", sub_id)
    db("UPDATE submissions SET status=?, reviewed_by=?, reviewed_at=?, note=? WHERE id=?",
       ("approved" if body.approve else "rejected", admin["id"], time.time(), body.note[:500], sub_id))
    return {"ok": True, "version": version}


@router.delete("/api/skills/submissions/{sub_id}")
def submissions_delete(sub_id: str, user: dict = Depends(current_user)):
    s = _submission(sub_id, user)
    if s["status"] == "approved" and not is_admin(user):
        raise HTTPException(409, "已入库的提交只有管理员能删除")
    shutil.rmtree(SUBMISSIONS / sub_id, ignore_errors=True)
    db("DELETE FROM submissions WHERE id=?", (sub_id,))
    return {"ok": True}


# ---------- 技能详情和版本记录（能用这个技能的人都能看） ----------

def _check_name(name: str, user: dict) -> dict:
    row = db("SELECT * FROM library WHERE name=?", (name,), one=True) if NAME_RE.match(name) else None
    if not row or not can_use(user, name):
        raise HTTPException(404, "技能库里没有这个技能，或者没分配给你")
    return row


@router.get("/api/skills/{name}")
def skill_detail(name: str, user: dict = Depends(current_user)):
    row = _check_name(name, user)
    versions = db("SELECT v.*, u.display_name AS submitter, a.display_name AS approver, u.avatar AS submitter_avatar_raw, "
                  "u.id AS uid, u.role AS urole FROM skill_versions v LEFT JOIN users u ON u.id=v.submitted_by "
                  "LEFT JOIN users a ON a.id=v.approved_by WHERE v.name=? ORDER BY v.version DESC", (name,))
    from store import avatar_of
    for v in versions:
        v["submitter_avatar"] = avatar_of({"avatar": v.pop("submitter_avatar_raw"), "id": v.pop("uid"), "role": v.pop("urole")})
    pending = db("SELECT s.id, s.changelog, s.created, u.display_name AS submitter FROM submissions s "
                 "LEFT JOIN users u ON u.id=s.submitted_by WHERE s.name=? AND s.status='pending' ORDER BY s.created", (name,))
    return {"skill": row, "versions": versions, "pending": pending}


@router.get("/api/skills/{name}/versions/{version}/files")
def skill_version_files(name: str, version: int, user: dict = Depends(current_user)):
    _check_name(name, user)
    d = VERSIONS / name / str(version)
    if not d.is_dir():
        raise HTTPException(404, "没有这个版本")
    prev = VERSIONS / name / str(version - 1)
    return {"files": read_files(d), "changes": diff_dirs(prev, d) if prev.is_dir() else None}


class Rollback(BaseModel):
    version: int


@router.post("/api/admin/library/{name}/rollback")
def library_rollback(name: str, body: Rollback, admin: dict = Depends(require_admin)):
    row = _check_name(name, admin)
    d = VERSIONS / name / str(body.version)
    if not d.is_dir():
        raise HTTPException(404, "没有这个版本")
    meta = parse_frontmatter((d / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
    v = publish_version(name, d, meta.get("description", row["description"])[:500], admin["id"], admin["id"],
                        f"回滚到 v{body.version}")
    return {"ok": True, "version": v}


# ---------- 技能库管理（管理员） ----------

@router.get("/api/admin/library")
def library_list(admin: dict = Depends(require_admin)):
    rows = db("SELECT l.*, u.display_name AS submitter FROM library l LEFT JOIN users u ON u.id=l.submitted_by "
              "ORDER BY l.name")
    access = {}
    for a in db("SELECT name, user_id FROM skill_access"):
        access.setdefault(a["name"], []).append(a["user_id"])
    pending = {r["name"]: r["n"] for r in db("SELECT name, COUNT(*) AS n FROM submissions WHERE status='pending' GROUP BY name")}
    for r in rows:
        r["users"] = access.get(r["name"], [])
        r["pending"] = pending.get(r["name"], 0)
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
    shutil.rmtree(VERSIONS / name, ignore_errors=True)
    for sql in ("DELETE FROM library WHERE name=?", "DELETE FROM skill_access WHERE name=?",
                "DELETE FROM skill_versions WHERE name=?"):
        db(sql, (name,))
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
        publish_version(name, src, meta.get("description", "")[:500], admin["id"], admin["id"], "从台式机本机导入")
        done.append(name)
    return {"imported": done}


# ---------- 给 Claude 用（approval_mcp.py 里的 skill_* 工具）：在聊天里取出、修改、提交技能 ----------

class InternalReq(BaseModel):
    session_id: str
    name: str = ""
    path: str = ""
    changelog: str = ""


def _session_owner(sid: str) -> tuple[dict, dict]:
    sess = db("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
    owner = get_user(sess["user_id"]) if sess else None
    if not sess or not owner:
        raise HTTPException(404, "会话不存在")
    return sess, owner


@router.post("/api/internal/skills/list")
def internal_list(body: InternalReq, _: dict = Depends(require_internal)):
    _, owner = _session_owner(body.session_id)
    names = set(user_skill_names(owner))
    return {"skills": [{"name": r["name"], "version": r["version"], "description": r["description"]}
                       for r in db("SELECT * FROM library ORDER BY name") if r["name"] in names]}


@router.post("/api/internal/skills/checkout")
def internal_checkout(body: InternalReq, _: dict = Depends(require_internal)):
    sess, owner = _session_owner(body.session_id)
    row = db("SELECT * FROM library WHERE name=?", (body.name,), one=True) if NAME_RE.match(body.name) else None
    if not row or not can_use(owner, body.name):
        raise HTTPException(404, f"{owner['display_name']} 没有技能 {body.name} 的使用权限，或者技能库里没有它")
    dest = session_workspace(sess) / DRAFTS / body.name
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(LIBRARY / body.name, dest)
    return {"path": str(dest), "version": row["version"],
            "files": [p.relative_to(dest).as_posix() for p in sorted(dest.rglob("*")) if p.is_file()]}


@router.post("/api/internal/skills/draft-dir")
def internal_draft_dir(body: InternalReq, _: dict = Depends(require_internal)):
    sess, _owner = _session_owner(body.session_id)
    d = session_workspace(sess) / DRAFTS
    d.mkdir(parents=True, exist_ok=True)
    return {"path": str(d)}


@router.post("/api/internal/skills/submit")
def internal_submit(body: InternalReq, _: dict = Depends(require_internal)):
    sess, owner = _session_owner(body.session_id)
    ws = session_workspace(sess).resolve()
    src = Path(body.path).resolve()
    if not src.is_dir() or not (src == ws or src.is_relative_to(ws)):
        raise HTTPException(400, "只能提交这个会话工作区里的文件夹（先用 skill_checkout 取出，或在技能草稿文件夹里新建）")
    if not body.changelog.strip():
        raise HTTPException(400, "请写更新说明（changelog），管理员审核时要看")
    sub_id = uuid.uuid4().hex[:10]
    shutil.copytree(src, SUBMISSIONS / sub_id)
    db("INSERT INTO submissions (id, status, submitted_by, created) VALUES (?,?,?,?)",
       (sub_id, "draft", owner["id"], time.time()))
    try:
        s = _finalize(sub_id, body.changelog)
    except HTTPException:
        shutil.rmtree(SUBMISSIONS / sub_id, ignore_errors=True)
        db("DELETE FROM submissions WHERE id=?", (sub_id,))
        raise
    lib = db("SELECT version FROM library WHERE name=?", (s["name"],), one=True)
    changes = diff_dirs(LIBRARY / s["name"], SUBMISSIONS / sub_id) if lib else None
    return {"name": s["name"], "submission_id": sub_id, "updates_version": (lib or {}).get("version"), "changes": changes}
