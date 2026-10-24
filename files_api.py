"""文件浏览、预览，以及每个人的工作区（同事本机文件上传到这里给 Claude 处理，再同步回去）。

路径写法（网页和接口之间）：
  "合同/2026/a.docx"   工作目录（D 盘）里的相对路径
  "ws:<会话>/报告/a.xlsx"  自己的工作区；管理员可以用 "ws<用户id>:" 看别人的
  也接受 Claude 回复里出现的完整路径（D:\\合同\\a.docx），会自动换算
"""
import asyncio
import hashlib
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from store import (DATA, FILE_ROOT, WS_ROOT, current_user, db, is_admin, jdump, session_for,
                   session_workspace)

router = APIRouter()

SOFFICE = os.environ.get("SOFFICE") or shutil.which("soffice") or next(
    (p for p in (r"C:\Program Files\LibreOffice\program\soffice.exe",
                 r"C:\Program Files (x86)\LibreOffice\program\soffice.exe") if Path(p).is_file()), None)
HIDDEN_NAMES = {"$recycle.bin", "system volume information", "pagefile.sys", "swapfile.sys", "hiberfil.sys",
                "dumpstack.log.tmp", "config.msi", "recovery"}
TEXT_LIMIT = 1024 * 1024
MAX_UPLOAD = 1024 ** 3          # 单个文件最大 1 GB
_soffice_lock = asyncio.Lock()
_WS_RE = re.compile(r"^ws(\d*):(.*)$", re.S)


# ---------- 路径换算和权限 ----------

def _within(p: Path, root: Path) -> bool:
    return p == root or p.is_relative_to(root)


def _check(target: Path, user: dict) -> Path:
    """数据目录（聊天记录库、别人的工作区）不让碰；工作区只能看自己的。"""
    if _within(target, WS_ROOT):
        rel = target.relative_to(WS_ROOT).parts
        if not is_admin(user) and (not rel or rel[0] != str(user["id"])):
            raise HTTPException(403, "只能访问自己的工作区")
        return target
    if _within(target, DATA.resolve()):
        raise HTTPException(403, "这里是系统数据，不能访问")
    if not _within(target, FILE_ROOT):
        raise HTTPException(403, "只能访问工作目录里的文件")
    return target


def resolve_path(path: str, user: dict, must_exist: bool = True) -> Path:
    raw = (path or "").strip().strip('"')
    if m := _WS_RE.match(raw):
        uid = m.group(1) or str(user["id"])
        target = (WS_ROOT / uid / m.group(2).lstrip("/\\")).resolve()
    elif Path(raw).is_absolute():
        target = Path(raw).resolve()
    else:
        target = (FILE_ROOT / raw.lstrip("/\\")).resolve()
    _check(target, user)
    if must_exist and not target.exists():
        raise HTTPException(404, "文件不存在")
    return target


def to_virtual(p: Path, user: dict) -> str:
    if _within(p, WS_ROOT):
        parts = p.relative_to(WS_ROOT).parts
        uid, rest = (parts[0] if parts else str(user["id"])), "/".join(parts[1:])
        return f"ws:{rest}" if uid == str(user["id"]) else f"ws{uid}:{rest}"
    return "" if p == FILE_ROOT else p.relative_to(FILE_ROOT).as_posix()


def _session_titles(uid: str) -> dict:
    return {s["id"]: s["title"] for s in db("SELECT id, title FROM sessions WHERE user_id=?", (uid,))}


def crumbs_for(p: Path, user: dict) -> list[dict]:
    if _within(p, WS_ROOT):
        parts = p.relative_to(WS_ROOT).parts
        uid = parts[0] if parts else str(user["id"])
        prefix = "ws:" if uid == str(user["id"]) else f"ws{uid}:"
        owner = "" if uid == str(user["id"]) else ((db("SELECT display_name FROM users WHERE id=?", (uid,), one=True)
                                                     or {}).get("display_name", uid) + " 的")
        out = [{"label": owner + "工作区", "path": prefix}]
        titles = _session_titles(uid)
        for i, part in enumerate(parts[1:], start=1):
            out.append({"label": titles.get(part, part) if i == 1 else part, "path": prefix + "/".join(parts[1:i + 1])})
        return out
    out = [{"label": str(FILE_ROOT), "path": ""}]
    if p != FILE_ROOT:
        parts = p.relative_to(FILE_ROOT).parts
        for i, part in enumerate(parts):
            out.append({"label": part, "path": "/".join(parts[:i + 1])})
    return out


def _hidden(entry: os.DirEntry) -> bool:
    if entry.name.lower() in HIDDEN_NAMES or entry.name.startswith("~$"):
        return True
    attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attrs & 0x6)  # Windows 的隐藏、系统属性


def entry_info(p: Path, user: dict, st=None, name=None) -> dict:
    st = st or p.stat()
    is_dir = p.is_dir()
    return {"name": name or p.name, "dir": is_dir, "size": 0 if is_dir else st.st_size, "mtime": st.st_mtime,
            "path": to_virtual(p, user), "abs": str(p)}


def list_dir(d: Path, user: dict, limit: int = 3000) -> list[dict]:
    data_dir = DATA.resolve()
    titles = None
    if d.parent == WS_ROOT:  # 工作区第一层是各个会话的文件夹，显示会话标题
        titles = _session_titles(d.name)
    items = []
    try:
        with os.scandir(d) as it:
            for e in it:
                try:
                    if _hidden(e):
                        continue
                    p = Path(e.path)
                    if e.name.lower() == "data" and p.resolve() == data_dir:
                        continue  # 项目在 D 盘里时，不显示系统数据文件夹
                    items.append(entry_info(p, user, e.stat(), titles.get(e.name, e.name) if titles else None))
                except OSError:
                    continue  # 没权限或已删除
                if len(items) >= limit:
                    break
    except PermissionError:
        raise HTTPException(403, "没有权限打开这个文件夹")
    items.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    return items


# ---------- 浏览和预览 ----------

@router.get("/api/files")
def files_list(path: str = "", user: dict = Depends(current_user)):
    """列出文件夹；传的是文件就列出它所在的文件夹，并用 select 标出这个文件。"""
    target = resolve_path(path, user, must_exist=False)
    if not target.exists():
        if _within(target, WS_ROOT):
            target.mkdir(parents=True, exist_ok=True)  # 自己的工作区第一次打开时还没建
        else:
            raise HTTPException(404, "文件不存在")
    select = None
    if target.is_file():
        select, target = to_virtual(target, user), target.parent
    return {"path": to_virtual(target, user), "abs": str(target), "crumbs": crumbs_for(target, user),
            "select": select, "entries": list_dir(target, user), "office": bool(SOFFICE),
            "roots": [str(FILE_ROOT), str(WS_ROOT)]}


@router.get("/api/files/search")
def files_search(q: str, path: str = "", user: dict = Depends(current_user)):
    """按文件名搜索，最多 200 条、最多搜 8 秒。"""
    base, words = resolve_path(path, user), q.lower().split()
    if not words:
        return {"results": [], "partial": False}
    data_dir = DATA.resolve()
    results, end, partial = [], time.time() + 8, False
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [n for n in dirnames if n.lower() not in HIDDEN_NAMES and not n.startswith(".")
                       and (Path(dirpath) / n).resolve() != data_dir]
        for name in dirnames + filenames:
            if all(w in name.lower() for w in words):
                try:
                    results.append(entry_info(Path(dirpath) / name, user))
                except OSError:
                    continue
        if len(results) >= 200 or time.time() > end:
            partial = True
            break
    return {"results": results[:200], "partial": partial}


@router.get("/api/files/raw")
def files_raw(path: str, download: bool = False, user: dict = Depends(current_user)):
    target = resolve_path(path, user)
    if not target.is_file():
        raise HTTPException(400, "这是文件夹")
    return FileResponse(target, filename=target.name,
                        content_disposition_type="attachment" if download else "inline")


@router.get("/api/files/text")
def files_text(path: str, user: dict = Depends(current_user)):
    target = resolve_path(path, user)
    data = target.read_bytes()[:TEXT_LIMIT + 1]
    truncated = len(data) > TEXT_LIMIT
    data = data[:TEXT_LIMIT]
    for enc in ("utf-8-sig", "gb18030", "utf-16"):  # 中文 Windows 上的老文件常是 GBK
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text, enc = data.decode("utf-8", errors="replace"), "utf-8"
    return {"text": text, "truncated": truncated, "encoding": enc}


@router.get("/api/files/pdf")
async def files_pdf(path: str, user: dict = Depends(current_user)):
    """用 LibreOffice 把 Office 文件转成 PDF 预览，按修改时间缓存。"""
    if not SOFFICE:
        raise HTTPException(501, "台式机没装 LibreOffice，这种文件不能预览")
    target = resolve_path(path, user)
    st = target.stat()
    key = uuid.uuid5(uuid.NAMESPACE_URL, f"{target}|{st.st_mtime_ns}|{st.st_size}").hex
    cache = DATA / "preview"
    pdf = cache / f"{key}.pdf"
    if not pdf.exists():
        async with _soffice_lock:  # soffice 同时只能跑一个
            if not pdf.exists():
                work = cache / key
                work.mkdir(parents=True, exist_ok=True)
                src = work / ("src" + target.suffix.lower())
                shutil.copyfile(target, src)  # 复制一份再转，原文件名带特殊字符时 soffice 容易失败
                cmd = [SOFFICE, "--headless", "--norestore", f"-env:UserInstallation={(cache / 'profile').as_uri()}",
                       "--convert-to", "pdf", "--outdir", str(work), str(src)]
                try:
                    await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=180,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                except subprocess.TimeoutExpired:
                    pass
                out = work / "src.pdf"
                if out.exists():
                    out.replace(pdf)
                shutil.rmtree(work, ignore_errors=True)
    if not pdf.exists():
        raise HTTPException(500, "转换失败，可以下载后用本机程序打开")
    return FileResponse(pdf, media_type="application/pdf", filename=target.stem + ".pdf",
                        content_disposition_type="inline")


# ---------- 工作区：同事本机的文件上传到这里，处理完再同步回去 ----------

class NewLink(BaseModel):
    name: str


def _safe_rel(rel: str) -> Path:
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or ":" in p for p in parts):
        raise HTTPException(400, "文件路径不对")
    return Path(*parts)


def _link(sess: dict, lid: str) -> tuple[dict, Path]:
    row = db("SELECT * FROM links WHERE id=? AND session_id=?", (lid, sess["id"]), one=True)
    if not row:
        raise HTTPException(404, "找不到这个上传的文件夹")
    return row, session_workspace(sess) / row["name"]


def link_view(row: dict, sess: dict, user: dict) -> dict:
    d = session_workspace(sess) / row["name"]
    return {"id": row["id"], "name": row["name"], "abs": str(d), "path": to_virtual(d, user), "created": row["created"]}


def _owner_only(sess: dict, user: dict) -> None:
    if sess["user_id"] != user["id"]:
        raise HTTPException(403, "只有会话的主人能上传文件")


@router.get("/api/sessions/{sid}/links")
def links_list(sid: str, user: dict = Depends(current_user)):
    sess = session_for(sid, user)
    rows = db("SELECT * FROM links WHERE session_id=? ORDER BY created", (sid,))
    return [link_view(r, sess, user) for r in rows]


@router.post("/api/sessions/{sid}/links")
def links_create(sid: str, body: NewLink, user: dict = Depends(current_user)):
    sess = session_for(sid, user)
    _owner_only(sess, user)
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", body.name.strip()).strip(". ")[:60] or "上传"
    ws, name, n = session_workspace(sess), base, 2
    while (ws / name).exists():
        name, n = f"{base}-{n}", n + 1
    (ws / name).mkdir()
    lid = uuid.uuid4().hex[:10]
    db("INSERT INTO links (id, session_id, user_id, name, created, manifest) VALUES (?,?,?,?,?,?)",
       (lid, sid, user["id"], name, time.time(), "{}"))
    return link_view(db("SELECT * FROM links WHERE id=?", (lid,), one=True), sess, user)


@router.put("/api/sessions/{sid}/links/{lid}/file")
async def links_upload(sid: str, lid: str, path: str, request: Request, user: dict = Depends(current_user)):
    sess = session_for(sid, user)
    _owner_only(sess, user)
    _, root = _link(sess, lid)
    dest = (root / _safe_rel(path)).resolve()
    if not dest.is_relative_to(root.resolve()):
        raise HTTPException(400, "文件路径不对")
    if int(request.headers.get("content-length") or 0) > MAX_UPLOAD:
        raise HTTPException(413, "单个文件不能超过 1 GB")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp, size = dest.with_name(dest.name + ".uploading"), 0
    with open(tmp, "wb") as f:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_UPLOAD:
                f.close()
                tmp.unlink(missing_ok=True)
                raise HTTPException(413, "单个文件不能超过 1 GB")
            f.write(chunk)
    tmp.replace(dest)
    return {"ok": True, "size": size}


def _sha1(p: Path) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _scan(root: Path) -> dict:
    out = {}
    for dirpath, _, files in os.walk(root):
        for name in files:
            if name.endswith(".uploading"):
                continue
            p = Path(dirpath) / name
            st = p.stat()
            out[p.relative_to(root).as_posix()] = [st.st_size, st.st_mtime_ns]
    return out


@router.post("/api/sessions/{sid}/links/{lid}/snapshot")
def links_snapshot(sid: str, lid: str, user: dict = Depends(current_user)):
    """记下现在的文件状态，之后用来对比 Claude 改了哪些。上传完、同步回本机后各调一次。"""
    sess = session_for(sid, user)
    _owner_only(sess, user)
    _, root = _link(sess, lid)
    manifest = {rel: v + [_sha1(root / rel)] for rel, v in _scan(root).items()}
    db("UPDATE links SET manifest=? WHERE id=?", (jdump(manifest), lid))
    return {"files": len(manifest)}


@router.get("/api/sessions/{sid}/links/{lid}/changes")
def links_changes(sid: str, lid: str, user: dict = Depends(current_user)):
    import json
    sess = session_for(sid, user)
    row, root = _link(sess, lid)
    old = json.loads(row["manifest"] or "{}")
    now = _scan(root)
    added, modified = [], []
    for rel, (size, mtime) in now.items():
        prev = old.get(rel)
        if not prev:
            added.append(rel)
        elif prev[0] != size or prev[1] != mtime:
            if _sha1(root / rel) != prev[2]:
                modified.append(rel)
    deleted = [rel for rel in old if rel not in now]

    def item(rel):
        p = root / rel
        return {"rel": rel, "path": to_virtual(p, user), "size": p.stat().st_size}
    return {"added": [item(r) for r in sorted(added)], "modified": [item(r) for r in sorted(modified)],
            "deleted": sorted(deleted)}


@router.delete("/api/sessions/{sid}/links/{lid}")
def links_delete(sid: str, lid: str, user: dict = Depends(current_user)):
    sess = session_for(sid, user)
    _owner_only(sess, user)
    _, root = _link(sess, lid)
    shutil.rmtree(root, ignore_errors=True)
    db("DELETE FROM links WHERE id=?", (lid,))
    return {"ok": True}
