"""文件库：浏览、预览、上传、新建文件夹、改名、删除（进回收站，30 天内可恢复），以及每个人的工作区。

路径写法（网页和接口之间）：
  "合同/2026/a.docx"        文件库（D 盘）里的相对路径
  "ws:2026-10-24 周报/a.xlsx" 自己的工作区；管理员可以用 "ws<用户id>:" 看别人的
  也接受完整路径（D:\\合同\\a.docx），会自动换算
工作区在 D:\\Projector工作区\\<显示名(用户名)>\\<日期 会话标题>\\，别人的工作区和回收站普通用户进不去。
"""
import asyncio
import hashlib
import json
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

from store import (BASE, DATA, FILE_ROOT, TRASH, TRASH_DAYS, WS_ROOT, current_user, db, get_user, is_admin, jdump,
                   log_file_op, require_admin, safe_name, session_for, session_workspace, user_ws_dir, ws_owner)

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
DATA_DIR = DATA.resolve()
BASE_DIR = BASE.resolve()


# ---------- 路径换算和权限 ----------

def _within(p: Path, root: Path) -> bool:
    return p == root or p.is_relative_to(root)


def _ws_owner_of(p: Path) -> int | None:
    """工作区里的路径属于谁；工作区根目录、回收站返回 None"""
    if not _within(p, WS_ROOT) or p == WS_ROOT:
        return None
    return ws_owner(p.relative_to(WS_ROOT).parts[0])


def _check(target: Path, user: dict) -> Path:
    """能不能看：系统数据不让碰；工作区只能看自己的；回收站和工作区根目录只有管理员能看。"""
    if _within(target, WS_ROOT):
        owner = _ws_owner_of(target)
        if not is_admin(user) and owner != user["id"]:
            raise HTTPException(403, "只能访问自己的工作区")
        return target
    if _within(target, DATA_DIR):
        raise HTTPException(403, "这里是系统数据，不能访问")
    if not _within(target, FILE_ROOT):
        raise HTTPException(403, "只能访问文件库里的文件")
    return target


def _check_write(target: Path, user: dict) -> Path:
    """能不能改：在能看的基础上，再挡住程序自己的文件夹、工作区和个人文件夹的根、回收站。"""
    _check(target, user)
    if target in (FILE_ROOT, WS_ROOT, TRASH) or _within(target, TRASH):
        raise HTTPException(403, "这个位置不能修改")
    if _within(target, BASE_DIR) or _within(BASE_DIR, target):
        raise HTTPException(403, "这是 Projector 程序所在的文件夹，不能在这里修改")
    if target.parent == WS_ROOT:
        raise HTTPException(403, "个人工作区文件夹本身不能改，里面的可以")
    return target


def resolve_path(path: str, user: dict, must_exist: bool = True) -> Path:
    raw = (path or "").strip().strip('"')
    if m := _WS_RE.match(raw):
        u = get_user(int(m.group(1))) if m.group(1) else user
        if not u:
            raise HTTPException(404, "没有这个用户")
        target = (user_ws_dir(u) / m.group(2).lstrip("/\\")).resolve()
    elif Path(raw).is_absolute():
        target = Path(raw).resolve()
    else:
        target = (FILE_ROOT / raw.lstrip("/\\")).resolve()
    _check(target, user)
    if must_exist and not target.exists():
        raise HTTPException(404, "文件不存在")
    return target


def to_virtual(p: Path, user: dict) -> str:
    owner = _ws_owner_of(p)
    if owner is not None:
        rest = "/".join(p.relative_to(WS_ROOT).parts[1:])
        return f"ws:{rest}" if owner == user["id"] else f"ws{owner}:{rest}"
    if _within(p, FILE_ROOT):
        return "" if p == FILE_ROOT else p.relative_to(FILE_ROOT).as_posix()
    return str(p)


def crumbs_for(p: Path, user: dict) -> list[dict]:
    owner = _ws_owner_of(p)
    if owner is not None:
        parts = p.relative_to(WS_ROOT).parts
        prefix = "ws:" if owner == user["id"] else f"ws{owner}:"
        label = "我的工作区" if owner == user["id"] else ((get_user(owner) or {}).get("display_name", "") + " 的工作区")
        out = [{"label": label, "path": prefix}]
        for i, part in enumerate(parts[1:], start=1):
            out.append({"label": part, "path": prefix + "/".join(parts[1:i + 1])})
        return out
    # 根目录是整个盘（D:\）就直接显示盘符，否则显示“文件库”，免得长路径把后面挤掉
    out = [{"label": str(FILE_ROOT) if FILE_ROOT.parent == FILE_ROOT else "文件库", "path": ""}]
    if p != FILE_ROOT and _within(p, FILE_ROOT):
        parts = p.relative_to(FILE_ROOT).parts
        for i, part in enumerate(parts):
            out.append({"label": part, "path": "/".join(parts[:i + 1])})
    return out


def _hidden(entry: os.DirEntry) -> bool:
    if entry.name.lower() in HIDDEN_NAMES or entry.name.startswith("~$") or entry.name.endswith(".uploading"):
        return True
    attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attrs & 0x6)  # Windows 的隐藏、系统属性


def entry_info(p: Path, user: dict, st=None) -> dict:
    st = st or p.stat()
    is_dir = p.is_dir()
    return {"name": p.name, "dir": is_dir, "size": 0 if is_dir else st.st_size, "mtime": st.st_mtime,
            "path": to_virtual(p, user), "abs": str(p)}


def list_dir(d: Path, user: dict, limit: int = 3000) -> list[dict]:
    items = []
    admin = is_admin(user)
    try:
        with os.scandir(d) as it:
            for e in it:
                try:
                    if _hidden(e):
                        continue
                    p = Path(e.path)
                    if e.name.lower() == "data" and p.resolve() == DATA_DIR:
                        continue  # 项目在 D 盘里时，不显示系统数据文件夹
                    if p == WS_ROOT and not admin:
                        continue  # 普通用户从“我的工作区”进自己的那份
                    if d == WS_ROOT and not admin and ws_owner(e.name) != user["id"]:
                        continue
                    items.append(entry_info(p, user, e.stat()))
                except OSError:
                    continue  # 没权限或已删除
                if len(items) >= limit:
                    break
    except PermissionError:
        raise HTTPException(403, "没有权限打开这个文件夹")
    items.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    return items


def _writable(p: Path, user: dict) -> bool:
    try:
        _check_write(p / "_", user)  # 能不能在这个文件夹里新建东西
        return True
    except HTTPException:
        return False


# ---------- 浏览和预览 ----------

@router.get("/api/files")
def files_list(path: str = "", user: dict = Depends(current_user)):
    """列出文件夹；传的是文件就列出它所在的文件夹，并用 select 标出这个文件。"""
    target = resolve_path(path, user, must_exist=False)
    if not target.exists():
        raise HTTPException(404, "文件夹不存在")
    select = None
    if target.is_file():
        select, target = to_virtual(target, user), target.parent
    return {"path": to_virtual(target, user), "abs": str(target), "crumbs": crumbs_for(target, user),
            "select": select, "entries": list_dir(target, user), "office": bool(SOFFICE),
            "writable": _writable(target, user), "roots": [str(FILE_ROOT), str(WS_ROOT)]}


@router.get("/api/files/search")
def files_search(q: str, path: str = "", user: dict = Depends(current_user)):
    """按文件名搜索，最多 200 条、最多搜 8 秒。"""
    base, words = resolve_path(path, user), q.lower().split()
    if not words:
        return {"results": [], "partial": False}
    admin = is_admin(user)
    results, end, partial = [], time.time() + 8, False
    for dirpath, dirnames, filenames in os.walk(base):
        here = Path(dirpath)
        keep = []
        for n in dirnames:
            full = (here / n)
            if n.lower() in HIDDEN_NAMES or n.startswith(".") or full.resolve() == DATA_DIR:
                continue
            if not admin and (full == WS_ROOT or (here == WS_ROOT and ws_owner(n) != user["id"])):
                continue
            keep.append(n)
        dirnames[:] = keep
        for name in dirnames + filenames:
            if all(w in name.lower() for w in words):
                try:
                    results.append(entry_info(here / name, user))
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


# ---------- 修改文件库：上传、新建文件夹、改名、删除（进回收站） ----------

def _safe_rel(rel: str) -> Path:
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or ":" in p for p in parts):
        raise HTTPException(400, "文件路径不对")
    return Path(*[safe_name(p, 120) for p in parts])


def _unique(p: Path) -> Path:
    """同名时加 (2)、(3)……"""
    if not p.exists():
        return p
    n = 2
    while True:
        cand = p.with_name(f"{p.stem} ({n}){p.suffix}") if p.is_file() or p.suffix else p.with_name(f"{p.name} ({n})")
        if not cand.exists():
            return cand
        n += 1


def _busy(e: Exception) -> HTTPException:
    return HTTPException(409, f"操作失败，文件可能正被别的程序打开：{e}")


def move_to_trash(p: Path, user: dict, reason: str = "删除") -> dict:
    """把文件或文件夹挪进回收站并记一笔；30 天后自动清掉。"""
    tid = uuid.uuid4().hex[:10]
    slot = TRASH / f"{time.strftime('%Y%m%d-%H%M%S')}_{tid}"
    slot.mkdir(parents=True, exist_ok=True)
    size = p.stat().st_size if p.is_file() else sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    is_dir = p.is_dir()
    try:
        shutil.move(str(p), str(slot / p.name))
    except OSError as e:
        shutil.rmtree(slot, ignore_errors=True)
        raise _busy(e)
    db("INSERT INTO trash (id, name, orig_path, trash_path, is_dir, size, deleted_by, deleted_at, reason) VALUES (?,?,?,?,?,?,?,?,?)",
       (tid, p.name, str(p), str(slot / p.name), int(is_dir), size, user["id"], time.time(), reason))
    return {"id": tid, "name": p.name}


def purge_trash() -> None:
    cutoff = time.time() - TRASH_DAYS * 86400
    for row in db("SELECT * FROM trash WHERE deleted_at < ?", (cutoff,)):
        shutil.rmtree(Path(row["trash_path"]).parent, ignore_errors=True)
        db("DELETE FROM trash WHERE id=?", (row["id"],))


class MkdirBody(BaseModel):
    path: str = ""
    name: str


class RenameBody(BaseModel):
    path: str
    name: str


class DeleteBody(BaseModel):
    paths: list[str]


@router.post("/api/files/mkdir")
def files_mkdir(body: MkdirBody, user: dict = Depends(current_user)):
    parent = resolve_path(body.path, user)
    target = parent / safe_name(body.name, 120)
    _check_write(target, user)
    if target.exists():
        raise HTTPException(409, "已经有同名的文件或文件夹")
    target.mkdir(parents=True)
    log_file_op(user, "新建文件夹", target)
    return entry_info(target, user)


@router.put("/api/files/upload")
async def files_upload(request: Request, dir: str, name: str, on_conflict: str = "ask", user: dict = Depends(current_user)):
    """上传一个文件到文件库的某个文件夹；name 可以带子文件夹（上传整个文件夹时）。
    on_conflict：ask 同名时返回 409 让网页问用户；overwrite 覆盖（旧文件进回收站）；rename 自动改名保留两份。"""
    parent = resolve_path(dir, user)
    if not parent.is_dir():
        raise HTTPException(400, "要上传到一个文件夹里")
    dest = (parent / _safe_rel(name)).resolve()
    if not _within(dest, parent):
        raise HTTPException(400, "文件路径不对")
    _check_write(dest, user)
    if int(request.headers.get("content-length") or 0) > MAX_UPLOAD:
        raise HTTPException(413, "单个文件不能超过 1 GB")
    replaced = None
    if dest.exists():
        if dest.is_dir():
            raise HTTPException(409, f"已经有同名文件夹：{dest.name}")
        if on_conflict == "rename":
            dest = _unique(dest)
        elif on_conflict == "overwrite":
            replaced = dest
        else:
            raise HTTPException(409, json.dumps({"exists": True, "name": dest.name}, ensure_ascii=False))
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
    if replaced:
        try:
            move_to_trash(replaced, user, "上传时被覆盖")
        except HTTPException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        tmp.replace(dest)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise _busy(e)
    log_file_op(user, "覆盖上传" if replaced else "上传", dest, f"{size} 字节")
    return entry_info(dest, user)


@router.post("/api/files/rename")
def files_rename(body: RenameBody, user: dict = Depends(current_user)):
    src = resolve_path(body.path, user)
    _check_write(src, user)
    dest = src.with_name(safe_name(body.name, 120))
    _check_write(dest, user)
    if dest == src:
        return entry_info(src, user)
    if dest.exists() and dest.name.lower() != src.name.lower():
        raise HTTPException(409, "已经有同名的文件或文件夹")
    try:
        src.rename(dest)
    except OSError as e:
        raise _busy(e)
    log_file_op(user, "改名", src, f"→ {dest.name}")
    return entry_info(dest, user)


@router.post("/api/files/delete")
def files_delete(body: DeleteBody, user: dict = Depends(current_user)):
    done, failed = [], []
    for raw in body.paths[:200]:
        try:
            p = resolve_path(raw, user)
            _check_write(p, user)
            done.append(move_to_trash(p, user))
            log_file_op(user, "删除", p, "移到回收站")
        except HTTPException as e:
            failed.append({"path": raw, "error": e.detail})
    return {"deleted": done, "failed": failed}


# ---------- 回收站 ----------

@router.get("/api/trash")
def trash_list(user: dict = Depends(current_user)):
    purge_trash()
    sql = "SELECT t.*, u.display_name AS who FROM trash t LEFT JOIN users u ON u.id=t.deleted_by"
    rows = db(sql + " ORDER BY t.deleted_at DESC") if is_admin(user) else \
        db(sql + " WHERE t.deleted_by=? ORDER BY t.deleted_at DESC", (user["id"],))
    for r in rows:
        r["days_left"] = max(0, TRASH_DAYS - int((time.time() - r["deleted_at"]) // 86400))
        r["orig_virtual"] = to_virtual(Path(r["orig_path"]), user) if _within(Path(r["orig_path"]), FILE_ROOT) or _within(Path(r["orig_path"]), WS_ROOT) else r["orig_path"]
    return {"items": rows, "days": TRASH_DAYS}


def _trash_item(tid: str, user: dict) -> dict:
    row = db("SELECT * FROM trash WHERE id=?", (tid,), one=True)
    if not row or (not is_admin(user) and row["deleted_by"] != user["id"]):
        raise HTTPException(404, "回收站里没有这个文件")
    return row


@router.post("/api/trash/{tid}/restore")
def trash_restore(tid: str, user: dict = Depends(current_user)):
    row = _trash_item(tid, user)
    src, orig = Path(row["trash_path"]), Path(row["orig_path"])
    if not src.exists():
        db("DELETE FROM trash WHERE id=?", (tid,))
        raise HTTPException(404, "回收站里的文件已经不在了")
    orig.parent.mkdir(parents=True, exist_ok=True)
    dest = _unique(orig) if orig.exists() else orig
    try:
        shutil.move(str(src), str(dest))
    except OSError as e:
        raise _busy(e)
    shutil.rmtree(src.parent, ignore_errors=True)
    db("DELETE FROM trash WHERE id=?", (tid,))
    log_file_op(user, "恢复", dest)
    return {"restored": str(dest), "path": to_virtual(dest, user), "renamed": dest != orig}


@router.delete("/api/trash/{tid}")
def trash_purge_one(tid: str, admin: dict = Depends(require_admin)):
    row = _trash_item(tid, admin)
    shutil.rmtree(Path(row["trash_path"]).parent, ignore_errors=True)
    db("DELETE FROM trash WHERE id=?", (tid,))
    log_file_op(admin, "彻底删除", row["orig_path"])
    return {"ok": True}


@router.get("/api/admin/file-ops")
def file_ops(limit: int = 300, admin: dict = Depends(require_admin)):
    return db("SELECT f.*, u.display_name AS who, u.avatar, u.id AS uid, u.role FROM file_ops f "
              "LEFT JOIN users u ON u.id=f.user_id ORDER BY f.id DESC LIMIT ?", (min(limit, 2000),))


# ---------- 工作区：同事本机的文件上传到这里，处理完再同步回去 ----------

class NewLink(BaseModel):
    name: str


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
    base = safe_name(body.name) or "上传"
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
    if root.exists():
        move_to_trash(root, user, "删除上传的文件夹")
    db("DELETE FROM links WHERE id=?", (lid,))
    return {"ok": True}
