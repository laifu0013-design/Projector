"""每个任务里给 Claude Code 用的工具（由中心服务生成的 --mcp-config 启动，通过环境变量拿到会话信息）。

- approve：权限确认（--permission-prompt-tool）。Claude 遇到不在自动放行名单里的操作时调用，
  本脚本把请求转给中心服务，等网页端或语音里有人同意/拒绝后再返回。
- skill_list / skill_checkout / skill_submit：在聊天里查看、修改、提交技能（提交后要管理员审核才生效）。
- ask_user：弹出选项卡片让用户点选（像 Claude 桌面端那样），等用户回答后返回。
- kb_search / kb_read / kb_write：查、读、写团队知识库（D:\\知识库）。
- read_document：把 Word、Excel、PPT、PDF 转成文字。
"""
import json
import logging
import os
import traceback
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

CENTER_URL = os.environ["CENTER_URL"]
CENTER_TOKEN = os.environ["CENTER_TOKEN"]
CENTER_SESSION = os.environ["CENTER_SESSION"]
HEADERS = {"Authorization": f"Bearer {CENTER_TOKEN}"}

_LOG = Path(__file__).resolve().parent / "data" / "logs" / "approval_mcp.log"
_LOG.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=_LOG, level=logging.INFO, encoding="utf-8", format="%(asctime)s %(message)s")
log = logging.getLogger("approval")

mcp = FastMCP("center")


async def _post(path: str, body: dict, timeout=60) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(CENTER_URL + path, headers=HEADERS, json={"session_id": CENTER_SESSION, **body})
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        raise RuntimeError(data.get("detail") or f"中心服务返回错误 {r.status_code}")
    return data


@mcp.tool()
async def approve(tool_name: str, input: dict, tool_use_id: str | None = None) -> str:
    """请求用户确认一个操作。"""
    # 不管出什么错都要返回合法的 allow/deny 文本，否则 Claude Code 会报
    # “Permission prompt tool returned an invalid result”，这一步直接失败
    try:
        log.info("[%s] 请求确认 %s", CENTER_SESSION, tool_name)
        async with httpx.AsyncClient(timeout=None) as client:
            r = await client.post(
                CENTER_URL + "/api/approvals", headers=HEADERS,
                json={"session_id": CENTER_SESSION, "tool_name": tool_name, "input": input},
            )
        decision = r.json() if r.status_code == 200 else {"allow": False, "message": f"中心服务拒绝了请求（{r.status_code}）"}
    except Exception as e:  # noqa: BLE001
        log.error("[%s] 确认失败：%r\n%s", CENTER_SESSION, e, traceback.format_exc())
        decision = {"allow": False, "message": f"确认通道出错（{type(e).__name__}），这一步没有执行，可以让用户重发一次"}
    log.info("[%s] %s → %s", CENTER_SESSION, tool_name, "同意" if decision.get("allow") else "拒绝")
    if decision.get("allow"):
        return json.dumps({"behavior": "allow", "updatedInput": input}, ensure_ascii=False)
    return json.dumps(
        {"behavior": "deny", "message": decision.get("message") or "用户拒绝了这个操作，不要重试同样的操作"},
        ensure_ascii=False,
    )


class Option(BaseModel):
    label: str = Field(description="选项文字，简短（推荐的选项在末尾加“（推荐）”）")
    description: str = Field("", description="一句话说明选这个会怎样")


class Question(BaseModel):
    question: str = Field(description="完整的问题，以问号结尾")
    header: str = Field("", description="很短的标签，最多 6 个字，比如“模板”“范围”")
    options: list[Option] = Field(description="2~4 个选项；用户总能另外选“其他”自己填写")
    multi_select: bool = Field(False, description="可以多选时设为 true")


@mcp.tool()
async def ask_user(questions: list[Question]) -> str:
    """在用户的屏幕上弹出选项卡片，让用户点选，等用户回答后返回结果。
    需要用户在几个方案里做选择、或确认关键信息时使用；一次最多 4 个问题。
    不要用它问显而易见的事情，也不要在回答文字里列选项等用户打字回复。"""
    try:
        async with httpx.AsyncClient(timeout=None) as client:  # 等用户回答，可能很久
            r = await client.post(CENTER_URL + "/api/questions", headers=HEADERS,
                                  json={"session_id": CENTER_SESSION, "questions": [q.model_dump() for q in questions]})
        if r.status_code != 200:
            return f"没能弹出问题（中心服务返回 {r.status_code}），请直接在回答里问用户。"
        return r.json().get("text", "用户没有回答。")
    except Exception as e:  # noqa: BLE001
        log.error("[%s] 提问失败：%r", CENTER_SESSION, e)
        return f"没能弹出问题（{type(e).__name__}），请直接在回答里问用户。"


_STATE = {"changed": "⚠ 原文件在卡片写完后改过，内容可能过时", "missing": "⚠ 原文件已经不在了"}


@mcp.tool()
async def kb_search(query: str, limit: int = 8) -> str:
    """在团队知识库里搜索：经验（以前怎么做的）、文件卡片（重要文件的摘要和位置）、主题（项目、客户等）。
    query 用空格分开几个关键词，比如“退货 流程”“客户A 合同”。
    遇到和公司业务、项目、客户、以前做过的事有关的问题，或要在文件库里找资料时，先用它查一下。"""
    try:
        d = await _post("/api/internal/kb/search", {"query": query, "limit": limit})
    except Exception as e:
        return f"出错了：{e}"
    if not d["results"]:
        return f"知识库里没有找到和“{query}”相关的笔记（知识库在 {d['root']}）。可以换个说法再搜，或者直接去文件库找。"
    lines = []
    for r in d["results"]:
        head = f"- [[{r['title']}]]（{r['type']}"
        head += f"，{r['status']}" if r["status"] != "有效" else ""
        head += f"，{r['updated']} {r['author']}）" if r["author"] else "）"
        lines.append(head)
        if r["summary"]:
            lines.append(f"  摘要：{r['summary']}")
        if r["source"]:
            lines.append(f"  原文件：{r['source']}" + (f" {_STATE[r['source_state']]}" if r["source_state"] else ""))
        if r["snippet"]:
            lines.append(f"  片段：{r['snippet']}")
    return "\n".join(lines) + "\n\n用 kb_read 读完整内容；文件卡片里写的原文件可以直接去读。"


@mcp.tool()
async def kb_read(name: str) -> str:
    """读一条知识库笔记的完整内容（name 用笔记标题，也就是 [[ ]] 里的名字）。会同时列出哪些笔记链接到了它。"""
    try:
        d = await _post("/api/internal/kb/read", {"name": name})
    except Exception as e:
        return f"出错了：{e}"
    tail = []
    if d["backlinks"]:
        tail.append("链接到这条的笔记：" + "、".join(f"[[{b}]]" for b in d["backlinks"]))
    if d["missing_links"]:
        tail.append("还没建的链接：" + "、".join(f"[[{b}]]" for b in d["missing_links"]))
    if d["source_state"]:
        tail.append(_STATE[d["source_state"]] + "，必要时重新读原文件并更新这张卡片")
    return f"文件：{d['abs']}\n\n{d['content']}" + ("\n\n" + "\n".join(tail) if tail else "")


@mcp.tool()
async def kb_write(title: str, content: str, type: str = "经验", tags: list[str] | None = None,
                   summary: str = "", source: str = "", mode: str = "create") -> str:
    """新建或修改知识库笔记（只有用户要求沉淀经验、整理文件进知识库、或明确让你记下来时才写）。
    title：标题，就是文件名和 [[链接]] 名，写成以后别人会搜的样子；
    content：Markdown 正文（不用写开头的 --- 属性，作者、日期会自动补上）；提到项目、客户、供应商、产品、人名时用 [[名字]] 链接；
    type：经验 / 文件卡片 / 主题；tags：几个标签；summary：一句话摘要；
    source：文件卡片对应的原文件或文件夹的完整路径（用来提醒原文件改过）；
    mode：create 新建（同名已存在会报错，防止重复）/ update 整体改写（先 kb_read，保留有用的旧内容）/ append 在末尾追加一段。
    不要写进密码、身份证号、银行卡号。"""
    body = {"title": title, "content": content, "type": type, "mode": mode,
            "tags": tags or None, "summary": summary or None, "source": source or None}
    try:
        d = await _post("/api/internal/kb/write", body)
    except Exception as e:
        return f"没写成：{e}"
    msg = f"已{'新建' if d['created'] else '更新'}笔记 [[{d['title']}]]：{d['abs']}"
    if d["missing_links"]:
        msg += ("\n其中这些链接还没有对应的笔记：" + "、".join(f"[[{x}]]" for x in d["missing_links"])
                + "。如果是被多处提到的重要主题（项目、客户等），可以建一条 type=主题 的笔记；不重要的可以不管。")
    return msg


@mcp.tool()
async def read_document(path: str, offset: int = 0) -> str:
    """把 Word（docx/doc/wps）、Excel（xlsx/xls/et）、PPT（pptx/ppt）、PDF 或文本文件转成文字读出来。
    path：文件完整路径。内容很长时分段返回，按提示用 offset 继续读。"""
    try:
        d = await _post("/api/internal/kb/doc", {"path": path, "offset": offset}, timeout=240)
    except Exception as e:
        return f"读不了：{e}"
    text = d["text"] or "（这个文件里没有读到文字）"
    if d["more"] or d["start"]:
        text += f"\n\n（共 {d['total']} 字，这是第 {d['start']}~{d['end']} 字" + \
                (f"；继续读用 offset={d['end']}）" if d["more"] else "）")
    return text


@mcp.tool()
async def skill_list() -> str:
    """列出当前用户能用的团队技能（名字、当前版本、说明）。"""
    try:
        data = await _post("/api/internal/skills/list", {})
    except Exception as e:
        return f"出错了：{e}"
    if not data["skills"]:
        return "这个用户还没有分配到任何技能。"
    return "\n".join(f"- {s['name']}（v{s['version']}）：{s['description']}" for s in data["skills"])


@mcp.tool()
async def skill_checkout(name: str = "") -> str:
    """把团队技能库里某个技能的当前版本复制到本会话的工作区，供你修改。
    name 留空时只返回“技能草稿”文件夹的路径，用来新建技能：在里面建一个以技能名命名的文件夹，写好 SKILL.md。
    修改完调用 skill_submit 提交。"""
    try:
        if not name:
            d = await _post("/api/internal/skills/draft-dir", {})
            return (f"技能草稿文件夹：{d['path']}\n新建技能：在里面建一个文件夹（名字用小写英文和短横线），"
                    "写一个 SKILL.md，开头用 --- 包住 name 和 description 两行，然后调用 skill_submit。")
        d = await _post("/api/internal/skills/checkout", {"name": name})
    except Exception as e:
        return f"出错了：{e}"
    files = "\n".join(f"  {f}" for f in d["files"])
    return (f"已取出技能 {name} 的 v{d['version']} 到：{d['path']}\n文件：\n{files}\n"
            "直接修改这个文件夹里的文件（不要改 SKILL.md 里的 name），改完调用 skill_submit 提交新版本。")


@mcp.tool()
async def skill_submit(path: str, changelog: str) -> str:
    """把工作区里的技能文件夹提交为新技能或已有技能的新版本，交给管理员审核。
    path：技能文件夹的完整路径（skill_checkout 给出的路径，或技能草稿文件夹里新建的文件夹）。
    changelog：这次改了什么、为什么改，用中文写清楚，管理员审核时会看。"""
    try:
        d = await _post("/api/internal/skills/submit", {"path": path, "changelog": changelog})
    except Exception as e:
        return f"提交失败：{e}"
    if d.get("updates_version"):
        ch = d.get("changes") or {}
        summary = "，".join(f"{k} {len(v)} 个" for k, v in (("新增", ch.get("added", [])), ("修改", ch.get("modified", [])),
                                                            ("删除", ch.get("removed", []))) if v) or "文件内容没有变化"
        return f"已提交技能 {d['name']} 的新版本（当前是 v{d['updates_version']}，{summary}），等管理员审核通过后生效。"
    return f"已提交新技能 {d['name']}，等管理员审核通过并分配后才能使用。"


if __name__ == "__main__":
    mcp.run(transport="stdio")
