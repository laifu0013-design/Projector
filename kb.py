"""团队知识库：D:\\知识库 里的 Markdown 笔记，格式和 Obsidian 一样，可以直接用 Obsidian 打开这个文件夹。

三类笔记，各放一个文件夹：
  经验/      从对话里沉淀下来的做法：遇到什么问题、怎么解决、用了哪些文件
  文件卡片/  重要文件或文件夹的摘要、关键信息和位置（source 记原文件路径，原文件改了会提示卡片可能过时）
  主题/      项目、客户、供应商、产品等，把相关的经验和卡片串起来
笔记之间用 [[笔记名]] 链接，开头 --- 包住的是属性（类型、标签、作者、日期……）。

Claude 通过 kb_search / kb_read / kb_write 工具读写（自动记作者，每次修改都留历史，可以恢复）；
read_document 把 Word、Excel、PPT、PDF 转成文字给 Claude 读。大家在网页的“知识库”里看、搜、改、看关系图谱。
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import files_api
from skills_api import _session_owner
from store import FILE_ROOT, KB_ROOT, current_user, db, get_user, is_admin, log_file_op, require_internal, safe_name

router = APIRouter()

TYPES = {"经验": "经验", "文件卡片": "文件卡片", "主题": "主题"}   # 类型 → 文件夹
STATUSES = ("有效", "过时")
GUIDE_NAME = "知识库说明.md"
HISTORY_KEEP = 60          # 每条笔记最多留多少个历史版本
LIST_KEYS = {"tags", "aliases", "contributors"}
ORDER = ["type", "summary", "tags", "aliases", "source", "source_mtime", "status",
         "author", "contributors", "created", "updated", "session"]
CJK = re.compile(r"[\u3400-\u9fff]")
LINK_RE = re.compile(r"(!?)\[\[([^\[\]\n|#^]+)(#[^\[\]\n|]*)?(?:\|([^\[\]\n]*))?\]\]")
TAG_RE = re.compile(r"(?:^|(?<=[\s，。、；（(]))#([^\s#\[\]{}()（），。、；：:,.!?！？\"'`]+)")
CODE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.S)

_write_lock = threading.Lock()

db("CREATE TABLE IF NOT EXISTS kb_history (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT, content TEXT, "
   "user_id INTEGER, by_name TEXT, note TEXT, created REAL)")
db("CREATE INDEX IF NOT EXISTS idx_kbh_path ON kb_history(path)")

GUIDE = """---
type: 说明
tags: [知识库]
---
# 知识库说明

这里是团队的知识库，每条笔记是一个 Markdown 文件，可以在 Projector 网页的“知识库”里看和改，也可以用 Obsidian 打开这个文件夹。

## 三类笔记

- **经验**（`经验/`）：做成过一件事以后，把背景、做法、用到的文件、踩过的坑记下来。聊完点“沉淀经验”，Claude 会帮你写。
- **文件卡片**（`文件卡片/`）：重要文件或文件夹的摘要、关键信息和位置。在文件库里点“整理进知识库”，Claude 会读文件后生成。原文件改过后，卡片会提示“可能过时”。
- **主题**（`主题/`）：项目、客户、供应商、产品、部门等。主题笔记把相关的经验和卡片串在一起。

## 写法

- 笔记之间用 `[[笔记名]]` 链接，比如 `[[客户A]]`、`[[2026 年采购合同]]`。链接到还不存在的笔记也可以，以后有人建了就自动连上。
- 开头 `---` 之间是属性：类型（type）、一句话摘要（summary）、标签（tags）、原文件（source）、状态（status：有效 / 过时）、作者、日期。
- 一条笔记只讲一件事，标题写成以后别人会搜的样子。
- 不要写进密码、身份证号、银行卡号这类敏感信息。
- 过时的内容不要直接删，标成“过时”，并链接到新的笔记。

每次修改都会留历史版本，改错了可以在网页里恢复。
"""


def ensure_layout() -> None:
    for folder in TYPES.values():
        (KB_ROOT / folder).mkdir(parents=True, exist_ok=True)
    guide = KB_ROOT / GUIDE_NAME
    if not guide.exists():
        guide.write_text(GUIDE, encoding="utf-8")


ensure_layout()


# ---------- 属性（frontmatter）的读写：只支持 Obsidian 常用的简单写法 ----------

FM_RE = re.compile(r"\A\ufeff?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


def _scalar(v: str):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        if v[0] == '"':
            try:
                return json.loads(v)
            except ValueError:
                return v[1:-1]
        return v[1:-1].replace("''", "'")
    return v


def _split_flow(s: str) -> list[str]:
    out, cur, quote = [], "", ""
    for ch in s:
        if quote:
            cur += ch
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            cur += ch
        elif ch == ",":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def parse_yaml(src: str) -> dict:
    out: dict = {}
    key = None
    for line in src.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        s = line.strip()
        if line[0] in " \t" or s.startswith("- ") or s == "-":
            if key and s.startswith("-"):
                if not isinstance(out.get(key), list):
                    out[key] = []
                item = _scalar(s[1:])
                if item != "":
                    out[key].append(item)
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        key, v = k.strip(), v.strip()
        if v.startswith("[") and v.endswith("]"):
            out[key] = [_scalar(x) for x in _split_flow(v[1:-1]) if x.strip()]
        elif v == "":
            out[key] = []
        else:
            out[key] = _scalar(v)
    return {k: ("" if v == [] and k not in LIST_KEYS else v) for k, v in out.items()}


def split_fm(text: str) -> tuple[dict, str]:
    m = FM_RE.match(text)
    if not m:
        return {}, text.lstrip("\ufeff")
    try:
        return parse_yaml(m.group(1)), text[m.end():]
    except Exception:  # noqa: BLE001  属性写坏了也不要影响正文
        return {}, text[m.end():]


def _q(v) -> str:
    s = str(v)
    if (s == "" or re.search(r"[:#\[\]{},&*!|>'\"%@`]", s) or s != s.strip() or s[0] in "-?"
            or s.lower() in ("true", "false", "null", "yes", "no", "~")):
        return json.dumps(s, ensure_ascii=False)
    return s


def dump_fm(meta: dict) -> str:
    lines = []
    for k in ORDER + [k for k in meta if k not in ORDER]:
        v = meta.get(k)
        if v in (None, "", []):
            continue
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(_q(x) for x in v)}]")
        else:
            lines.append(f"{k}: {_q(v)}")
    return ("---\n" + "\n".join(lines) + "\n---\n") if lines else ""


def _as_list(v) -> list[str]:
    if isinstance(v, list):
        items = v
    elif isinstance(v, str) and v.strip():
        items = re.split(r"[,，、\s]+", v)
    else:
        items = []
    out = []
    for x in items:
        x = str(x).strip().lstrip("#").strip()
        if x and x not in out:
            out.append(x)
    return out


def read_text(p: Path) -> str:
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ---------- 索引：扫描文件夹，按修改时间缓存，算出链接、反向链接、未创建的链接 ----------

class Index:
    def __init__(self):
        self.notes: dict[str, dict] = {}
        self.names: dict[str, str] = {}
        self.back: dict[str, set] = {}
        self.ghosts: dict[str, dict] = {}
        self.scanned = 0.0
        self.dirty = True
        self.lock = threading.Lock()


IDX = Index()


def _source_path(src: str) -> Path | None:
    if not src:
        return None
    p = Path(src.strip().strip('"'))
    return p if p.is_absolute() else FILE_ROOT / src.strip().lstrip("/\\")


def _parse(p: Path, rel: str, st) -> dict:
    try:
        text = read_text(p)
    except OSError:
        text = ""
    meta, body = split_fm(text)
    clean = CODE_RE.sub(" ", body)
    links = []
    for m in LINK_RE.finditer(clean):
        t = m.group(2).strip()
        if t and t not in links:
            links.append(t)
    tags = _as_list(meta.get("tags"))
    for t in TAG_RE.findall(clean):
        t = t.strip("/")
        if t and not t.isdigit() and t not in tags:
            tags.append(t)
    folder = rel.split("/")[0] if "/" in rel else ""
    ntype = str(meta.get("type") or (folder if folder in TYPES else "笔记"))
    status = str(meta.get("status") or "有效")
    return {
        "path": rel, "title": Path(rel).stem, "type": ntype, "tags": tags,
        "summary": str(meta.get("summary") or ""), "status": status if status in STATUSES else "有效",
        "author": str(meta.get("author") or ""), "contributors": _as_list(meta.get("contributors")),
        "created": str(meta.get("created") or ""), "updated": str(meta.get("updated") or ""),
        "source": str(meta.get("source") or ""), "source_mtime": str(meta.get("source_mtime") or ""),
        "session": str(meta.get("session") or ""), "aliases": _as_list(meta.get("aliases")),
        "links": links, "mtime": st.st_mtime, "size": st.st_size,
        "body": body, "body_l": body.lower(), "meta": meta,
    }


def index(force: bool = False) -> Index:
    """扫描知识库；两秒内重复调用直接用缓存（Obsidian 等外部修改最多晚两秒看到）。"""
    with IDX.lock:
        if not force and not IDX.dirty and time.time() - IDX.scanned < 2:
            return IDX
        changed = IDX.dirty
        seen = set()
        for dirpath, dirnames, filenames in os.walk(KB_ROOT):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for f in filenames:
                if not f.lower().endswith(".md") or f.startswith("~$"):
                    continue
                p = Path(dirpath) / f
                rel = p.relative_to(KB_ROOT).as_posix()
                try:
                    st = p.stat()
                except OSError:
                    continue
                seen.add(rel)
                old = IDX.notes.get(rel)
                if not old or old["mtime"] != st.st_mtime or old["size"] != st.st_size:
                    IDX.notes[rel] = _parse(p, rel, st)
                    changed = True
        for rel in [r for r in IDX.notes if r not in seen]:
            del IDX.notes[rel]
            changed = True
        if changed:
            _rebuild_links()
        IDX.scanned = time.time()
        IDX.dirty = False
        return IDX


def _rebuild_links() -> None:
    names: dict[str, str] = {}
    for n in sorted(IDX.notes.values(), key=lambda n: (n["path"].count("/"), len(n["path"]), n["path"])):
        for key in (n["title"].lower(), n["path"][:-3].lower(), *(a.lower() for a in n["aliases"])):
            names.setdefault(key, n["path"])
    IDX.names = names
    back: dict[str, set] = {}
    ghosts: dict[str, dict] = {}
    for n in IDX.notes.values():
        for t in n["links"]:
            target = resolve(t, names)
            if target:
                if target != n["path"]:
                    back.setdefault(target, set()).add(n["path"])
            else:
                g = ghosts.setdefault(t.lower(), {"name": t, "from": set()})
                g["from"].add(n["path"])
    IDX.back, IDX.ghosts = back, ghosts


def resolve(target: str, names: dict | None = None) -> str | None:
    names = IDX.names if names is None else names
    t = target.strip().replace("\\", "/")
    if t.lower().endswith(".md"):
        t = t[:-3]
    return names.get(t.lower()) or names.get(t.split("/")[-1].lower())


def invalidate() -> None:
    IDX.dirty = True


def source_state(n: dict) -> str:
    """文件卡片对应的原文件：missing 已经不在了；changed 卡片写完以后又改过"""
    p = _source_path(n.get("source", ""))
    if not p:
        return ""
    try:
        st = p.stat()
    except OSError:
        return "missing"
    try:
        sm = float(n.get("source_mtime") or 0)
    except ValueError:
        sm = 0
    return "changed" if sm and st.st_mtime > sm + 2 else ""


def brief(n: dict) -> dict:
    return {k: n[k] for k in ("path", "title", "type", "tags", "summary", "status", "author", "created",
                              "updated", "source", "mtime")} | {
        "source_state": source_state(n), "links": len(n["links"]), "backlinks": len(IDX.back.get(n["path"], ())),
        "words": len(n["body"])}


# ---------- 搜索 ----------

def _terms(q: str) -> list[str]:
    return [t for t in dict.fromkeys(re.split(r"[\s,，。、；;:：!?！？()（）\[\]【】\"'“”]+", q.lower())) if t]


def _snippet(n: dict, terms: list[str], width: int = 70) -> str:
    body = n["body"]
    low = n["body_l"]
    pos = min((i for i in (low.find(t) for t in terms) if i >= 0), default=-1)
    if pos < 0:
        text = n["summary"] or body
        pos = 0
    else:
        text = body
    start = max(0, pos - width // 2)
    s = re.sub(r"\s+", " ", text[start:start + width * 2]).strip()
    s = LINK_RE.sub(lambda m: m.group(4) or m.group(2), s).replace("[[", "").replace("]]", "")
    s = re.sub(r"[#>*`]+", "", s)
    return ("…" if start > 0 else "") + s + ("…" if start + width * 2 < len(text) else "")


def search(q: str, limit: int = 20, ntype: str = "") -> list[dict]:
    idx = index()
    terms = _terms(q)
    if not terms:
        return []
    scored = []
    for n in idx.notes.values():
        if ntype and n["type"] != ntype:
            continue
        title, summ, body = n["title"].lower(), n["summary"].lower(), n["body_l"]
        tags = [t.lower() for t in n["tags"]]
        extra = " ".join([*n["aliases"], n["source"]]).lower()
        score, matched = 0.0, 0
        for t in terms:
            s = 12 * title.count(t) + (8 if any(t in g for g in tags) else 0) + 4 * summ.count(t) \
                + 3 * extra.count(t) + min(body.count(t), 8)
            if s == 0 and len(t) >= 3 and CJK.search(t):
                # 长的中文词没完全命中时，按两个字一组再找一遍（“退货纠纷处理”也能找到“退货纠纷”）
                grams = {t[i:i + 2] for i in range(len(t) - 1)}
                hit = sum(1 for g in grams if g in title or g in summ or g in body)
                if hit >= max(2, (len(grams) + 1) // 2):
                    s = hit * 0.8
            if s:
                matched += 1
            score += s
        if not score:
            continue
        score *= 1 + matched / len(terms)          # 命中的词越多越靠前
        if n["status"] == "过时":
            score *= 0.5
        score += 0.3 * len(idx.back.get(n["path"], ()))
        scored.append((score, n))
    scored.sort(key=lambda x: (-x[0], -x[1]["mtime"]))
    return [brief(n) | {"score": round(s, 1), "snippet": _snippet(n, terms)} for s, n in scored[:limit]]


# ---------- 写入：自动补属性、留历史、改名时更新别人的链接 ----------

def _rel_of(path: str) -> str:
    raw = (path or "").strip().replace("\\", "/")
    p = Path(raw)
    if p.is_absolute():
        try:
            raw = p.resolve().relative_to(KB_ROOT).as_posix()
        except ValueError:
            raise HTTPException(400, "这个文件不在知识库里")
    raw = raw.lstrip("/")
    target = (KB_ROOT / raw).resolve()
    if not target.is_relative_to(KB_ROOT) or target == KB_ROOT or not raw.lower().endswith(".md"):
        raise HTTPException(400, "笔记路径不对")
    return target.relative_to(KB_ROOT).as_posix()


def _title_file(title: str) -> str:
    t = safe_name(re.sub(r"[\[\]#^|]", " ", title or ""), 80)
    return re.sub(r"\s+", " ", t).strip() or "未命名"


def _snapshot(rel: str, content: str, user: dict | None, by_name: str, note: str) -> None:
    db("INSERT INTO kb_history (path, content, user_id, by_name, note, created) VALUES (?,?,?,?,?,?)",
       (rel, content, (user or {}).get("id"), by_name, note[:200], time.time()))
    old = db("SELECT id FROM kb_history WHERE path=? ORDER BY id DESC LIMIT -1 OFFSET ?", (rel, HISTORY_KEEP))
    for r in old:
        db("DELETE FROM kb_history WHERE id=?", (r["id"],))


def _save(rel: str, content: str, user: dict | None, by_name: str, note: str) -> None:
    """写文件前先把当前内容存进历史（如果是在外面改的，比如 Obsidian），写完再存一份新的。"""
    p = KB_ROOT / rel
    if p.exists():
        cur = read_text(p)
        last = db("SELECT content FROM kb_history WHERE path=? ORDER BY id DESC LIMIT 1", (rel,), one=True)
        if not last or last["content"] != cur:
            _snapshot(rel, cur, None, "（之前的版本）", "修改前的内容")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".saving")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    os.replace(tmp, p)
    _snapshot(rel, content, user, by_name, note)


class KBConflict(Exception):
    pass


def write_note(*, user: dict, by_name: str, title: str = "", body: str = "", ntype: str | None = None,
               tags=None, summary: str | None = None, source: str | None = None, status: str | None = None,
               mode: str = "create", path: str | None = None, session: str = "", base_mtime: float | None = None,
               note: str = "") -> dict:
    today = time.strftime("%Y-%m-%d")
    # Claude 有时会把属性也写进正文，拆出来合并
    extra_meta, body = split_fm(body or "")
    body = body.strip("\n")
    with _write_lock:
        idx = index(force=True)
        if path:
            rel = _rel_of(path)
        else:
            title = (title or extra_meta.get("title") or "").strip()
            if not title:
                raise HTTPException(400, "笔记要有标题")
            rel = resolve(title, idx.names)
            if not rel:
                ntype_ = ntype or str(extra_meta.get("type") or "") or "经验"
                folder = TYPES.get(ntype_, "")
                rel = (folder + "/" if folder else "") + _title_file(title) + ".md"
        p = KB_ROOT / rel
        exists = p.exists()
        if exists and mode == "create":
            raise KBConflict(rel)
        if exists and base_mtime and abs(p.stat().st_mtime - base_mtime) > 0.01:
            raise HTTPException(409, "这条笔记刚被别人（或 Claude）改过，请刷新后再改")
        old_meta, old_body = split_fm(read_text(p)) if exists else ({}, "")
        meta = dict(old_meta)
        for k, v in extra_meta.items():
            meta.setdefault(k, v)
        meta.pop("title", None)
        if ntype or not exists:
            meta["type"] = ntype or str(extra_meta.get("type") or "") or \
                (rel.split("/")[0] if rel.split("/")[0] in TYPES else "笔记")
        if summary is not None:
            meta["summary"] = re.sub(r"\s+", " ", summary).strip()[:200]
        if tags is not None:
            new_tags = _as_list(tags)
            meta["tags"] = (_as_list(old_meta.get("tags")) + [t for t in new_tags if t not in _as_list(old_meta.get("tags"))]) \
                if mode == "append" else new_tags
        if source is not None:
            meta["source"] = source.strip()
            sp = _source_path(meta["source"])
            meta["source_mtime"] = int(sp.stat().st_mtime) if sp and sp.exists() else ""
        if status is not None:
            if status not in STATUSES:
                raise HTTPException(400, "状态只能是“有效”或“过时”")
            meta["status"] = status
        if not meta.get("author"):
            meta["author"] = by_name
        elif by_name and by_name != meta["author"]:
            meta["contributors"] = _as_list(meta.get("contributors")) + \
                ([by_name] if by_name not in _as_list(meta.get("contributors")) else [])
        meta.setdefault("created", today)
        if not meta.get("created"):
            meta["created"] = today
        meta["updated"] = today
        if session and not meta.get("session"):
            meta["session"] = session
        if mode == "append" and exists:
            new_body = old_body.rstrip() + "\n\n" + body.strip() + "\n"
        elif body or not exists:
            new_body = body.strip() + "\n"
        else:
            new_body = old_body
        content = dump_fm(meta) + "\n" + new_body.lstrip("\n")
        _save(rel, content, user, by_name, note or ("新建" if not exists else "追加" if mode == "append" else "修改"))
        invalidate()
    log_file_op(user, "知识库新建" if not exists else "知识库修改", KB_ROOT / rel, by_name)
    idx = index(force=True)
    n = idx.notes.get(rel)
    missing = [t for t in (n["links"] if n else []) if not resolve(t)]
    return {"path": rel, "abs": str(KB_ROOT / rel), "title": Path(rel).stem, "created": not exists,
            "missing_links": missing, "mtime": n["mtime"] if n else 0}


def rename_note(rel: str, new_title: str, user: dict) -> str:
    """改标题 = 改文件名；顺便把其他笔记里的 [[旧名]] 改成 [[新名]]（和 Obsidian 一样）"""
    new_file = _title_file(new_title) + ".md"
    old_title = Path(rel).stem
    new_rel = (rel.rsplit("/", 1)[0] + "/" if "/" in rel else "") + new_file
    if new_rel == rel:
        return rel
    if (KB_ROOT / new_rel).exists() or resolve(Path(new_file).stem):
        raise HTTPException(409, f"已经有一条叫“{Path(new_file).stem}”的笔记了")
    with _write_lock:
        idx = index(force=True)
        refs = sorted(idx.back.get(rel, ()))
        os.replace(KB_ROOT / rel, KB_ROOT / new_rel)
        db("UPDATE kb_history SET path=? WHERE path=?", (new_rel, rel))
        pat = re.compile(r"(!?\[\[)" + re.escape(old_title) + r"((?:#[^\[\]\n|]*)?(?:\|[^\[\]\n]*)?\]\])", re.I)
        for r in refs:
            text = read_text(KB_ROOT / r)
            new_text = pat.sub(lambda m: m.group(1) + Path(new_file).stem + m.group(2), text)
            if new_text != text:
                _save(r, new_text, user, user["display_name"], f"链接跟着改名：{old_title} → {Path(new_file).stem}")
        invalidate()
    log_file_op(user, "知识库改名", KB_ROOT / new_rel, f"{old_title} → {Path(new_file).stem}")
    return new_rel


# ---------- 读 Word / Excel / PPT / PDF ----------

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"
SHEET_ROWS = 400
_doc_cache: dict[tuple, str] = {}


def _wpara(p) -> str:
    out = []
    for el in p.iter():
        if el.tag == W + "t" and el.text:
            out.append(el.text)
        elif el.tag == W + "tab":
            out.append("\t")
        elif el.tag in (W + "br", W + "cr"):
            out.append("\n")
    text = "".join(out)
    style = p.find(f"{W}pPr/{W}pStyle")
    if style is not None and text.strip():
        m = re.search(r"(?i)heading\s*(\d)|^(\d)$|标题\s*(\d)", style.get(W + "val", ""))
        if m:
            text = "#" * min(int(next(g for g in m.groups() if g)), 6) + " " + text
    return text


def _docx(z: zipfile.ZipFile) -> str:
    root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(W + "body")
    out = []
    for el in (body if body is not None else []):
        if el.tag == W + "p":
            out.append(_wpara(el))
        elif el.tag == W + "tbl":
            for tr in el.iter(W + "tr"):
                cells = [" ".join(_wpara(p) for p in tc.iter(W + "p")).strip().replace("\n", " ")
                         for tc in tr.findall(W + "tc")]
                out.append("| " + " | ".join(cells) + " |")
            out.append("")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]*", ref or "").group(0):
        n = n * 26 + ord(ch) - 64
    return max(n - 1, 0)


def _xlsx(z: zipfile.ZipFile) -> str:
    names = set(z.namelist())
    shared = []
    if "xl/sharedStrings.xml" in names:
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(S + "si"):
            shared.append("".join(t.text or "" for t in si.iter(S + "t")))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).iter(PKG + "Relationship")}
    out = []
    for sh in wb.iter(S + "sheet"):
        target = (rels.get(sh.get(REL + "id")) or "").lstrip("/")
        target = target if target.startswith("xl/") else "xl/" + target
        if target not in names:
            continue
        out.append(f"## 工作表：{sh.get('name')}")
        rows, more = 0, False
        with z.open(target) as f:
            for _ev, el in ET.iterparse(f):
                if el.tag != S + "row":
                    continue
                if rows >= SHEET_ROWS:
                    more = True
                    el.clear()
                    continue
                cells: dict[int, str] = {}
                for c in el.findall(S + "c"):
                    t, v = c.get("t"), c.find(S + "v")
                    if t == "s" and v is not None and v.text and v.text.isdigit() and int(v.text) < len(shared):
                        val = shared[int(v.text)]
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(S + "t"))
                    elif t == "b" and v is not None:
                        val = "是" if v.text == "1" else "否"
                    else:
                        val = v.text if v is not None and v.text else ""
                    if val != "":
                        cells[_col(c.get("r", ""))] = val.replace("\n", " ").replace("\t", " ")
                el.clear()
                if cells:
                    out.append("\t".join(cells.get(i, "") for i in range(max(cells) + 1)))
                    rows += 1
        if more:
            out.append(f"……（这个工作表超过 {SHEET_ROWS} 行，只列出前 {SHEET_ROWS} 行有内容的；要完整数据请用 python 读）")
        out.append("")
    out.append("（提示：日期可能显示成数字，是 Excel 的日期序号，45000 左右≈2023 年）")
    return "\n".join(out).strip()


def _pptx(z: zipfile.ZipFile) -> str:
    slides = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                    key=lambda n: int(re.search(r"(\d+)\.xml", n).group(1)))
    out = []
    for i, name in enumerate(slides, 1):
        root = ET.fromstring(z.read(name))
        paras = ["".join(t.text or "" for t in p.iter(A + "t")) for p in root.iter(A + "p")]
        out.append(f"## 第 {i} 页\n" + "\n".join(x for x in paras if x.strip()))
    return "\n\n".join(out).strip()


def _pdf(p: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "（台式机没装 pypdf，读不了 PDF：在服务端文件夹运行 pip install pypdf，或者直接用 Read 工具读）"
    reader = PdfReader(str(p))
    out = []
    for i, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            text = ""
        out.append(f"## 第 {i} 页\n{text.strip()}")
    text = "\n\n".join(out).strip()
    if len(re.sub(r"\s|#|第 \d+ 页", "", text)) < 20:
        text += "\n\n（这个 PDF 几乎没有文字，可能是扫描件或图片，需要 OCR 才能读）"
    return text


OLD_OFFICE = {"doc": "docx", "wps": "docx", "rtf": "docx", "odt": "docx",
              "xls": "xlsx", "et": "xlsx", "ods": "xlsx", "ppt": "pptx", "dps": "pptx", "odp": "pptx"}


def extract_text(p: Path) -> str:
    st = p.stat()
    key = (str(p), st.st_mtime, st.st_size)
    if key in _doc_cache:
        return _doc_cache[key]
    ext = p.suffix.lower().lstrip(".")
    if ext in OLD_OFFICE:
        if not files_api.SOFFICE:
            raise HTTPException(400, f".{ext} 是老格式，台式机装了 LibreOffice 才能读；也可以请用户另存为 .{OLD_OFFICE[ext]}")
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([files_api.SOFFICE, "--headless", "--convert-to", OLD_OFFICE[ext], "--outdir", tmp, str(p)],
                           capture_output=True, timeout=180, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            out = next(Path(tmp).glob("*." + OLD_OFFICE[ext]), None)
            if not out:
                raise HTTPException(500, "转换失败，文件可能损坏或加了密码")
            text = extract_text(out)
    elif ext in ("docx", "docm", "xlsx", "xlsm", "pptx", "ppsx"):
        try:
            with zipfile.ZipFile(p) as z:
                text = _docx(z) if ext.startswith("doc") else _xlsx(z) if ext.startswith("xls") else _pptx(z)
        except (zipfile.BadZipFile, KeyError, ET.ParseError):
            raise HTTPException(400, "读不了这个文件：可能加了密码，或者文件已损坏")
    elif ext == "pdf":
        text = _pdf(p)
    elif st.st_size <= 5 * 1024 * 1024:
        text = read_text(p)
    else:
        raise HTTPException(400, "文件太大，也不是能转成文字的格式")
    if len(_doc_cache) > 30:
        _doc_cache.clear()
    _doc_cache[key] = text
    return text


# ---------- 接口：网页 ----------

def _guard_note(rel: str) -> dict:
    n = index().notes.get(rel)
    if not n:
        raise HTTPException(404, "没有这条笔记（可能刚被删除或改名）")
    return n


def _recent(limit: int = 40) -> list[dict]:
    rows = db("SELECT h.id, h.path, h.by_name, h.note, h.created FROM kb_history h "
              "WHERE h.user_id IS NOT NULL ORDER BY h.id DESC LIMIT ?", (limit * 3,))
    out, seen = [], set()
    for r in rows:
        k = (r["path"], r["by_name"], int(r["created"] // 600))   # 十分钟内同一人改同一篇算一次
        if k in seen:
            continue
        seen.add(k)
        out.append({**r, "title": Path(r["path"]).stem, "exists": r["path"] in IDX.notes})
        if len(out) >= limit:
            break
    return out


@router.get("/api/kb")
def kb_home(user: dict = Depends(current_user)):
    idx = index()
    notes = [brief(n) for n in idx.notes.values()]
    notes.sort(key=lambda n: -n["mtime"])
    tags: dict[str, int] = {}
    for n in notes:
        for t in n["tags"]:
            tags[t] = tags.get(t, 0) + 1
    week = time.time() - 7 * 86400
    by_type: dict[str, int] = {}
    for n in notes:
        by_type[n["type"]] = by_type.get(n["type"], 0) + 1
    hubs = sorted(notes, key=lambda n: -n["backlinks"])[:8]
    ghosts = sorted(({"name": g["name"], "count": len(g["from"])} for g in idx.ghosts.values()),
                    key=lambda g: -g["count"])[:12]
    return {"root": str(KB_ROOT), "notes": notes, "types": list(TYPES),
            "tags": sorted(tags.items(), key=lambda x: (-x[1], x[0]))[:60],
            "stats": {"total": len(notes), "by_type": by_type, "week": sum(1 for n in notes if n["mtime"] >= week),
                      "stale": sum(1 for n in notes if n["source_state"]), "links": sum(n["links"] for n in notes)},
            "hubs": [h for h in hubs if h["backlinks"]], "ghosts": [g for g in ghosts if g["count"] > 1],
            "recent": _recent(20)}


@router.get("/api/kb/note")
def kb_note(path: str, user: dict = Depends(current_user)):
    rel = _rel_of(path)
    n = _guard_note(rel)
    idx = IDX
    backlinks = []
    for src in sorted(idx.back.get(rel, ()), key=lambda r: -idx.notes[r]["mtime"]):
        b = idx.notes[src]
        terms = [rel.split("/")[-1][:-3].lower()]
        backlinks.append({"path": src, "title": b["title"], "type": b["type"], "snippet": _snippet(b, terms, 50)})
    links = {t: resolve(t) for t in n["links"]}
    sess = None
    if n["session"]:
        s = db("SELECT id, title, user_id FROM sessions WHERE id=?", (n["session"],), one=True)
        if s and (is_admin(user) or s["user_id"] == user["id"]):
            sess = {"id": s["id"], "title": s["title"]}
    hist = db("SELECT COUNT(*) AS c FROM kb_history WHERE path=?", (rel,), one=True)["c"]
    sp = _source_path(n["source"])
    return {"note": brief(n) | {"body": n["body"], "contributors": n["contributors"], "aliases": n["aliases"],
                                "abs": str(KB_ROOT / rel), "source_abs": str(sp) if sp else ""},
            "backlinks": backlinks, "links": links, "session": sess, "history": hist}


@router.get("/api/kb/search")
def kb_search_api(q: str, type: str = "", user: dict = Depends(current_user)):
    return {"results": search(q, 40, type)}


@router.get("/api/kb/graph")
def kb_graph(user: dict = Depends(current_user)):
    idx = index()
    nodes = [{"id": n["path"], "title": n["title"], "type": n["type"], "status": n["status"],
              "deg": len(n["links"]) + len(idx.back.get(n["path"], ()))} for n in idx.notes.values()]
    edges = set()
    for n in idx.notes.values():
        for t in n["links"]:
            target = resolve(t)
            if target and target != n["path"]:
                edges.add(tuple(sorted((n["path"], target))))
            elif not target:
                edges.add((n["path"], "?" + t.lower()))
    ghosts = [{"id": "?" + k, "title": g["name"], "type": "ghost", "deg": len(g["from"])} for k, g in idx.ghosts.items()]
    return {"nodes": nodes + ghosts, "edges": [list(e) for e in edges]}


@router.get("/api/kb/cards")
def kb_cards(path: str, user: dict = Depends(current_user)):
    """文件库里预览某个文件时，看看知识库里有没有它的卡片"""
    target = files_api.resolve_path(path, user, must_exist=False)
    key = os.path.normcase(str(target))
    out = []
    for n in index().notes.values():
        sp = _source_path(n["source"])
        if sp and os.path.normcase(str(sp)) == key:
            out.append(brief(n))
    return {"cards": out}


class NoteBody(BaseModel):
    path: str = ""
    title: str = ""
    type: str = ""
    tags: list[str] | None = None
    summary: str | None = None
    body: str = ""
    source: str | None = None
    status: str | None = None
    base_mtime: float | None = None


@router.put("/api/kb/note")
def kb_save(body: NoteBody, user: dict = Depends(current_user)):
    rel = body.path and _rel_of(body.path)
    if rel and body.title and _title_file(body.title) != Path(rel).stem:
        rel = rename_note(rel, body.title, user)
    if body.type and body.type not in TYPES and body.type not in ("笔记", "说明"):
        raise HTTPException(400, "类型不对")
    try:
        r = write_note(user=user, by_name=user["display_name"], title=body.title, body=body.body,
                       ntype=body.type or None, tags=body.tags, summary=body.summary, source=body.source,
                       status=body.status, mode="update" if rel else "create", path=rel or None,
                       base_mtime=body.base_mtime if rel else None)
    except KBConflict as e:
        raise HTTPException(409, f"已经有一条叫“{Path(str(e)).stem}”的笔记了，换个标题，或者打开那条去改")
    return r


class PathBody(BaseModel):
    path: str
    status: str = ""


@router.post("/api/kb/status")
def kb_status(body: PathBody, user: dict = Depends(current_user)):
    rel = _rel_of(body.path)
    _guard_note(rel)
    return write_note(user=user, by_name=user["display_name"], path=rel, mode="update", status=body.status,
                      note=f"标记为{body.status}")


@router.post("/api/kb/delete")
def kb_delete(body: PathBody, user: dict = Depends(current_user)):
    rel = _rel_of(body.path)
    _guard_note(rel)
    if rel == GUIDE_NAME:
        raise HTTPException(400, "说明页不能删")
    with _write_lock:
        files_api.move_to_trash(KB_ROOT / rel, user, "知识库")
        invalidate()
    log_file_op(user, "知识库删除", KB_ROOT / rel)
    return {"ok": True}


@router.get("/api/kb/history")
def kb_history(path: str, user: dict = Depends(current_user)):
    rel = _rel_of(path)
    rows = db("SELECT id, by_name, note, created, length(content) AS size FROM kb_history WHERE path=? ORDER BY id DESC", (rel,))
    return {"versions": rows}


@router.get("/api/kb/history/{hid}")
def kb_history_one(hid: int, user: dict = Depends(current_user)):
    row = db("SELECT * FROM kb_history WHERE id=?", (hid,), one=True)
    if not row:
        raise HTTPException(404, "没有这个版本")
    return row


@router.post("/api/kb/history/{hid}/restore")
def kb_restore(hid: int, user: dict = Depends(current_user)):
    row = db("SELECT * FROM kb_history WHERE id=?", (hid,), one=True)
    if not row:
        raise HTTPException(404, "没有这个版本")
    with _write_lock:
        _save(row["path"], row["content"], user, user["display_name"], f"恢复到 {time.strftime('%m-%d %H:%M', time.localtime(row['created']))} 的版本")
        invalidate()
    log_file_op(user, "知识库修改", KB_ROOT / row["path"], "恢复历史版本")
    return {"path": row["path"]}


# ---------- 接口：给 Claude 的工具（approval_mcp.py） ----------

class KBReq(BaseModel):
    session_id: str
    query: str = ""
    limit: int = 8
    name: str = ""
    title: str = ""
    content: str = ""
    type: str = ""
    tags: list[str] | str | None = None
    summary: str | None = None
    source: str | None = None
    status: str | None = None
    mode: str = "create"
    path: str = ""
    offset: int = 0


@router.post("/api/internal/kb/search")
def internal_search(body: KBReq, _: dict = Depends(require_internal)):
    return {"root": str(KB_ROOT), "results": search(body.query, max(1, min(body.limit, 20)))}


@router.post("/api/internal/kb/read")
def internal_read(body: KBReq, _: dict = Depends(require_internal)):
    idx = index()
    name = body.name.strip()
    rel = None
    if name.lower().endswith(".md"):
        try:
            rel = _rel_of(name)
        except HTTPException:
            rel = None
    rel = rel if rel in idx.notes else resolve(name.strip("[]"))
    if not rel:
        hits = search(name, 5)
        raise HTTPException(404, "没有叫这个名字的笔记。" + ("相近的有：" + "、".join(h["title"] for h in hits) if hits else ""))
    n = idx.notes[rel]
    return {"path": rel, "abs": str(KB_ROOT / rel), "content": read_text(KB_ROOT / rel),
            "backlinks": [idx.notes[r]["title"] for r in sorted(idx.back.get(rel, ()))],
            "missing_links": [t for t in n["links"] if not resolve(t)], "source_state": source_state(n)}


@router.post("/api/internal/kb/write")
def internal_write(body: KBReq, _: dict = Depends(require_internal)):
    sess, owner = _session_owner(body.session_id)
    if body.mode not in ("create", "update", "append"):
        raise HTTPException(400, "mode 只能是 create、update 或 append")
    if body.type and body.type not in TYPES:
        raise HTTPException(400, "type 只能是：" + "、".join(TYPES))
    try:
        return write_note(user=owner, by_name=owner["display_name"], title=body.title, body=body.content,
                          ntype=body.type or None, tags=body.tags, summary=body.summary, source=body.source,
                          status=body.status, mode=body.mode, path=body.path or None, session=sess["id"],
                          note=f"Claude（{sess['title'][:20]}）")
    except KBConflict as e:
        raise HTTPException(409, f"已经有同名笔记：{e}。先用 kb_read 看看内容，再用 mode=update（整体改写，保留有用的旧内容）或 append（在末尾追加）")


@router.post("/api/internal/kb/doc")
def internal_doc(body: KBReq, _: dict = Depends(require_internal)):
    _sess, owner = _session_owner(body.session_id)
    p = files_api.resolve_path(body.path, owner)   # 别人的工作区读不了
    if p.is_dir():
        raise HTTPException(400, "这是文件夹，请给一个文件")
    text = extract_text(p)
    limit = 30000
    start = max(0, body.offset)
    part = text[start:start + limit]
    more = start + limit < len(text)
    return {"text": part, "total": len(text), "start": start, "end": start + len(part), "more": more}
