# 综合部工作台三项增强 实现计划（v3）

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在现有 Projector 上增加“按用户 token 统计”“分区工作台”“文档 MD 持久缓存”三项能力，不改动现有会话、文件库、知识库的核心行为。

**架构：** 三个独立模块共用现有 SQLite：token 走 `usage_turns` 流水表；分区走 `sections` 表 + `sessions.section_id` + 知识库 frontmatter 的 `section` 字段；文档缓存走 `data/doc_cache/` 下的 MD 文件 + `doc_cache` 索引表。分阶段上线，每阶段可独立回滚。

**技术栈：** Python 3 / FastAPI / SQLite / 原生 JS（`static/*.js`）/ Claude Code stream-json。

## 本版（v3）修订摘要

v3 继续吸收评审意见，重点改动：

- 测试增加 `PROJECTOR_DATA` 隔离，并同时隔离 `KB_ROOT` / `WORKSPACE_ROOT`，不再碰真实库和 D 盘。
- 用量写入包 `try/finally`，写库失败不能跳过 `RUNS` 清理；以 `normalize_usage()` 结果决定是否落库。
- 费用 0 不再用 `or` 丢掉；`has_data` 和查询区间分开；个人 turns 接口拒绝 `user_id`。
- `add_message` 在同一把锁里取 `lastrowid`，锁外才 `broadcast` / `push_session`。
- 知识库 `section` 区分人和 Claude：人用 NoteBody 的 None/空串/名字控制保持/清空/设置；Claude 只补空分区，不搬已有分区。
- 分区筛选改为“只选该分区，未分类单独一项”，并补 `/api/kb/graph` 的 section 参数。
- 分区改名改为“内存保存原文 + 成功后才写历史 + 失败回滚文件/历史/分区名”。
- 文档缓存加入 Claude 拒绝规则，缓存文件名改为路径哈希。
- 删除首页“最近文件按分区过滤”（本期没有数据源）。
- `section_id=0` 表示清空/未分类，不传表示不改；新建和更新都校验分区。
- P2b（目录/关键词摘录）移出本期。
- 现有网页先做分区/用量入口（侧栏、`ensureSession`、知识库、管理页）；年鉴壳层适配另列，不把两套布局混在一起。
- 年鉴首页改为三列值班板，会话页作为正式工作界面；去掉大日期/罗盘/英文引文/假输入栏，字体改本机栈，1024px 用目录抽屉。
- 验收端口统一为现有默认 `8080`（`CENTER_PORT`）。

---

## 0. 已定决策

- **分区模型：团队统一分区。** 管理员维护分区（合同、采购、报表、人事、综合事务等），所有人可以给**自己的会话**归区，也可以编辑团队笔记及其分区；个人工作台只是“按分区看”的视图。
- **经验归属：团队共享。** 知识库仍然全员可见，分区是组织和检索维度，**不是权限边界**。
- **token 归属：按会话发起人。** 现有代码只有会话主人能发消息（`_owner_only`），所以统计到 `sessions.user_id` 即可；流水表额外留 `message_id`，以后要精确到轮次也有依据。
- **费用只作参考。** 订阅账号的 `total_cost_usd` 可能为 0，报表以 token 为主，费用标注“估算”。有 `modelUsage` 时只使用各模型的 `costUSD`，不叠加 `total_cost_usd`。
- **MD 缓存只处理被读过的文件。** 不做全库预处理，不做 OCR，不跨机器同步。
- **历史 token 不回填。** 统计从功能上线当天开始。
- **未分类 = 没有分区。** 知识库 frontmatter 不写 `section`（或写空值），不把“未分类”当成真实分区名；UI 上显示为“未分类”。
- **笔记权限沿用现状。** 登录用户都可以编辑团队笔记；分区只是笔记的一个属性。会话归区仍然只限主人（`_owner_only`），管理员也不能替别人改会话分区。
- **分区名全局唯一（含已归档）。** 归档后不删除记录；重建同名分区返回 409，提示先恢复归档分区，不采用“部分唯一索引”。
- **测试必须隔离。** 所有测试和接口验收走临时目录，不能连接 `data/center.db`，也不能在 `D:\` 下创建知识库或工作区。

## 1. 范围

**本期做：**

- 每轮对话记录 input / output / cache read / cache write tokens、模型、费用。
- 个人用量入口（用户菜单）+ 管理后台“用量”页；不依赖欢迎页。
- 团队分区表、会话归区、知识库经验归区、工作台按分区查看。
- 文档首次读取时转 MD 并持久缓存；文件变更自动失效；空文本和扫描件也缓存状态。
- 缓存目录加入 Claude 文件工具的拒绝规则。

**本期不做：**

- 分区级权限、项目成员、分区私有经验。
- 按分区隔离文件库访问；文件没有 `section_id`。
- 扫描件 OCR、PDF 图片文字识别。
- 历史 token 从 Claude Code JSONL 回填。
- P2b 大文件“目录/摘要/关键词摘录”读取模式（`mode=outline`、`query`）。它对 xlsx、PDF 没有稳定语义，也和现有 `offset` 分段冲突，后续单独做。
- 内容哈希校验（本机 NTFS，覆盖保存会改 `mtime_ns`，本期不加）。
- 扩大 Bash 旁路防护；拒绝规则维持现有 `Read/Edit/Write`，和今天的软隔离同一类。
- 新版年鉴 UI 的整体换肤（那是另一条工作线；本计划只做功能区，样式跟随最终 UI）。

## 2. 分期与依赖

| 阶段 | 内容 | 粗估 | 依赖 |
|---|---|---|---|
| P1 | token 采集落库 + 聚合接口 | 0.5–1 天 | 无 |
| P2 | 文档 MD 持久缓存（不含 P2b） | 1–1.5 天 | 无 |
| P3 | 分区数据模型 + API + 知识库基础 UI | 1.5–2 天 | 无 |
| P4 | 现有网页分区工作台（侧栏、会话、整理、管理分区） | 1–1.5 天 | P3 |
| P5 | 现有网页用量入口 + 管理用量页 | 0.5–1 天 | P1 |
| P4b/P5b | 年鉴壳层适配 | 0.5–1 天 | 壳层定稿 |

**关键顺序：** P1 越早上线越好，先积累数据；P2 独立、风险低；P3 后端先落，现有网页的 P4/P5 紧随其后；年鉴壳层的 P4b/P5b 等壳层定稿后再做，避免两套布局互相覆盖。

## 3. 文件结构

**新建：**

- `usage.py`：token 流水写入、归一化、聚合查询、`/api/usage/*` 路由。
- `sections.py`：分区 CRUD、权限校验、`/api/sections/*` 路由。顶层**不 import `kb`**，只在改名/统计笔记数时函数内延迟 import，避免循环导入。
- `tests/conftest.py`：模块顶层设置临时 `PROJECTOR_DATA` / `KB_ROOT` / `WORKSPACE_ROOT` / `FILES_ROOT`（必须在 import 前），再提供清库 fixture。
- `tests/test_usage.py`：用量归一化与聚合。
- `tests/test_doc_cache.py`：缓存命中、失效、空文本、扫描件、临时文件。
- `tests/test_sections.py`：分区 CRUD、会话归区、知识库归区、筛选、改名回滚。
- `tests/test_isolation.py`：确认测试不会写到真实 `data/`、`D:\知识库`、`D:\Projector工作区`。
- `requirements-dev.txt`（新建）：`pytest`。

**修改：**

- `store.py`：新增 `usage_turns`、`sections`、`doc_cache` 表；`sessions.section_id`、`users.last_section_id` 迁移；`DATA` 支持 `PROJECTOR_DATA`；新增 `db_insert`/`db_many` 辅助（同一把 `_db_lock` 内取 `lastrowid` / 批量提交）。
- `server.py`：`_drive()` 记录用量；会话创建/更新/视图带分区；挂载 `usage.router`、`sections.router`；`_deny_rules()` 增加缓存目录。
- `kb.py`：`extract_text()` 接持久缓存；索引/搜索/写入/图谱支持 `section`；分区改名批量更新 frontmatter 并支持回滚。
- `approval_mcp.py`：`kb_search`/`kb_write` 增加 `section` 参数；`read_document` 按缓存状态显示空文本/扫描件提示。
- `static/kb.js`：知识库 `KB.section`、列表/首页/图谱/星空分区筛选、编辑器分区字段、整理对话框分区。
- `static/admin.js`：管理后台“用量”“分区”两个页签；`show()` 分支写在 `renderReview()` 之前。
- `static/index.html`：侧栏分区组（`#kbNav` 与 `#sessions` 之间）、知识库分区 chips、编辑器分区下拉、管理页签按钮。
- `static/app.js`：`renderSections()`、`renderSessions()` 分区过滤、`ensureSession()` 传 `section_id`、会话头分区、用户菜单用量入口。
- 年鉴壳层的实际文件名待定；P4b/P5b 只改壳层文件，不再把现有网页的入口逻辑复制一遍。

---

## 4. P1：按用户 token 统计

### 4.1 数据模型

在 `store.py` 的 `executescript` 里新增：

```sql
CREATE TABLE IF NOT EXISTS usage_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    user_id INTEGER,
    message_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_write_tokens INTEGER,
    cost_usd REAL,
    created REAL,
    source TEXT DEFAULT 'result'
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_usage_turn_model
    ON usage_turns(message_id, model);
CREATE INDEX IF NOT EXISTS idx_usage_user_time ON usage_turns(user_id, created);
CREATE INDEX IF NOT EXISTS idx_usage_session ON usage_turns(session_id);
```

说明：

- 一轮对话可能涉及多个模型，`modelUsage` 有几项就写几行；没有 `modelUsage` 时写一行，`model` 取 `final.model` 或 `unknown`。
- `message_id` 指向本轮助手/错误消息的 `messages.id`，不指向后面的 event 消息。
- 唯一约束 `(message_id, model)` 防止重试/重复回调写重；写入用 `INSERT ... ON CONFLICT(message_id, model) DO NOTHING`，不要用 `INSERT OR IGNORE`（它会连 NOT NULL/CHECK 一起吞掉）。
- 汇总里的“轮次”是 `COUNT(DISTINCT message_id)`，一行是一个模型，不是一轮。
- 不修改 `messages` 表，避免影响现有读写。

### 4.2 采集

- [ ] 新建 `usage.py`，实现 `normalize_usage(final: dict) -> list[dict]`：

```python
def normalize_usage(final: dict) -> list[dict]:
    """把 Claude Code 的 result 事件拆成按模型的用量行。

    有 modelUsage 时只按模型拆行，费用只取各模型的 costUSD；
    没有 modelUsage、但有 usage 时，才退化成单行。
    """
    models = final.get("modelUsage") or {}
    rows = []
    if models:
        for model, u in models.items():
            if not isinstance(u, dict):
                continue
            rows.append({
                "model": model or "unknown",
                "input_tokens": _int(u.get("inputTokens", u.get("input_tokens"))),
                "output_tokens": _int(u.get("outputTokens", u.get("output_tokens"))),
                "cache_read_tokens": _int(u.get("cacheReadInputTokens", u.get("cache_read_input_tokens"))),
                "cache_write_tokens": _int(u.get("cacheCreationInputTokens", u.get("cache_creation_input_tokens"))),
                "cost_usd": _num(u.get("costUSD", u.get("cost_usd"))),
            })
    else:
        usage = final.get("usage") or {}
        if usage:
            # 不能用 or：0.0 是合法费用，会被当成没有。
            cost = final.get("total_cost_usd")
            if cost is None:
                cost = final.get("totalCostUSD")
            rows.append({
                "model": final.get("model") or "unknown",
                "input_tokens": _int(usage.get("input_tokens")),
                "output_tokens": _int(usage.get("output_tokens")),
                "cache_read_tokens": _int(usage.get("cache_read_input_tokens")),
                "cache_write_tokens": _int(usage.get("cache_creation_input_tokens")),
                "cost_usd": _num(cost),
            })
    return [r for r in rows if any(r[k] for k in
            ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost_usd"))]
```

- [ ] `normalize_usage` 的健壮性：
  - `modelUsage` 里不是 dict 的项直接跳过，其他模型照常写；模型名为空时写 `unknown`。
  - 兜底费用不能用 `or`：先看 `total_cost_usd` 键是否存在且值不是 `None`，再取 `totalCostUSD`；`0` 照写，两个键都没有才记 `None`。`_num(None)` 返回 `None`，`_num(0)` 返回 `0.0`。
  - `thinkingTokens` 已经包含在 `outputTokens` 里，不要再加一遍。
  - 有 `modelUsage` 时继续只用各模型 `costUSD`，不走上面的兜底。
- [ ] `record_turn(session_id, user_id, message_id, final) -> int`：
  - 先调用 `normalize_usage`；返回空列表时直接返回 `0`，不写库。
  - 不打开新的 SQLite 连接，全部走 `store.db()` 系列；新增一个 `store.db_many(sql, rows)`（或 `db_executemany`），在**一次 `_db_lock` + 一次 commit** 里写完本轮所有模型行；失败整批回滚，避免写到一半留下半轮。
  - 唯一冲突和真正的写入失败要分开：
    - 用 `INSERT ... ON CONFLICT(message_id, model) DO NOTHING`，不要用 `INSERT OR IGNORE`；后者会把 NOT NULL、CHECK 等错误也一起忽略。
    - 唯一冲突跳过该行，其余行照常提交。
    - NOT NULL、磁盘错误等异常在释放锁之前 `rollback()`，一行不留，再按失败路径记日志。
  - 返回值用提交前后 `connection.total_changes`（或 `changes()` 的差值）计算实际写入行数；冲突跳过的行不算。
  - 每行必须写 `created=time.time()`。`store.db()` 不会自动填时间；`created` 为空会让 `date(created, 'unixepoch', 'localtime')` 变成 NULL，本月/自定义区间都查不到。
  - `source` 在 `final.is_error` 时写 `error`，否则 `result`。
  - 整个函数自己包 `try/except`：失败写 `DATA/logs/usage.log`，返回 `0`，**绝不能把异常抛给 `_drive()`**。
  - 日志写失败也要吞掉，不能再抛。
- [ ] `usage.log_failure(sid, where)`：写 `DATA/logs/usage.log`，自己吞异常，供 `_drive` 兜底调用。
- [ ] 修 `server.py:add_message()` 的消息 id 竞态：
  - 现在是“INSERT 一次 → UPDATE sessions 一次 → 再 ORDER BY id DESC 查一次”，三次 `db()` 之间锁会放开；同一会话里的同意/拒绝/超时 event 可能在这中间插入，导致查回 event 的 id。
  - 改成在**同一次 `_db_lock`** 里用 `_db.execute` 完成 INSERT、UPDATE sessions、按 `lastrowid` 取回消息；然后提交。
  - 持锁期间不要调用 `db()`、`db_many()`、`session_view()`；`push_session()` 内部会再进 `db()`，`session_view()` 里的 `get_user` 也会。这些都必须放到**释放锁之后**，连同 `broadcast()`。
  - 不要改成可重入锁；`_db_lock` 保持不可重入。
  - `record_turn` 必须拿这个 `lastrowid` 对应的助手/错误消息，不能拿后面的 event。
- [ ] 修改 `server.py:_drive()`：
  - 拿到 `final` 之后，把“UPDATE claude_session、`add_message` 四个分支、record_turn、warn_perm event”整段放进同一个 `try`；`except Exception` 记日志，最内层 `finally` 无论如何 `RUNS.pop`、`run.done.set()`、`push_session`。
  - 各分支的 `add_message(...)` 结果保存为 `msg`；后面的 `warn_perm` event 用独立调用，不要覆盖 `msg`。
  - `record_turn` 继续自己吞异常，`_drive` 外层再保一层。
  - 写成结构：

```python
try:
    if final.get("session_id"):
        db("UPDATE sessions SET claude_session=? WHERE id=?", (final["session_id"], run.sid))
    msg = None
    if run.stopped:
        msg = add_message(...)
    elif run.timed_out:
        msg = add_message(...)
    elif final.get("is_error"):
        msg = add_message(...)
    else:
        msg = add_message(...)
    try:
        if msg:
            usage.record_turn(run.sid, run.owner_id, msg["id"], final)
    except Exception:  # record_turn 自己也会兜，这里再保一层
        usage.log_failure(run.sid, "record_turn")
    if "warn_perm" in run.warnings:
        add_message(...)  # 用独立调用，不要赋给 msg
except Exception:
    usage.log_failure(run.sid, "drive")
finally:
    try:
        for a in pending_for(run.sid) + questions_for(run.sid):
            if not a["future"].done():
                a["future"].set_result((False, "任务已结束"))
    finally:
        RUNS.pop(run.sid, None)
        run.done.set()
        push_session(run.sid)
```

  - 注意：`pending` / `questions` 只在 `finally` 清一次即可；`RUNS.pop` / `done.set` / `push_session` 必须在最内层 `finally`。
  - 三个终止分支的 `meta` 要带状态：`run.stopped` 写 `stopped: true`，`run.timed_out` 写 `timed_out: true`，`final.is_error` 写 `error: true`；P4 的会话状态据此派生，不靠猜文案。
- [ ] 修 `server.py:start_turn()`：
  - `RUNS[sid] = Run(...)` 之后写用户消息的 `add_message` 也要包 `try/except`；失败时 `RUNS.pop(sid, None)` 再抛，否则会话会一直显示“进行中”。
  - `build_command` / `build_prompt` / `add_message` 任意一步失败都清掉 `RUNS[sid]`。
- [ ] `messages.meta` 保留现有 `cost`，含义仍是 `total_cost_usd`；为 0 时维持现在“不显示”的行为，不为了和用量页对齐去改 `app.js`。
  - 用量页、管理页和 CSV 一律以 `usage_turns.cost_usd` 之和为准。
  - 完成标准里的“数字可对账”只比较 `/api/usage/me` 和 `/api/admin/usage`，不和聊天页的 `meta.cost` 对账。

**边界：**

- `run.stopped` / `run.timed_out` 时 `final` 可能没有 `usage`/`modelUsage`，`normalize_usage` 返回空，不写行，不报错。
- `final.is_error` 但有 `modelUsage`/`usage` 时照常记录，`source='error'`。
- 字段缺失记 `0` 或 `NULL`，不要用估算值填。
- 只有 `modelUsage`、没有 `usage` 的轮次必须记录（v2 起修正的重点）。
- 写用量失败、写消息失败、UPDATE sessions 失败都不能让 `RUNS` 残留。

### 4.3 聚合接口

- [ ] `usage.py` 定义 `router = APIRouter()`，提供：

```python
from fastapi import HTTPException, Query

@router.get("/api/usage/me")
def my_usage(from_: str = Query("", alias="from"), to: str = "", all: bool = False,
             user: dict = Depends(current_user)):
    return usage_summary(user_id=user["id"], date_from=from_, date_to=to, all_time=all)

@router.get("/api/admin/usage")
def all_usage(from_: str = Query("", alias="from"), to: str = "", all: bool = False,
              admin: dict = Depends(require_admin)):
    return usage_summary(user_id=None, date_from=from_, date_to=to,
                         all_time=all, by_user=True)

@router.get("/api/usage/me/turns")
def my_turns(limit: int = 20, user_id: int | None = None,
             user: dict = Depends(current_user)):
    # 个人接口永远只用 current_user；多传 user_id 直接 400，不能静默忽略。
    if user_id is not None:
        raise HTTPException(400, "个人用量接口不能指定 user_id")
    return recent_turns(user_id=user["id"], limit=max(1, min(limit, 100)))

@router.get("/api/admin/usage/turns")
def admin_turns(user_id: int, limit: int = 20, admin: dict = Depends(require_admin)):
    return recent_turns(user_id=user_id, limit=max(1, min(limit, 100)))
```

- [ ] 日期口径：
  - 查询参数是 `from` / `to`，用 `YYYY-MM-DD`，含首尾两天。
  - 只有两侧都空才默认本月：本地当月 1 日到下月 1 日减一天。12 月的下月是次年 1 月。
  - 只传一侧、解析失败、日期不存在（如 2026-02-30），都返回 400。
  - `all=True` 时**忽略 `from` / `to`**：不要先套“没传就用本月”，也不要对空日期做 366 天校验；直接查全部。
  - `all=False` 时自定义区间最多 366 天，超出返回 400；`from` 晚于 `to` 返回 400。
  - `daily` 在 `all=True` 时最多返回最近 366 天，避免一次返回几年数据；`total`、`by_model`、`by_user` 仍是全部。
  - 日期分组用 SQLite：`date(created, 'unixepoch', 'localtime')`。
- [ ] `has_data` 与本次查询区间无关：个人接口看该用户在 `usage_turns` 是否至少有一行；管理接口看全库是否至少有一行。
  - 区间内没有行但 `has_data=true`：`total` 全是 0、`daily=[]`，页面显示 0，不能显示“还没有采集到用量”。
  - 只有 `has_data=false`（从未采集过）才显示“还没有采集到用量”。
- [ ] `total.cost`：区间内所有 `cost_usd` 都是 NULL 时返回 `null`，不要收成 0；有非 NULL 值时求和。页面 `cost == null` 显示“未采集费用”，`cost === 0` 显示“0（估算）”。
- [ ] `usage_summary` 返回：

```json
{
  "range": {"from": "2026-10-01", "to": "2026-10-31", "all": false},
  "has_data": true,
  "total": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost": null, "turns": 0},
  "daily": [{"date": "2026-10-30", "input": 0, "output": 0, "cache_read": 0, "cost": 0, "turns": 0}],
  "by_model": [{"model": "claude-sonnet-4-5", "input": 0, "output": 0, "cost": 0}],
  "by_user": [{"user_id": 1, "display_name": "张三", "input": 0, "output": 0, "cost": 0, "turns": 0}]
}
```

- [ ] `total.turns`、`daily.turns`、`by_user.turns` 都用 `COUNT(DISTINCT message_id)`。
- [ ] `recent_turns` 按 `message_id` 聚合成“轮”，返回：

```json
[{"message_id": 123, "session_id": "...", "session_title": "...",
  "created": 1760000000, "models": ["claude-sonnet-4-5"],
  "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost": 0,
  "preview": "这一轮用户/助手内容的前 80 字"}]
```

  - 按 `created DESC, message_id DESC` 排序，默认 20 条，最多 100 条。
  - `preview` 取同一会话里、这条助手消息之前最近的一条 `role='user'`，最多 80 字；没有用户消息再用助手或错误消息。不要取紧挨着的上一条，那经常是“已同意/已拒绝”的 event。
  - 个人接口只返回本人的行，不接受 `user_id`；管理员另走 `/api/admin/usage/turns?user_id=`，`user_id` 必填。
- [ ] 在 `server.py` 挂载 `app.include_router(usage.router)`。

### 4.4 测试

- [ ] `test_normalize_prefers_model_usage`：同时有 `usage` 和 `modelUsage` 时，按模型拆行。
- [ ] `test_normalize_model_usage_without_usage`：只有 `modelUsage`、没有 `usage`，仍然返回行。
- [ ] `test_normalize_missing_usage`：`final={}` 返回空列表。
- [ ] `test_model_usage_cost_not_double_counted`：有 `modelUsage` 时，总费用只等于各模型 `costUSD` 之和，不叠加 `total_cost_usd`。
- [ ] `test_fallback_cost_zero_is_kept`：没有 `modelUsage`、`total_cost_usd=0.0` 时 `cost_usd` 是 0，不是 None；只有两个键都缺才 None。
- [ ] `test_summary_turns_distinct_message`：同一 `message_id` 两个模型只算一轮。
- [ ] `test_summary_groups_by_day_and_user`：插入 3 行，验证总数、按天、按人。
- [ ] `test_me_only_returns_own_rows`：两个用户的数据不串。
- [ ] `test_recent_turns_groups_by_message`：最近 20 轮按消息聚合，不按模型行聚合。
- [ ] `test_recent_turns_preview_skips_events`：预览取前面最近的 user 消息，不是“已同意”event。
- [ ] `test_me_turns_rejects_user_id`：普通用户带别人的 `user_id` 返回 400，不会返回别人的行。
- [ ] `test_admin_turns_requires_user_id`：管理员接口不传 `user_id` 返回 422/400。
- [ ] `test_record_turn_failure_does_not_raise`：mock `db` 抛异常，`record_turn` 返回 0 且不抛。
- [ ] `test_record_turn_unique_conflict_keeps_other_rows`：同一 `(message_id, model)` 已存在时跳过该行，其余模型照常写入。
- [ ] `test_record_turn_not_null_rolls_back`：制造 NOT NULL 失败，整批回滚，一行不留。
- [ ] `test_record_turn_sets_created`：写入行 `created` 非空，`date(created,'unixepoch','localtime')` 能分到当天。
- [ ] `test_usage_all_ignores_from_to`：`all=True` 同时传空/超长 from/to 也返回全部，不触发 366 天校验。
- [ ] `test_usage_date_validation`：只传一侧、非法日期、2 月 30 日都 400；12 月默认下月是次年 1 月。
- [ ] `test_usage_has_data_independent_of_range`：区间外有数据、区间内没有时 `has_data=true`、`total` 为 0、`daily=[]`。
- [ ] `test_usage_cost_null_vs_zero`：区间全 NULL 时 `total.cost=null`；有 0 行时返回 0。
- [ ] `test_add_message_returns_inserted_id`：插入时并发 event，`record_turn` 仍拿到助手/错误消息的 id。

### 4.5 验收

- 发起一轮真实对话后，`usage_turns` 出现一行或多行，字段与 `result` 事件一致。
- 只有 `modelUsage`、没有 `usage` 的轮次也能落库。
- `/api/usage/me?from=...&to=...` 总数等于该用户区间内所有行之和；管理员能看到所有人。
- 一轮里 `cache_read_tokens` 单独显示，不被合并进 `input_tokens`。
- 订阅账号费用为 0 时页面显示“0（估算）”，不显示“免费”。
- 区间内没有行但历史上采集过时，页面显示 0 而不是“还没有采集到用量”；只有从未采集过才显示空状态。
- 用量页和后台页的数字只与这两个接口对账，不和聊天页的 `meta.cost` 对比。
- 写用量失败时，这一轮对话照常结束，会话不会卡在“进行中”。

---

## 5. P2：文档 MD 持久缓存

### 5.1 数据模型

在 `store.py` 新增：

```sql
CREATE TABLE IF NOT EXISTS doc_cache (
    source_path TEXT PRIMARY KEY,
    size INTEGER,
    mtime_ns INTEGER,
    extractor_version INTEGER,
    md_name TEXT,
    chars INTEGER,
    bytes INTEGER,
    status TEXT,
    hits INTEGER DEFAULT 0,
    created REAL,
    used REAL
);
```

- `source_path` 用 `os.path.normcase(str(p.resolve()))`，避免 Windows 大小写/斜杠差异造成重复缓存。
- `md_name` 用 `sha256(source_path).hexdigest()[:32] + ".md"`，**不要用原文件名**，避免从缓存目录泄露文件名。
- `status` 取值：`ok`（有正文）、`empty`（确实没有文字）、`scan`（有页面但几乎没有文字，疑似扫描件/图片）。
- `bytes` 是 MD 文件大小，供清理时按体积统计；`chars` 是正文字符数。
- `hits` 是 L2 命中次数；管理接口的命中数用 `SUM(hits)`。
- 本阶段不需要 `error` 列，建表语句里不写；提取异常走日志，不落缓存。
- 提取抛异常时不写缓存，保持现有错误提示。特别是 `_pdf()` 在 `ImportError`（没装 pypdf）时**必须抛异常**，不能 `return` 一段安装说明；异常 message 保留安装说明，让 `read_document` 原样显示。否则这段说明会被当成正文缓存，装上 pypdf 后 size/mtime 没变还会继续命中旧缓存。

### 5.2 缓存流程

- [ ] 在 `kb.py` 顶部加 `DOC_CACHE_DIR = DATA / "doc_cache"`，启动时 `mkdir(parents=True, exist_ok=True)`；`EXTRACTOR_VERSION = 1`。
  - 在 `EXTRACTOR_VERSION` 旁写清楚它管三件事：xlsx 每表 400 行上限（`SHEET_ROWS`）、非 Office 文件 5 MB 上限、扫描件 20 字门槛。这三处任一改动都要把版本号 +1。
- [ ] 把现有 `extract_text()` 拆成两层：
  - `_extract_uncached(p) -> tuple[str, str]`：返回 `(正文, status)`，只做解析，**不碰 L1/L2 缓存**。
  - `extract_document(p) -> dict`：查 L1 内存缓存，再查 L2 `doc_cache` + MD 文件，最后调 `_extract_uncached` 并回写；返回 `{text, status, cached, source_mtime, chars}`。
  - `extract_text(p)`：薄包装，返回 `extract_document(p)["text"]`，保持现有调用方兼容；`internal_doc` 不要再用它，直接调 `extract_document`。
- [ ] L1 和 L2 使用**同一套键**：`(normcase(str(p.resolve())), st.st_size, st.st_mtime_ns, EXTRACTOR_VERSION)`。L1 是 `dict[key, tuple[text, status]]`。
- [ ] 缓存命中条件：`source_path` 命中、`size`/`mtime_ns`/`extractor_version` 全一致、MD 文件存在，**且 MD 文件大小等于索引里的 `bytes`**；大小对不上就当未命中，重新提取。
- [ ] 写缓存：先写 `MD 文件.tmp`，`os.replace` 原子替换；再 `INSERT ... ON CONFLICT(source_path) DO UPDATE` 更新索引。
  - 索引更新失败时，**删掉刚写入的 MD 文件**，不要留下“旧索引 + 新文件”的状态；记日志，返回本次正文，不要变成 500。
  - Windows 上 `os.replace` 可能因文件正被读取而失败；按缓存写失败处理，记日志并返回本次正文。
- [ ] 空正文：`_extract_uncached` 返回 `("", "empty")`，写空 MD 文件（保证“文件存在”条件成立），`status='empty'`。
- [ ] 扫描件：PDF 有页面但去空白/页码后少于 20 字，**保留提取出的页文本**（可以只有页码骨架），返回 `(原文本, "scan")`；不要返回 `("", "scan")`，否则页码结构会丢。`status='scan'`。**不要把“疑似扫描件”提示文案写进 MD 正文**；提示由 `internal_doc` / `read_document` 读取时按 `status` 合成。
- [ ] 老格式（`.doc/.xls/.ppt/...`）：先转成临时 `.docx/.xlsx/.pptx`，对临时文件只调用 `_extract_uncached`，**不写 L1/L2**；最终结果按原文件路径、原文件 `size`/`mtime_ns` 缓存。
- [ ] 同一路径并发提取用 `threading.Lock` 串行化；锁表用 `dict[str, Lock]` + 全局锁保护。锁按路径串行，同一路径的新旧 `mtime` 不能并行写。
- [ ] L2 命中时 `hits = hits + 1`；`used` 仍按每小时最多写一次。
- [ ] L1 仍设条数上限（沿用现有 30 条左右），不能无限增长。

### 5.3 清理与可观测

- [ ] `prune_doc_cache(max_bytes=2 * 1024**3)`：按 `used` 从旧到新删除 MD 和索引行，直到总量低于上限；同时删除索引里没有的孤儿 MD 文件、清理 MD 文件已丢失的索引行。
- [ ] 用 `settings` 记 `doc_cache_last_prune`；启动时和每次写缓存前检查，超过 7 天才执行 `prune_doc_cache`。
  - 启动时用后台线程执行，避免 2 GB 清理拖慢服务启动。
  - 清理线程只调用 `store.db()`，**不要再包一层 `_db_lock`，也不要开第二根 SQLite 连接**；`_db_lock` 不可重入，锁里再调 `db()` 会死锁。
  - 每次写缓存前只做一次时间戳比较，不每次都扫目录。
- [ ] `GET /api/admin/doc-cache`：返回条数、总大小、命中数（`SUM(hits)`）、最近使用时间。
- [ ] `POST /api/admin/doc-cache/prune`：管理员手动清理。
- [ ] 日志：提取失败、缓存写失败写 `data/logs`，不影响本次返回。

### 5.4 读取接口增强

- [ ] `internal_doc` 改调 `extract_document(p)`，返回 `status`、`cached`、`source_mtime`、`chars`，**`text` 只含正文**。
  - `total`、`start`、`end`、`more` 都按正文计算；提示文案不能拼进 `text`，否则会占掉 30000 字窗口，翻页还会重复。
- [ ] `read_document` **先看 `status` 再决定提示**，提示只出现在返回字符串里：
  - `status='empty'` 且 `start == 0`：整段就是“这个文件里没有文字”，不要再写 `text or "没有文字"`。
  - `status='scan'`：先返回本段正文，再提示“这个 PDF 几乎没有文字，可能是扫描件或图片，需要 OCR 才能读”，最后才是“共 N 字”。翻页（`start > 0`）不重复提示。
  - `status='ok'`：正常返回正文，不显示扫描件提示。
  - 不要用 `d["text"] or "（这个文件里没有读到文字）"` 把空正文和扫描件混在一起。
- [ ] 不实现 `mode=outline` / `query`；P2b 后续单独做。

### 5.5 安全：缓存目录不能被 Claude 直接读

- [ ] `server.py:_deny_rules()` 增加 `kb.DOC_CACHE_DIR`（或 `DATA / "doc_cache"`），生成 `Read/Edit/Write(/**)` 拒绝规则。
- [ ] 缓存文件名用路径哈希，不暴露原文件名；这只是纵深防御，真正的隔离靠拒绝规则。
- [ ] `test_doc_cache.py::test_deny_rules_blocks_doc_cache`：断言规则里包含缓存目录。

### 5.6 测试

- [ ] `test_second_read_uses_cache`：第一次调用提取函数，第二次不再调用；断言返回文本一致。
- [ ] `test_mtime_change_invalidates`：改文件 mtime/size 后重新提取。
- [ ] `test_extractor_version_change_invalidates`：改版本号后重新提取。
- [ ] `test_empty_text_cached`：空文本写 `status='empty'`，第二次不再提取。
- [ ] `test_scan_pdf_cached_as_scan`：扫描件写 `status='scan'`，保留页文本/页码骨架，返回提示但不把提示存进正文。
- [ ] `test_pypdf_missing_not_cached`：mock `_pdf` 的 `ImportError`，第一次抛异常，L1/L2 都没有该 PDF 的行；装上 pypdf 后能重新提取。
- [ ] `test_temp_conversion_not_cached`：`.doc` 转临时 `.docx` 时，临时路径不出现在 L1/L2。
- [ ] `test_l1_l2_same_key`：L1 命中和 L2 命中对同一路径、大小写、版本号结果一致。
- [ ] `test_prune_respects_last_run`：7 天内不重复清理；超过 7 天才执行。
- [ ] `test_prune_uses_store_db`：清理只调用 `store.db()`，不自己拿 `_db_lock`；在测试里不应死锁。
- [ ] `test_index_failure_deletes_new_md`：索引更新失败时刚写入的 MD 被删除，不留“旧索引 + 新文件”。
- [ ] `test_hit_checks_size_and_counts_hits`：MD 大小与 `bytes` 不一致时未命中；L2 命中后 `hits` +1。
- [ ] `test_scan_hint_not_in_pagination`：提示不进入 `internal_doc.text`，`total`/`offset` 只算正文；翻页不重复提示。

### 5.7 验收

- 同一个 docx 连续读两次，第二次明显更快，且不会重复走 zip 解析。
- 修改文件后再次读取，内容更新。
- 扫描版 PDF 返回“没有文字/疑似扫描件”，缓存状态为 `scan`，保留页文本/页码骨架，不缓存成有内容的假结果。
- 纯空文本文件缓存状态为 `empty`，第二次不再解析。
- 没装 pypdf 时读 PDF 返回错误提示，不写 L1/L2；装上 pypdf 后能重新提取。
- Claude 不能直接 `Read` 缓存目录；尝试读取会被拒绝规则挡住。
- 缓存目录可被管理员查看总大小并清理。

---

## 6. P3：分区数据模型与 API

### 6.1 数据模型

```sql
CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    color TEXT DEFAULT '',
    sort INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0,
    created REAL
);
CREATE INDEX IF NOT EXISTS idx_sections_active ON sections(archived, sort, id);
```

迁移：

```python
("sessions", "section_id INTEGER"), ("users", "last_section_id INTEGER")
```

- `section_id` 为 `NULL` 表示“未分类”，不强制建一条“未分类”记录。
- 删除分区用“归档”（`archived=1`），不物理删除会话/笔记。
- `name` 保持全局唯一（含已归档）。归档后重建同名分区返回 409，提示先恢复；**不使用 `WHERE archived=0` 的部分唯一索引**。原因：笔记 frontmatter 按分区名引用，同名归档 + 同名新建会让老笔记串到新分区。
- 恢复归档分区用 `PATCH /api/sections/{id}` 把 `archived` 设回 0；恢复时如果已有同名激活分区，返回 409。
- `__uncategorized__` 是 API 保留值，不能作为分区名创建；UI 上的“未分类”只是标签，不落库。

### 6.2 权限与规则

- 所有登录用户：查看未归档分区列表、给自己的会话归区、编辑任意团队笔记（含修改笔记分区）。
- 管理员：新建、改名、排序、归档、恢复分区。
- 会话归区：只限会话主人（沿用 `_owner_only`），管理员不能替别人改。
- 改名：更新 `sections.name`，并批量更新知识库 frontmatter 里 `section` 等于旧名的笔记；失败要回滚文件和分区名。
- 归档：不再出现在新建会话/新笔记的选择器里；已有会话和笔记仍显示原分区名。
- 新建分区名不能和任何现存分区（含已归档）重名。

### 6.3 后端接口

- [ ] 新建 `sections.py`：
  - `list_sections(user, include_archived=False)`：返回未归档分区 + 每个分区的 `session_count`、`note_count`；管理员传 `include_archived=1` 时额外返回已归档分区，供恢复 UI 使用。
    - 普通用户 `session_count` 只统计自己的会话；管理员统计全团队会话数。
    - `note_count` 统计全团队笔记（知识库本来就是共享的）。
    - 延迟 `import kb` 计算笔记数，避免顶层循环导入。
  - `create_section(name, color, sort)`：管理员；`name.strip()`，拒绝空串和 `__uncategorized__`；重名（含归档）返回 409。
  - `update_section(id, name, color, sort, archived)`：管理员；改名时 `name.strip()`，拒绝空串和保留值；**把重名预检、`sections.name` 更新和文件修改整体委托给 `kb.rename_section(...)`**，不要先自己改一次 `sections.name` 再调用，避免双重更新和回滚错位。
  - `reorder_sections(ids)`：管理员。
  - `section_name(section_id, include_archived=True)`：供 `server.py`、`kb.py` 复用；显示老会话/老笔记的归档分区名时必须能查到。
  - `require_active(section_id)` / `require_active_name(name, current="")`：校验分区存在且未归档；`current` 相同则放行，避免改标题时被已归档分区连带拒绝。校验通过后返回数据库里那一行的 `name` 原文（`COLLATE NOCASE` 下用户传 `hr`、库里是 `HR` 时，写入和改名都用 `HR`）。
  - `active_name_for_session(sess)`：新建/补空笔记的默认分区用；会话分区已归档时返回空字符串。
  - 顶层只 import `store`，**不 import `kb`**；`list_sections` 和改名在函数内延迟 import。
- [ ] `kb.py`：
  - `_parse` 返回 `"section": str(meta.get("section") or "")`；`ORDER` 加入 `"section"`。
  - `brief()` 返回 `section`。
  - `search(q, limit=20, ntype="", section="")`：
    - `section == ""`：全库。
    - `section == "__uncategorized__"`：只要 `section == ""` 的笔记。
    - 其他：**只保留 `section == 该名字` 的笔记**，不再并入未分类。
  - `write_note(..., section: str | None = None, fill_empty_section: str = "")`：
    - 先 `extra_meta.pop("section", None)`。正文 frontmatter 里的 `section` 不能绕过校验，也不能被 `setdefault` 写进 meta。
    - `section is not None`（人通过 NoteBody 显式改）：先 `section = section.strip()`；空串表示清空，删除 `meta["section"]`；非空用 `sections.require_active_name(name, current=旧分区)` 拿回数据库里的原文再写入，不要写用户输入的大小写。
    - `section is None` 且 `fill_empty_section`（Claude 新建或补空）：笔记不存在、或原分区为空时，写入这个会话的未归档分区（同样用数据库原文）；原分区已有名字时保持原值，忽略 `fill_empty_section`。
    - `section is None` 且没有 `fill_empty_section`：保持原分区；新建则未分类。
  - `rename_section(section_id, old, new, user)`：
    - 在 `_write_lock` 里完成**重名预检**、`UPDATE sections SET name=?`、文件修改；不要预检在锁外、更新在锁内。
    - 先 `index(force=True)` 找出所有 `section == old` 的笔记，把原文读入内存 `originals`。
    - 逐篇解析 frontmatter，改 `meta["section"] = new`，再 `dump_fm(meta) + 正文`；**不要对文件原文做字符串替换**，带冒号/引号的值会被 `_q` 加 JSON 引号，原文替换对不上。
    - **不走 `_save`**：直接 `tmp + os.replace` 原子写文件，成功前不写 `kb_history`。
    - 全部文件写成功后，再逐篇 `_snapshot` 记录新内容；记录本次插入的 history id，供失败时清理。
    - rename 的历史插入用不做 `HISTORY_KEEP` 裁剪的 helper（或先备份将被裁掉的行），避免回滚时删了新行却丢了旧历史。
    - 任一步失败：用 `originals` 原子写回文件；删除本次已插入的 history id；恢复 `sections.name = old`；再抛原异常。
    - 回滚写入本身失败：写日志，并在异常信息里注明“有 N 篇可能未回滚”，不能静默。
    - `finally: invalidate()`。
  - 改名前把 `{section_id, old, new}` 写进 `settings` 的 `section_rename_in_progress`；成功后删除。启动时在 `_write_lock` 里调用 `recover_section_rename()`：
      - `sections.name` 已经是 `new`：说明崩溃在文件阶段，把仍然是 `old` 的笔记补写成 `new`，再删标记。
      - `sections.name` 还是 `old`：说明还没提交改名，直接删标记。
      - 分区不存在：删标记并记日志。
  - `kb_home(section="")`：
    - `notes` **仍返回全库**（带 `section`），供聊天里的 `[[链接]]`、`kbFind`、`kbSuggest` 解析；否则选一个分区后，其他分区的链接会显示成“还没有这条笔记”。
    - `stats`、`tags`、`hubs`、`ghosts`、`recent` 按 section 过滤后计算；前端笔记列表用 `brief.section` 过滤。
    - `_recent()` 不能直接查全量 `kb_history` 就返回；要按过滤后的笔记路径集合收窄，否则选“合同”仍会看到其他分区的最近修改。
    - `ghosts` 用过滤后的笔记重新计算，不要直接用全局 `IDX.ghosts`。
    - 增加 `today_new`：今天新建的经验/文件卡片，来源 `kb_history` 里 `note='新建'` 且 `created` 在本地今天范围内，再关联当前索引拿标题/类型/路径；没有就返回空数组。不要拿 `stats.week` 冒充“今日新增”。
  - `kb_graph(section="")`：新增 section 参数；节点按同一规则过滤，边只保留两端都在结果里的，ghost 用过滤后的笔记重算，不要用全库的 `IDX.ghosts`。
  - `/api/kb/cards`、单篇历史、反向链接保持全库，不按分区过滤。
- [ ] `server.py`：
  - `NewSession` 增加 `section_id: int | None = None`；`UpdateSession` 增加 `section_id: int | None = None`。
  - `new_session` 端点要把 `body.section_id` 传进 `create_session(...)`；现在只传了标题和工作目录。
  - `create_session(..., section_id=None)`：
    - `section_id is None`：用 `users.last_section_id`，但它必须仍然存在且未归档，否则回退未分类。
    - `section_id == 0`：会话写 `NULL`，`users.last_section_id` 也写 `NULL`；**不要把 0 先收成 None**，否则又回退到上次分区。
    - 其他值：校验存在且未归档，写入会话和 `last_section_id`。
  - `session_view` 返回 `section_id`、`section_name`、`section_archived`、`status`、`duration_seconds`、`message_count`、`file_count`：
    - `status`：`pending`（有待确认/问题）> `running` > `stopped`（最后一轮被停止/超时/出错）> `done`。不要再用“已归档”表示结束。
    - `duration_seconds`：优先取最后一轮助手/错误消息的 `meta.seconds`；没有就退回 `updated - created`，再没有显示“—”。
    - `message_count`：`messages` 聚合计数。
    - `file_count`：`links.manifest` 的文件数；没有 links 时为 0。
  - `list_sessions` 用聚合查询一次补齐 `status` / `message_count` / `file_count`，不要在模板里逐会话 `db()` 造成 N+1。
  - `list_sessions(section_id: int | None = None, user=Depends(current_user))`：函数签名必须声明这个查询参数，否则 FastAPI 会丢掉 `?section_id=`。
    - `section_id is None`：全部。
    - `section_id == 0`：`section_id IS NULL`（未分类）。
    - 其他：只返回该分区。
    - 判断用 `is None` / `== 0`，不要写 `if section_id`；0 是假值，未分类会被当成全部。
  - `PATCH /api/sessions/{sid}`：
    - `section_id` 不传（`None`）：不改；显式传 JSON `null` 也按不传处理。前端要清空必须传 `0`。
    - `section_id == 0`：清空为未分类。
    - `section_id` 和当前值相同：即使当前分区已归档也放行（只改标题等）。
    - 其他：校验存在且未归档。
    - **PATCH 不改 `users.last_section_id`**：下次新建仍用上次新建记住的分区，不跟着这次修改走。
  - `KB_ACTIONS` 的 `distill` 和 `organize` 提示词：在 `start_turn` 拼 prompt 时追加当前分区说明，但要求 Claude **不要自己传 section**；分区由 `internal_write` 按会话补。
- [ ] `kb.py:internal_write`：
  - **忽略 `body.section`**（Claude 传了也不采用），以服务端会话分区为准。
  - `fill = sections.active_name_for_session(sess)`：会话没有分区或分区已归档时为空。
  - 调用 `write_note(..., section=None, fill_empty_section=fill)`：新建、或更新/追加但原分区为空时补上；原分区已有名字时保持，不会把已有笔记搬进当前会话分区。
- [ ] 人编辑笔记（`PUT /api/kb/note`）：
  - `NoteBody` 增加 `section: str | None = None`；`kb_save` 把它传给 `write_note(..., section=body.section)`。
  - `None` = 保持原分区；`""` = 清空为未分类；非空 = 校验未归档后写入。
  - `kb_status` 继续传 `section=None`，只改状态，不动分区。
  - 不要和 Claude 共用“空字符串 = 保持”的语义：Claude 走 `fill_empty_section`，人走 `section`。
- [ ] `approval_mcp.py`：
  - `kb_search(query, limit=8, type="", section="")`；工具描述写“不确定时留空，留空搜全库”。
  - `kb_write(..., section="")`：参数保留兼容，但服务端以会话分区为准；工具描述写“section 留空，不要自己填分区”。
  - `read_document` 按 `status` 显示空文本/扫描件提示。
- [ ] `KBReq` 增加 `section: str = ""`；`/api/kb?section=`、`/api/kb/search?section=`、`/api/kb/graph?section=` 透传。
  - `KBReq.section` 只给 `internal_search` 用；`internal_write` 不采用它，分区以会话为准。
  - `internal_search` 必须把 `body.section` 传给 `search(...)`；只传 `query`/`limit` 会让工具加的 section 在这里丢掉。

### 6.4 种子数据

- [ ] 首次启动不自动建分区；`NULL` 就是“未分类”。
- [ ] 管理后台提供“一键建常用分区”：合同、采购、报表、人事、综合事务、其他（可编辑，不强推）。

### 6.5 测试

- [ ] `test_admin_can_create_user_cannot`。
- [ ] `test_section_name_strip_and_reserved`：创建/改名会 strip，空串和 `__uncategorized__` 返回 400。
- [ ] `test_duplicate_archived_name_rejected`：归档“合同”后新建“合同”返回 409，恢复后可用。
- [ ] `test_session_assign_and_filter`：`?section_id=` 声明生效；`0` 返回未分类，不是全部。
- [ ] `test_session_filter_zero_not_dropped`：`section_id=0` 不被 `if section_id` 当成没传。
- [ ] `test_section_name_canonical`：库里是 `HR`，传入 `hr`，笔记 frontmatter 写 `HR`。
- [ ] `test_kb_home_returns_all_notes`：选分区时 `notes` 仍是全库，`stats`/`recent` 是过滤后的；聊天 [[链接]] 不变成“还没有这条笔记”。
- [ ] `test_new_session_passes_section`：`POST /api/sessions` 带 `section_id` 能落到会话上；端点没有吞掉参数。
- [ ] `test_session_clear_with_zero`：新建时 `section_id: 0` 写 NULL 且 `last_section_id` 也清空，不会被上次分区顶替；PATCH `0` 清空，`None`/不传不改。
- [ ] `test_patch_does_not_change_last_section`：PATCH 会话到未分类或其他分区，`users.last_section_id` 保持不变。
- [ ] `test_session_archived_not_selectable`：新建/改到归档分区 400；当前值就是归档分区时改标题放行。
- [ ] `test_last_section_fallback`：`users.last_section_id` 指向归档分区时，新建会话回退未分类。
- [ ] `test_note_frontmatter_section`。
- [ ] `test_note_create_without_section_is_uncategorized`：人新建不传 section -> 未分类。
- [ ] `test_note_update_without_section_keeps_section`：人更新不传 section -> 保持。
- [ ] `test_note_body_empty_section_clears`：人传 `section=""` -> 清空分区。
- [ ] `test_note_body_section_validated`：人传不存在的分区 -> 400。
- [ ] `test_note_content_section_ignored`：正文 frontmatter 写 `section: 合同` 不能绕过校验/落到 meta。
- [ ] `test_internal_write_fills_empty_note`：会话有“合同”，Claude 更新一条原本无分区的笔记 -> 补上“合同”。
- [ ] `test_internal_write_keeps_existing_section`：笔记已有“采购”，会话是“合同”，Claude 更新后仍为“采购”。
- [ ] `test_search_section_excludes_uncategorized`：选“合同”不返回未分类，也不返回其他分区。
- [ ] `test_search_uncategorized_only`：`section="__uncategorized__"` 只返回未分类。
- [ ] `test_kb_home_recent_filtered_by_section`：`_recent` 只返回当前分区笔记的历史。
- [ ] `test_kb_home_today_new`：`today_new` 只返回今天新建的经验/卡片，不用 `stats.week` 冒充。
- [ ] `test_kb_home_and_graph_filter_same_rule`：图谱 ghost 只用过滤后的笔记重算。
- [ ] `test_internal_search_passes_section`：`/api/internal/kb/search` 的 section 不丢。
- [ ] `test_rename_updates_notes`。
- [ ] `test_rename_failure_rolls_back_files_and_name`：mock 第 2 篇的写入只失败一次，断言第 1 篇已恢复、`sections.name` 也恢复、`kb_history` 没有留下新分区名。
- [ ] `test_rename_recovery_after_crash`：模拟 `sections.name` 已改、文件只改了一半、进程重启；启动恢复把旧名笔记补成新名并清标记。
- [ ] `test_rename_success_history_written_after_files`：全部文件成功后才写历史；从历史恢复不会把笔记串回错误分区。
- [ ] `test_any_user_can_edit_note_section`：非作者也能改团队笔记分区（沿用现状）。
- [ ] `test_session_section_owner_only`：非主人不能改会话分区。
- [ ] `test_session_status_derivation`：pending > running > stopped > done，`meta.stopped`/`timed_out`/`error` 能落成 `stopped`。
- [ ] `test_session_duration_file_count`：`duration_seconds` 取最后一轮 `meta.seconds`，`file_count` 取 `links.manifest`；没有时返回 0/—，不造假。

### 6.6 验收

- 管理员建“合同”，用户新建会话选“合同”，会话列表按“合同”筛出。
- 会话里“沉淀经验”：**新建**的笔记 frontmatter 出现 `section: 合同`；更新一条原本没有分区的老笔记，也会补上 `section: 合同`；更新一条已有“采购”的笔记，保持“采购”，不会被搬到“合同”。
- “整理进知识库”在 P3 的验收是：**会话上已经有分区时**，新会话创建后新建的卡片、以及原本没分区的笔记会补上这个分区；P3 不要求文件库按钮自己猜当前分区。
- 知识库按“合同”筛选，只显示合同笔记；“未分类”单独一项，其他分区不串；“最近修改”也不串。
- 不选择分区时搜全库；Claude 的 `kb_search` 默认也是全库，传了 section 时确实收窄。
- 管理员把“合同”改成“合同评审”，已有笔记的 `section` 同步更新；中途失败时文件、分区名和历史都回到原名。
- 归档分区后，新建会话选不到，但老会话仍显示原分区；恢复归档后可以继续使用。
- 分区已下架时，`distill` 不再把这个名字补进空笔记；老会话仍显示旧名。
- 归档“合同”后直接新建“合同”返回 409，提示先恢复。

---

## 7. P4：分区工作台 UI

> **分两套落点：** 现有网页先做（7.1），年鉴壳层定稿后再按 7.3 重排。不要再把“P4 前不改 `app.js`”当成总开关——会话归区和现有网页的入口必须现在做。

### 7.1 现有网页（本期先做）

**侧栏分区组**
- [ ] `static/index.html`：在 `#kbNav` 和 `#sessions` 之间加分区组容器（例如 `#sectionNav`）：“全部 / 未分类 / 各分区”。不要把分区组写进 `#sessions`，否则 `renderSessions()` 下次 `innerHTML` 会把它清掉。
- [ ] `static/app.js`：
  - 增加 `currentSection`：`null`=全部，`0`=未分类，其他为 `section_id`。
  - `renderSections()` 填充分区组；普通用户每项只显示自己的 `session_count`，管理员另外显示管理入口。
  - `renderSessions()` 先按 `currentSection` 过滤 `sessions`，再按 `#sessQ` 过滤；空分区时把“这个分区还没有会话，新建一个。”写进 `#sessions` 的空状态，不要用会自动消失的 toast。
  - `loadSessions()` 请求 `/api/sessions?section_id=...`：`null` 不传，`0` 原样传，不能因为假值被丢掉。

**会话创建（关键）**
- [ ] `ensureSession(title)` 请求体加 `section_id: currentSection`：`0`=未分类，`null`=全部（让后端用 `last_section_id`），其他为分区 id。`send()`、上传文件、添加本机文件夹都走 `ensureSession`，不能只改“新会话”按钮。
- [ ] 新建会话默认分区用 `users.last_section_id`；它已归档或不存在时回退未分类。切换分区后记住。
- [ ] 会话头显示分区标签，点击可改（仅会话主人）；PATCH 传 `0` 清空，不传不改。
- [ ] 已下架分区在老会话上只显示原名，不可重新选中。

**知识库（`static/kb.js`）**
- [ ] `KB.section = ""` 表示全部，`"__uncategorized__"` 表示未分类；切页签不要清掉它。
- [ ] `kbLoad()` 请求 `/api/kb?section=...`；服务端 `notes` 仍返回全库供 `[[链接]]`/`kbFind`/`kbSuggest` 解析，列表用 `brief.section` 在前端过滤；`stats`/`recent`/`hubs`/`ghosts` 用服务端过滤后的结果；`kbHome()` 里的 `stale`、侧栏、feed 也基于过滤后的笔记。
- [ ] `#kbQ` 搜索只有 `q` 非空才请求 `/api/kb/search?q=...&section=...`；空搜索框回到 `KB.data.notes` 过滤列表，不能把 `search()`（无关键词返回空）当列表源。
- [ ] `kbGraphView()` 和 `kbSky()` 请求 `/api/kb/graph?section=...`；分区 chips、列表、首页、图谱、星空共用同一个 `KB.section`。
- [ ] 分区 chips 的“未分类”查询值是 `__uncategorized__`；编辑器里的“未分类”保存传 `section: ""`。这两个空字符串不能混用。
- [ ] `kbEdit()` 加分区下拉：
  - 新建默认未分类；编辑默认当前笔记分区。
  - 笔记属于已下架分区时，下拉显示原名；已下架选项不能被其他笔记选中；不改时保存仍传原名，**不能传空串**（会把分区清空）；要移出只能改选其他未下架分区。
  - 用户主动选“未分类”才传 `""`。
- [ ] `kbOrganize()` 对话框加分区选择：文件库打开时可能没有当前会话，不能去读一条还不存在的筛选条；未分类传 `section_id: 0`，`POST /api/sessions` 带上分区。
- [ ] `kbDistill()` 继续只发 `{ action: "distill" }`，分区由会话在服务端补。
- [ ] `static/index.html`：知识库页签/容器加分区 chips 和编辑器分区下拉的挂载点；`#kbQ` 保持现有搜索框。

**管理后台**
- [ ] `static/index.html`：`#adminTabs` 增加“用量”“分区”两个按钮。
- [ ] `static/admin.js`：`show(t)` 在最后的 `else renderReview()` **之前**加 `else if (t === "usage") ...` / `else if (t === "sections") ...`；写在 `else` 后面会落到“待审核”。
- [ ] 分区页签：列表、新建、改名、排序、下架/恢复、一键建常用分区；改名确认里的 N 用 `note_count`。
- [ ] 恢复列表请求 `GET /api/sections?include_archived=1`，项上要有 `archived`；侧栏普通用户只显示 `session_count`，不要把全团队 `note_count` 当成自己的会话数。
- [ ] 一键建常用分区按名字逐个创建：未下架的同名跳过；已下架的同名不自动恢复，提示先恢复；其余继续。结束说明建成几个、跳过几个、几个要先恢复。

### 7.2 现有网页手工验收

- [ ] 1440×900 和 1280×900 下，侧栏分区组不挤压会话列表。
- [ ] 从输入框发消息、上传文件、添加本机文件夹创建的会话都带当前分区；未分类传 `0`。
- [ ] 切换分区只过滤会话行；空分区提示在 `#sessions` 列表里，不是 toast。
- [ ] 知识库列表、首页、图谱、星空共用 `KB.section`；切页签不丢；空搜索框仍有列表。
- [ ] 笔记属于已下架分区时显示原名；不改保存不丢分区；主动选未分类才清空。
- [ ] `kbOrganize` 新建会话带分区；`kbDistill` 不传 section。
- [ ] 管理页签顺序正确；改名确认 N=`note_count`；恢复列表能看到已下架项。
- [ ] 分区改名后，旧会话和旧笔记都显示新名字；选“合同”时看不到未分类和其他分区。

### 7.3 年鉴壳层（壳层定稿后）

**总原则**

- 会话页是正式工作界面；首页是值班板，不是海报。
- 保留纸纹、衬线标题、等宽数字、双线页眉、许可单；红色只表示紧急/待确认，不再用于装饰。
- 首页三列是真实内容，不是展示卡片：待处理、今日会话、今日知识库。目录负责导航，中间列负责“要处理的事”，不要复制一份完整目录。

**页眉**

- 删除描边大日期、固定口号、英文引文；日期只留在页眉 `.meta` 表里。
- 主标题改成动态值班句，例如「3 项待确认」「今天没有待确认」「3 个会话进行中」。
- 标题字号控制在 28–40px，不要再做 74px 的 display 巨字；数字用等宽。
- 页眉表保留日期、值守、服务器、工作目录、权限、状态；P5 的用量数字也追加在这里。

**三列内容**

- **待处理**：待确认放第一行，进行中其次；点一行进入会话。没有待确认时写「没有待确认的操作」。数据用 `session_view.pending` / `status`。
- **今日会话**：保留时刻、会话名、状态、时长、文件份数。只列今天有更新的会话；已完成的压到底部或折叠成计数，不要复刻目录的全部行。
  - 去掉 16 根小时柱和「日均 6.4」；如果一定要图，只留一条细 sparkline，本期可以直接不做。
  - 时长取最后一轮 `meta.seconds`，没有就 `updated - created`，再没有显示「—」；不要编造。
  - 文件份数取 `links.manifest` 数量，没有就 0。
- **今日知识库**：列出今天新建的经验和文件卡片，点进去打开知识库；没有就写「今天还没有新增知识库内容」。不要再用英文引文占这个位置。
  - 数据用 `kb_home.today_new`：`kb_history.note='新建'` 且本地今天范围内，关联当前索引取标题/类型/路径；不要拿 `stats.week` 冒充今日。
- 三列在内容变多时自然向下长；页眉和目录固定，主区滚动。**不要做三列各自独立滚动**，避免三个滚动条；小屏改单列堆叠。

**删除的装饰**

- 罗盘、「PEAK HOUR」、四方刻度、「已同步」章、页脚海军蓝灯带和胶片条全部删除；它们不表示可操作状态。
- 首页不做假输入栏；保留目录头部的「＋ 新会话」真实按钮。快捷键已经在会话输入条上，首页不再占一行。
- 登录页左右分栏保留，它是唯一一次品牌展示；字体同样改成本地字体。

**目录和导航**

- 左侧 88px 竖栏只留竖排「综合部工作台」，删除印章和三颗彩点；`padding:26px 0 152px` 改成上下对称的少量内边距，把高度还给目录。
- 目录底部主导航三项：**值班板 / 文件库 / 知识库**。文件库和知识库并列，管理仍从人名菜单进入，不做第四个主导航。
- 1024px 不再 `display:none`：竖栏收成 64px，目录改成从左侧滑出的 224px 抽屉，页眉放一个打开按钮；支持 Esc、点遮罩、选中会话后关闭，打开时焦点进抽屉。
- 1280px 保持三栏，目录 224px；1024–1199px 改两栏（待处理 + 今日知识库，今日会话整行放下面）；<1024px 单列堆叠。

**会话页**

- `KEEPER'S NOTE · 交办` 改成说话人名字，或只写「交办」；正式界面里的步骤表、许可单、页眉都用中文：「步骤记录」「待确认」「工作目录」。
- 会话状态只用：**待确认 / 进行中 / 已完成 / 已停止**。不要用「已归档」表示结束；「已下架」只用于分区。
- 状态由后端派生：`pending` > `running` > `stopped`（`meta.stopped` / `meta.timed_out` / `meta.error`）> `done`，不要靠文案猜。
- 页眉右侧保留工作目录、会话状态、消息数、权限；消息数由 `session_view.message_count` 提供。
- 许可单保持现在的视觉；拒绝和批准分两端。红色只给待确认/拒绝/危险操作，普通选中态用 navy 或 gold。

**分区**

- 分区控件放在 `.lb-search` 和 `.lb-scroll` 之间，是一行「分区 · 全部」，点开浮层；`.lb-scroll` 仍按「今日」「昨天」分组，**只过滤会话行**。
- 首页只过滤「最近记录」表里的会话行和目录里的会话；页眉日期/权限/服务器不跟分区变。
- 分区标签只用分区名，加「全部」「未分类」；管理员下架写「已下架」。原型里的「已归档」是会话状态，不要拿来当分区状态。
- 会话页的分区放在头部元数据里，可点改，仅主人。

**字体和视觉**

- 删除 Google Fonts 的 `link` / `preconnect`，不依赖外网。
- 标题/衬线：`"Source Han Serif SC", "Noto Serif CJK SC", "Songti SC", "STSong", "SimSun", serif`。
- 正文：`"Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB", sans-serif`。
- 数字：`Consolas, "Courier New", monospace`。
- 纸纹继续用内联 SVG；不要引入外部图片字体。
- 颜色语义：`--red` 只用于待确认、拒绝、危险；选中/激活/普通链接用 navy/gold；`::selection` 和用户消息左边线改成 navy，不再用红色。

**验收**

- 1280×900：三栏、目录 224px、页眉固定；1440×900 不出现巨大空白或海报式拉伸。
- 1024px：竖栏 64px、目录抽屉可打开/关闭，不出现没有入口的会话列表。
- 首页三列内容变多时只出现主区一个滚动条；页眉和目录不跟着滚走。
- 待处理为空、今日无会话、今日无知识库内容时都有中文空状态。
- 会话页状态只出现待确认/进行中/已完成/已停止；不再出现“已归档”当会话状态。
- 断网/外网不可达时字体不等待、不闪烁；标题和正文都落在本机字体上。
- 知识库和管理页原型里还没有对应页面；这两块继续用现有 `kb.js` / `admin.js`，等年鉴补出页面再迁。

---

## 8. P5：用量页面 UI

> **分两套落点：** 现有网页先做（8.1），年鉴壳层定稿后再按 8.2 放数字。个人用量不要放欢迎页——有消息后 `renderMessages()` 会拆掉欢迎页，卡片会消失。

### 8.1 现有网页（本期先做）

- [ ] 入口放在 `renderUserMenu()` 的用户菜单里，用对话框打开详情；不要放欢迎页。
- [ ] 详情支持“本月 / 上月 / 全部”：
  - 本月：本地当月 1 日到今天（或到月末）。
  - 上月：显式传 `from/to`；1 月的上月是去年 12 月。
  - 全部：只传 `all=true`。
  - 日期用本地日历拼，不要用 `toISOString()`。
- [ ] 详情内容：input / output / cache read / 估算费用 / 轮次；按天、按模型、最近 20 轮（`GET /api/usage/me/turns`）。
- [ ] 管理后台“用量”页签：用户汇总表（本月/上月/全部）、按天趋势、按模型分布、导出 CSV。
- [ ] CSV 由前端用 `/api/admin/usage` 的 `by_user` 生成，编码 `utf-8-sig`，列与返回字段一致。
- [ ] 金额显示：`cost == null` 写“未采集费用”，`cost === 0` 写“0（估算）”；禁止 `cost || 0`。
- [ ] 无数据：`has_data=false` 显示“还没有采集到用量”；区间内没有行但 `has_data=true` 显示 0，入口保留。
- [ ] 文案明确：“token 是模型调用量，不等于套餐额度；费用为估算值；cache read 单价更低。”

### 8.2 年鉴壳层（壳层定稿后）

- [ ] 用量数字放页眉 `.meta` 表的追加行，标签用「输入」「输出」「缓存」「轮次」「费用」。
- [ ] 详情在三栏和「以下空白」之间展开一张表；CSV 只在管理页。
- [ ] 无用量的格子写「尚未入账」，费用为 0 写「0 · 估算」；空分区写「此分区还没有会话。」
- [ ] 窄屏沿用原型 1024 的规则；用量数字不参与分区过滤。

---

## 9. 跨模块约定

### 9.1 错误处理

- 用量：`normalize_usage()` 返回空才跳过；唯一冲突跳过该行，NOT NULL/磁盘错误整批 rollback；写库失败只写 `data/logs/usage.log`，不阻断对话，不跳过 `RUNS` 清理。
- 缓存：读缓存失败回退到直接提取；写缓存失败（包括 Windows `os.replace` 被占用、索引写失败）只记日志并返回本次正文，不变成 500；空文本/扫描件也缓存状态。
- 分区：未知/已归档分区返回 400；知识库改名失败时回滚已改文件，再恢复 `sections.name`；进程崩溃用 `section_rename_in_progress` 恢复。
- 缓存安全：`DOC_CACHE_DIR` 必须在 `_deny_rules()` 里。

### 9.2 权限

- `/api/usage/me`、`/api/usage/me/turns`：登录用户，只看自己；个人 turns 拒绝 `user_id`。
- `/api/admin/usage`、`/api/admin/usage/turns`、`/api/sections` 的写操作、`/api/admin/doc-cache`：管理员。
- 分区读操作：登录用户。
- 会话归区：只限主人（`_owner_only`）。
- 笔记编辑/归区：沿用现状，任何登录用户都可以改团队笔记。
- `read_document` 仍走现有 `files_api.resolve_path`，别人的工作区读不了。

### 9.3 迁移

- 所有新表用 `CREATE TABLE IF NOT EXISTS`；新列用现有 `ALTER TABLE ... ADD COLUMN` 循环，失败忽略。
- 不改旧表字段类型，不删旧数据。
- 首次启动后老会话 `section_id=NULL`，老笔记 `section=""`，都归“未分类”。
- 启动时调用 `kb.recover_section_rename()`：处理“分区名已改、文件没写完”的崩溃恢复，详见 6.3。
- `store.py` 必须在**建目录和 `sqlite3.connect` 之前**读取 `PROJECTOR_DATA`，把 `DATA` 指向临时目录；`LIBRARY`、`SUBMISSIONS`、`VERSIONS`、`LOGS`、内部令牌、`doc_cache` 都从这份 `DATA` 派生。否则 conftest 设了环境变量，连接仍是 `Projector\data\center.db`，`adopt_orphans()` 还会改生产库。
- `KB_ROOT` / `WORKSPACE_ROOT` 现有代码已经认环境变量；但只断言这两个会假通过，因为数据库可能还是生产库。
- `tests/conftest.py` 的隔离必须写成**模块顶层赋值**，不能写成 fixture：fixture 在 `import server` 之后才跑，来不及。顶层同时 `mkdir` 临时 data 目录。
- `test_isolation.py` 断言 `store.DATA`、`store.FILE_ROOT`、`store.KB_ROOT`、`store.WS_ROOT` 以及 `PRAGMA database_list` 返回的库文件都落在临时根下。生产目录可能本来就在，不能用“目录不存在”当成功条件。

### 9.4 回滚

- P1/P2/P3/P4/P5 各自独立 commit；回滚一个阶段不影响其他阶段。
- 分区 UI 在 `/api/sections` 失败时降级为“不显示分区组”，会话功能不受影响。
- 用量 UI 入口在用户菜单；`has_data=false` 显示“还没有采集到用量”，区间内无行但 `has_data=true` 显示 0，不隐藏入口。

---

## 10. 测试与验证命令

- [ ] 语法检查：

```bash
python -m py_compile store.py server.py kb.py approval_mcp.py usage.py sections.py
```

- [ ] 单元测试（`tests/conftest.py` 会在 import 前自动设置临时目录；手工跑时也可以显式指定）：

PowerShell：

```powershell
$env:PROJECTOR_DATA = "$env:TEMP\projector-test-data"
$env:KB_ROOT = "$env:TEMP\projector-test-kb"
$env:WORKSPACE_ROOT = "$env:TEMP\projector-test-ws"
$env:FILES_ROOT = "$env:TEMP\projector-test-files"
python -m pytest tests -q
```

`tests/conftest.py` 应在 **模块顶层**（不是 fixture）设置，且要在 import `server` / `store` 之前：

```python
import os, tempfile
from pathlib import Path

_root = Path(tempfile.mkdtemp(prefix="projector-test-"))
(_root / "data").mkdir(parents=True, exist_ok=True)
(_root / "files").mkdir(parents=True, exist_ok=True)
os.environ["PROJECTOR_DATA"] = str(_root / "data")
os.environ["FILES_ROOT"] = str(_root / "files")
os.environ["WORKSPACE_ROOT"] = str(_root / "workspaces")
os.environ["KB_ROOT"] = str(_root / "kb")
```

- [ ] 手工接口检查（现有默认端口 8080；PowerShell 用 `curl.exe`）：

```powershell
curl.exe -H "Authorization: Bearer <token>" "http://localhost:8080/api/usage/me?from=2026-10-01&to=2026-10-31"
curl.exe -H "Authorization: Bearer <token>" "http://localhost:8080/api/usage/me/turns?limit=20"
curl.exe -H "Authorization: Bearer <token>" "http://localhost:8080/api/sections"
```

- [ ] 前端手工检查：Chrome 打开 `http://localhost:8080`，按 7.2 / 8.1 的清单逐项过；年鉴壳层落地后再按 7.3 / 8.2 过一遍。

---

## 11. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 测试连到真实库 / 真实 D 盘 | 污染生产数据 | `PROJECTOR_DATA` 在建目录/connect 前生效；`KB_ROOT`/`WORKSPACE_ROOT` 临时目录；断言 `store.DATA` 和 `PRAGMA database_list` |
| 用量行没写 `created` | 本月/区间查询全是 NULL，页面永远空 | `record_turn` 每行写 `time.time()`；`all=True` 忽略 from/to，不做默认本月和 366 天校验 |
| 用量写入抛异常 | 会话卡在“进行中” | `record_turn` 自兜 + `_drive` 用 `try/finally` 包住写消息和清理 |
| `add_message` 插入/查询不同锁 | `message_id` 可能挂到 event 上 | 同一次 `_db_lock` 里 INSERT + UPDATE + `lastrowid` 取回；锁内不调 `db()`/`push_session()` |
| `push_session` 在锁内 | `_db_lock` 不可重入，死锁 | 插入/更新/取 id 后先提交，释放锁再 `broadcast()` / `push_session()` |
| `result` 只有 `modelUsage` | 轮次被漏记 | 以 `normalize_usage()` 为准，返回空才跳过；非 dict 项跳过，空模型名写 `unknown` |
| `total_cost_usd` 是累计值 / 用 `or` 取 0 | 费用重复计算或 0 丢失 | 优先用各模型 `costUSD`；兜底先判断 `is None` 再取 `totalCostUSD`，0 照写 |
| `/api/usage/me/turns` 接受 user_id | 普通用户能看到别人的轮次 | 个人接口拒绝 `user_id`；管理员另开 `/api/admin/usage/turns` |
| 日期默认/has_data 写死 | 区间查询误报“没有数据” | 两侧都空才默认本地本月；单侧/非法 400；`has_data` 与区间无关 |
| `INSERT OR IGNORE` 吞掉 NOT NULL | 真正的写入失败被隐藏 | 用 `ON CONFLICT(message_id, model) DO NOTHING`；其他错误 rollback |
| 聊天页 `meta.cost` 和用量页对账 | 两个口径互相污染 | 只比较 `/api/usage/me` 和 `/api/admin/usage`；不改 `app.js` 的 `meta.cost` |
| `section=""` 被当成非法 | 沉淀经验全部 400 | 人走 NoteBody（None 保持 / "" 清空 / 名字校验）；Claude 走会话分区补空 |
| 只改 `KBReq` 没改 `NoteBody` | 编辑器选分区不落盘 | `NoteBody.section: str | None`；`kb_save` 传 `section`；`kb_status` 保持 None |
| 正文 frontmatter 带 section | 绕过校验写进 meta | `extra_meta.pop("section", None)` |
| 分区筛选并入未分类 | 筛选结果接近全库 | 只选该分区；“未分类”单独一项；内部搜索默认全库 |
| `_recent` / internal search / graph ghost 漏过滤 | 选“合同”仍看到其他分区 | `_recent` 按过滤后路径收窄；`internal_search` 传 `body.section`；ghost 用过滤后笔记重算 |
| `kb_home` 把 notes 滤成一个分区 | 聊天 [[链接]] 显示“还没有这条笔记” | `notes` 返回全库，统计/最近/ghost 按分区；前端用 `brief.section` 过滤列表 |
| `list_sessions` 没声明 section_id / 0 被丢 | `?section_id=0` 返回全部 | 函数签名显式声明；用 `is None` / `== 0` 判断 |
| 改名中途失败 | 一半笔记新旧名混用 | `_write_lock` 内预检+UPDATE；不走 `_save`，成功后才写历史；失败按原文写回、删本次历史、恢复分区名 |
| 改名大小写不一致 | 分区显示新名、笔记仍旧写法 | 校验通过后用数据库 `sections.name` 原文写入 |
| 缓存目录可被 Read | 绕过工作区权限读正文 | 加入 `_deny_rules()`；文件名用路径哈希 |
| 没装 pypdf 时 return 说明 | 说明被缓存成正文，装 pypdf 后仍命中 | `_pdf` 在 ImportError 时抛异常，不写 L1/L2 |
| 缓存索引写失败 | 旧索引 + 新 MD，永久命中旧状态 | 索引失败删除刚写的 MD；命中校验文件大小 == `bytes`；`hits` 单独计数 |
| 扫描件提示拼进正文 | 占 30000 字窗口，翻页重复 | `internal_doc.text` 只含正文；提示只在 `read_document` 第一段出现 |
| 归档后同名新建 | 唯一约束冲突 / 笔记串区 | 返回 409 提示先恢复；不做部分唯一索引 |
| 清理没有定时器 | 缓存无限增长 | `settings` 记上次清理时间；启动后台 + 写缓存前检查；清理只用 `store.db()` |
| `kb.py` 与 `sections.py` 循环导入 | 服务启动失败 | `sections.py` 顶层不 import `kb`，改名/统计时延迟 import |
| P2b 语义不清 | outline/query 与 offset 冲突 | 移出本期，后续单独设计 |
| UI 改版未定 | 分区 UI 返工 | 现有网页先做 P4/P5；年鉴壳层的 P4b/P5b 等定稿后再做，两套落点分开 |
| 首页继续是海报 / 重复会话列表 | 值班时看不到要处理的事 | 三列值班板；目录导航、中间列只放待处理/今日；删除装饰 |
| 会话状态/时长/消息数/文件数没有数据 | UI 只能编数字 | `session_view` 派生 `status` / `duration_seconds` / `message_count` / `file_count`；`meta.stopped` 落库 |
| 1024px 隐藏目录没有入口 | 窄屏无法切会话 | 竖栏 64px + 目录抽屉 + 页眉按钮；不要 `display:none` |
| 外网字体慢/失败 | 局域网首屏闪烁或字体错乱 | 删除 Google Fonts；标题/正文/数字用本机字体栈 |
| 红色到处都是 | 待确认失去警示性 | `--red` 只给待确认/拒绝/危险；选中/激活用 navy/gold |
| 三列各自滚动 | 三个滚动条，值班时难用 | 主区一个滚动条，页眉/目录固定；窄屏堆叠 |

---

## 12. 完成标准

- [ ] 一轮真实对话后，个人用量和后台用量都能看到，且数字可对账；“轮次”按消息去重。
- [ ] 只有 `modelUsage` 的轮次也能落库；写用量失败不影响对话结束。
- [ ] 同一个文件第二次读取不再走解析，文件变更后能自动更新；空文本和扫描件有明确状态。
- [ ] 缓存目录不能被 Claude 直接读。
- [ ] 管理员能建分区，用户能给会话/笔记归区，侧栏会话列表和知识库能按分区筛选。
- [ ] 现有网页的侧栏分区组、`ensureSession`、知识库、管理页都落地；年鉴壳层落点另列，不混改。
- [ ] 选分区时不再并入未分类；“未分类”有独立入口；“最近修改”、内部搜索和 `[[链接]]` 解析都正常。
- [ ] 分区改名后，老会话、老笔记同步显示新名字；失败时文件、分区名和历史都不会留下新名。
- [ ] 归档分区不可重新选中；同名重建返回 409 并提示恢复。
- [ ] 用量行 `created` 不为空；`all=true` 不受 from/to 默认本月和 366 天限制；`has_data` 与区间无关。
- [ ] 写用量、写消息、UPDATE sessions 任一失败，会话都不会卡在“进行中”，`message_id` 也不会挂到 event 上。
- [ ] 人在编辑器里选分区/清空能落盘；Claude 只补空分区，不搬已有分区；写入使用 `sections.name` 原文。
- [ ] 用量数字只以 `/api/usage/me` 和 `/api/admin/usage` 对账；聊天页 `meta.cost` 不改。
- [ ] 年鉴首页是三列值班板，不是海报：无大日期/罗盘/英文引文/假输入栏；三列只保留待处理、今日会话、今日知识库。
- [ ] 会话状态只用待确认/进行中/已完成/已停止，不再用“已归档”表示结束。
- [ ] 1024px 有目录抽屉入口；1280px 三栏、目录 224px；首页只出现一个主滚动条。
- [ ] 不依赖 Google Fonts，断网时标题/正文/数字都落在本机字体上。
- [ ] 会话页中文标签、许可单、步骤表保留；红色只用于紧急/待确认。
- [ ] 现有登录、会话、文件库、知识库、技能功能无回归。
- [ ] 每个阶段独立 commit，提交信息清晰，可单独回滚。
- [ ] 测试和验收命令使用 `8080`，不出现 `8765`。
