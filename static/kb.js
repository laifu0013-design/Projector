// 知识库：首页、笔记（阅读 / 编辑 / 历史 / 反向链接）、关系图谱；以及和聊天、文件库的衔接（沉淀经验、整理进知识库）。
// 用到 app.js 里的 api、post、esc、md、dialog、toast、showFiles、openSession 等。
"use strict";

const KB = { data: null, view: "home", type: "", tag: "", sel: null, note: null, editing: false, dirty: false,
             results: null, graph: null, sky: null, graphData: null, hidden: new Set(),
             section: "", sections: [], sectionsLoaded: false };
const KB_TYPES = { 经验: "exp", 文件卡片: "card", 主题: "topic" };
const kcls = t => KB_TYPES[t] || (t === "ghost" ? "ghost" : "other");
const ktype = t => `<span class="ktype k-${kcls(t)}">${esc(t)}</span>`;
const WL_RE = /(!?)\[\[([^\[\]\n|#^]+)(#[^\[\]\n|]*)?(?:\|([^\[\]\n]*))?\]\]/g;

// ---------- 数据 ----------

async function kbLoadSections() {
  try {
    const d = await api("/api/sections");
    KB.sections = Array.isArray(d) ? d : (d && d.sections) || [];
  } catch { KB.sections = []; }
  KB.sectionsLoaded = true;
}

function kbSectionMatch(n) {
  if (!KB.section) return true;
  if (KB.section === "__uncategorized__") return !n.section;
  return n.section === KB.section;
}

function kbSectionBar() {
  const box = $("kbSections");
  if (!box) return;
  const item = (val, label) => `<button type="button" data-ks="${esc(val)}" class="${KB.section === val ? "on" : ""}">${esc(label)}</button>`;
  box.innerHTML = item("", "全部") + item("__uncategorized__", "未分类") +
    KB.sections.filter(s => !s.archived).map(s => item(s.name, s.name)).join("");
}

function kbSectionOptions(selected) {
  const opts = [`<option value=""${!selected ? " selected" : ""}>未分类</option>`];
  const active = KB.sections.filter(s => !s.archived);
  if (selected && !active.some(s => s.name === selected)) {
    opts.unshift(`<option value="${esc(selected)}" selected>${esc(selected)}（已下架）</option>`);
  }
  for (const s of active) opts.push(`<option value="${esc(s.name)}"${s.name === selected ? " selected" : ""}>${esc(s.name)}</option>`);
  return opts.join("");
}

async function kbSetSection(section) {
  KB.section = section;
  KB.results = null;
  $("kbQ").value = "";
  await kbLoad();
  if (KB.view === "home") kbHome();
  else if (KB.view === "notes") { kbFilterBar(); kbRenderList(); }
  else if (KB.view === "graph") kbGraphView();
}

async function kbLoad() {
  if (!KB.sectionsLoaded) await kbLoadSections();
  const qs = KB.section ? "?section=" + encodeURIComponent(KB.section) : "";
  try { KB.data = await api("/api/kb" + qs); }
  catch { KB.data = await api("/api/kb"); }
  const count = KB.data.notes.filter(kbSectionMatch).length;
  $("kbCnt").textContent = count > 1 ? count : "";
  window.KBDATA = KB.data;
  kbSectionBar();
  window.onKbLoaded?.();
  return KB.data;
}
window.kbInit = () => kbLoad().catch(() => {});

const kbRoot = () => (KB.data ? KB.data.root : "").replace(/[\\/]+$/, "");
function kbRelOf(abs) {
  const root = kbRoot();
  if (!root || !abs) return null;
  const a = abs.replaceAll("\\", "/"), r = root.replaceAll("\\", "/");
  if (!a.toLowerCase().startsWith(r.toLowerCase() + "/") || !/\.md$/i.test(a)) return null;
  return a.slice(r.length + 1);
}
function kbFind(name) {
  if (!KB.data) return null;
  let t = name.trim().replaceAll("\\", "/").replace(/\.md$/i, "").toLowerCase();
  const notes = KB.data.notes;
  return notes.find(n => n.title.toLowerCase() === t) || notes.find(n => n.path.slice(0, -3).toLowerCase() === t)
    || notes.find(n => n.title.toLowerCase() === t.split("/").pop()) || null;
}

// 把 [[链接]] 变成可以点的标签；links：服务端给的 {目标: 路径或 null}
function kbLinkify(text, links) {
  return (text || "").split(/(```[\s\S]*?```|`[^`\n]*`)/).map((part, i) => i % 2 ? part : part.replace(WL_RE, (m, bang, t, hash, alias) => {
    const target = t.trim();
    // known：笔记路径；null 表示还没有这条笔记；undefined 表示不知道（知识库还没加载）
    const known = links ? (target in links ? links[target] : undefined) : (KB.data ? kbFind(target)?.path ?? null : undefined);
    const label = (alias || target + (hash || "")).trim();
    return `<a class="wl${known === null ? " ghost" : ""}" data-kb="${esc(target)}" title="${known === null ? "还没有这条笔记，点击新建" : "打开笔记"}">${esc(label)}</a>`;
  })).join("");
}
window.kbLinkify = kbLinkify;

// ---------- 打开和关闭 ----------

async function openKB(view = null) {
  $("kbBox").classList.add("show");
  $("side").classList.remove("open");
  if (!KB.data) $("kbHome").innerHTML = `<div class="kb-loading">${orb(40, "busy")}</div>`;
  try { await kbLoad(); } catch (e) { return toast(e.message, "err"); }
  kbTab(view || KB.view);
}
window.openKB = openKB;

function kbClose() {
  if (KB.editing && KB.dirty && !confirm("编辑的内容还没保存，确定关闭吗？")) return false;
  KB.editing = false; KB.dirty = false;
  $("kbBox").classList.remove("show");
  KB.graph?.stop(); KB.sky?.stop();
  return true;
}
window.kbBeforeClose = kbClose;
$("kbClose").onclick = kbClose;

function kbTab(view) {
  if (KB.editing && KB.dirty && view !== "notes" && !confirm("编辑的内容还没保存，确定离开吗？")) return;
  if (view !== "notes") KB.editing = false;
  KB.view = view;
  $("kbTabs").querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.kt === view));
  for (const [id, v] of [["kbHome", "home"], ["kbNotes", "notes"], ["kbGraph", "graph"]]) $(id).hidden = v !== view;
  if (view !== "graph") KB.graph?.stop();
  if (view !== "home") KB.sky?.stop();
  if (view === "home") kbHome();
  if (view === "notes") { kbFilterBar(); kbRenderList(); if (!KB.sel) kbEmptyView(); }
  if (view === "graph") kbGraphView();
}
$("kbTabs").onclick = e => { const b = e.target.closest("[data-kt]"); if (b) { KB.results = null; $("kbQ").value = ""; kbTab(b.dataset.kt); } };
$("kbSections").onclick = e => { const b = e.target.closest("[data-ks]"); if (b) kbSetSection(b.dataset.ks); };

// ---------- 首页 ----------

function kbHome() {
  const d = KB.data;
  const visible = d.notes.filter(kbSectionMatch);
  const bt = {};
  for (const n of visible) bt[n.type] = (bt[n.type] || 0) + 1;
  const week = visible.filter(n => n.mtime >= Date.now() / 1000 - 7 * 86400).length;
  const st = { total: visible.length, week };
  const stale = visible.filter(n => n.source_state);
  const tiles = [["经验", bt["经验"] || 0, "exp", "做过的事和做法"], ["文件卡片", bt["文件卡片"] || 0, "card", "重要文件的摘要"],
                 ["主题", bt["主题"] || 0, "topic", "项目、客户、产品"], ["本周更新", st.week, "week", "7 天内新增或修改"]];
  const visiblePaths = new Set(visible.map(n => n.path));
  const recent = (d.recent || []).filter(r => visiblePaths.has(r.path));
  const feed = recent.length ? recent.map((r, i) => `
      <div class="kh-ev ${r.exists ? "" : "gone"}" ${r.exists ? `data-kp="${esc(r.path)}"` : ""} style="animation-delay:${Math.min(i, 12) * 30}ms">
        <span class="kh-evd k-${kcls((d.notes.find(n => n.path === r.path) || {}).type)}"></span>
        <div><b>${esc(r.title)}</b><small>${esc(r.by_name)} · ${esc(r.note || "修改")} · ${rel(r.created)}</small></div>
      </div>`).join("") : `<div class="empty-note">还没有动静。第一条经验往往来自一次顺利完成的对话。</div>`;
  const side = [
    stale.length ? `<h3>${icon("alert", 14)}原文件改过的卡片</h3><div class="kh-list">${stale.slice(0, 6).map(n => `<a data-kp="${esc(n.path)}">${esc(n.title)}<span class="tag warn">${n.source_state === "missing" ? "原文件不在了" : "可能过时"}</span></a>`).join("")}</div>` : "",
    d.hubs.length ? `<h3>${icon("sparkle", 14)}常被提到</h3><div class="kh-chips">${d.hubs.map(h => `<a class="kchip k-${kcls(h.type)}" data-kp="${esc(h.path)}">${esc(h.title)}<i>${h.backlinks}</i></a>`).join("")}</div>` : "",
    d.ghosts.length ? `<h3>${icon("plus", 14)}被提到但还没写的</h3><div class="kh-chips">${d.ghosts.map(g => `<a class="kchip ghost" data-kb="${esc(g.name)}">${esc(g.name)}<i>${g.count}</i></a>`).join("")}</div>` : "",
    d.tags.length ? `<h3>${icon("search", 14)}标签</h3><div class="kh-tags">${d.tags.map(([t, c]) => `<a data-ktag="${esc(t)}" style="font-size:${Math.min(12 + c * 1.2, 19)}px">#${esc(t)}</a>`).join("")}</div>` : "",
  ].join("");
  $("kbHome").innerHTML = `
    <div class="kh-hero">
      <canvas id="khSky"></canvas>
      <div class="kh-copy">
        <div class="kh-kick">TEAM MEMORY</div>
        <h2 class="kh-h">大家做过的事，<em>都记在这里</em></h2>
        <p>Claude 回答问题前会先翻一翻这里。聊完点<b>“沉淀经验”</b>，或在文件库里点<b>“整理进知识库”</b>，它就会越来越懂你们的工作。</p>
      </div>
    </div>
    <div class="kh-stats">${tiles.map(([l, v, c, s], i) => `<div class="kh-stat k-${c}" style="animation-delay:${i * 60}ms" ${c !== "week" ? `data-ktype="${l}"` : ""}><div class="v" data-count="${v}">0</div><div class="l">${l}</div><small>${s}</small></div>`).join("")}</div>
    ${st.total < 6 ? `<div class="kh-how">
      <div><span>1</span><b>聊完一件事</b><small>在 Claude 的回答下面点“沉淀成经验”，它会把做法整理成一条笔记</small></div>
      <div><span>2</span><b>整理重要文件</b><small>文件库里选中文件或文件夹，点“整理进知识库”，Claude 读完后写成文件卡片</small></div>
      <div><span>3</span><b>以后直接问</b><small>“上次和客户 A 的合同在哪”——Claude 会先查知识库，再去打开原文件</small></div>
    </div>` : ""}
    <div class="kh-grid">
      <div class="kh-col"><h3>${icon("clock", 14)}最近更新</h3><div class="kh-feed">${feed}</div></div>
      <div class="kh-col">${side || `<div class="empty-note">笔记多起来以后，这里会显示常被提到的主题、标签和需要更新的卡片。</div>`}</div>
    </div>`;
  $("kbHome").querySelectorAll("[data-count]").forEach(el => countUp(el, +el.dataset.count));
  kbSky();
}

$("kbHome").addEventListener("click", e => {
  const t = e.target.closest("[data-ktype]");
  if (t) { KB.type = t.dataset.ktype; KB.tag = ""; KB.sel = null; return kbTab("notes"); }
  const g = e.target.closest("[data-ktag]");
  if (g) { KB.tag = g.dataset.ktag; KB.type = ""; KB.sel = null; return kbTab("notes"); }
});

async function kbSky() {
  KB.sky?.stop();
  const canvas = $("khSky");
  let data;
  try { data = KB.data.notes.filter(kbSectionMatch).length >= 6 ? await api("/api/kb/graph" + (KB.section ? "?section=" + encodeURIComponent(KB.section) : "")) : null; } catch { data = null; }
  if (!canvas.isConnected || KB.view !== "home") return;
  if (!data) {  // 笔记还少的时候，画一片装饰用的星空
    const types = ["经验", "文件卡片", "主题"], nodes = [], edges = [];
    for (let i = 0; i < 26; i++) nodes.push({ id: "d" + i, title: "", type: types[i % 3], deg: 1 + (i % 4) });
    for (let i = 1; i < 26; i++) edges.push(["d" + i, "d" + Math.floor(Math.random() * i)]);
    data = { nodes, edges };
  }
  KB.sky = new KGraph(canvas, data, { mini: true });
}

// ---------- 笔记列表 ----------

function kbFilterBar() {
  const d = KB.data;
  const visible = d.notes.filter(kbSectionMatch);
  const bt = {};
  for (const n of visible) bt[n.type] = (bt[n.type] || 0) + 1;
  const chip = (label, val, n) => `<button class="kf ${KB.type === val && !KB.results ? "on" : ""}" data-kf="${esc(val)}">${val ? `<i class="k-${kcls(val)}"></i>` : ""}${label}<small>${n}</small></button>`;
  $("kbFilter").innerHTML = (KB.results ? `<span class="kf on">${icon("search", 13)}“${esc($("kbQ").value.trim())}” 的结果<small>${KB.results.length}</small></span><button class="kf" data-kclear>${icon("x", 12)}清除</button>`
    : chip("全部", "", visible.length) + Object.keys(KB_TYPES).map(t => chip(t, t, bt[t] || 0)).join(""))
    + (KB.tag && !KB.results ? `<span class="kf tagf">#${esc(KB.tag)}<button data-kuntag>${icon("x", 12)}</button></span>` : "");
}
$("kbFilter").onclick = e => {
  const f = e.target.closest("[data-kf]");
  if (f) { KB.type = f.dataset.kf; kbFilterBar(); kbRenderList(); return; }
  if (e.target.closest("[data-kuntag]")) { KB.tag = ""; kbFilterBar(); kbRenderList(); return; }
  if (e.target.closest("[data-kclear]")) { KB.results = null; $("kbQ").value = ""; kbFilterBar(); kbRenderList(); }
};

function kbRenderList() {
  let items = KB.results;
  if (!items) {
    items = KB.data.notes.filter(n => kbSectionMatch(n) && (!KB.type || n.type === KB.type) && (!KB.tag || n.tags.includes(KB.tag)));
  }
  $("kbList").innerHTML = items.length ? items.map((n, i) => `
    <div class="kbi k-${kcls(n.type)} ${n.path === KB.sel ? "sel" : ""} ${n.status === "过时" ? "old" : ""}" data-kp="${esc(n.path)}" style="animation-delay:${Math.min(i, 20) * 14}ms">
      <span class="kdot k-${kcls(n.type)}"></span>
      <div class="kbi-m"><b>${esc(n.title)}</b><small>${esc(KB.results ? n.snippet : (n.summary || n.type))}</small></div>
      <span class="kbi-s">${n.section ? `<span class="tag">${esc(n.section)}</span>` : ""}${n.source_state ? `<span class="tag warn" title="原文件改过">!</span>` : ""}${n.status === "过时" ? '<span class="tag">过时</span>' : ""}<span>${rel(n.mtime)}</span></span>
    </div>`).join("")
    : `<div class="empty-note" style="padding:24px 12px;text-align:center">${KB.results ? "没有找到相关的笔记<br>换个说法试试，或者直接问 Claude" : "这里还没有笔记"}</div>`;
  $("kbList").querySelector(".sel")?.scrollIntoView({ block: "nearest" });
}

let kbSearchTimer;
$("kbQ").addEventListener("input", () => {
  clearTimeout(kbSearchTimer);
  const q = $("kbQ").value.trim();
  kbSearchTimer = setTimeout(async () => {
    if (!q) { KB.results = null; if (KB.view === "notes") { kbFilterBar(); kbRenderList(); } return; }
    try {
      const d = await api("/api/kb/search?q=" + encodeURIComponent(q) + (KB.section ? "&section=" + encodeURIComponent(KB.section) : ""));
      if ($("kbQ").value.trim() !== q) return;
      KB.results = d.results;
      if (KB.view !== "notes") kbTab("notes"); else { kbFilterBar(); kbRenderList(); }
    } catch (e) { toast(e.message, "err"); }
  }, 260);
});

// 列表、首页、反向链接里的笔记都用 data-kp 打开
$("kbBody").addEventListener("click", e => {
  const p = e.target.closest("[data-kp]");
  if (p && !e.target.closest("[data-kb]")) { if (KB.view !== "notes") kbTab("notes"); kbShow(p.dataset.kp); }
});

function kbEmptyView() {
  $("kbNotes").classList.remove("viewing");
  $("kbView").innerHTML = `<div class="kb-empty">${orb(54)}<p>选一条笔记来看<br><small>或者点右上角“新建笔记”</small></p></div>`;
}

// ---------- 阅读 ----------

async function kbShow(path) {
  if (KB.editing && KB.dirty && !confirm("编辑的内容还没保存，确定离开吗？")) return;
  KB.editing = false; KB.dirty = false;
  KB.sel = path;
  kbRenderList();
  $("kbNotes").classList.add("viewing");
  let d;
  try { d = await api("/api/kb/note?path=" + encodeURIComponent(path)); }
  catch (e) { $("kbView").innerHTML = `<div class="kb-empty"><p class="err">${esc(e.message)}</p></div>`; return kbLoad(); }
  if (KB.sel !== path) return;
  KB.note = d;
  const n = d.note;
  // 正文开头如果是和标题一样的一级标题，就不重复显示了
  const body = n.body.replace(new RegExp("^\\s*#\\s+" + n.title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\s*\\n"), "");
  const srcWarn = n.source_state === "changed" ? "原文件在这张卡片写完后改过，内容可能过时" : n.source_state === "missing" ? "原文件已经不在原来的位置了" : "";
  const who = [n.author && `${esc(n.author)} 创建`, n.contributors.length && `${n.contributors.map(esc).join("、")} 补充过`].filter(Boolean).join(" · ");
  $("kbView").innerHTML = `
    <div class="kv-bar">
      <button class="iconbtn" id="kvBack">${icon("chevL")}</button>
      <span class="sp"></span>
      ${isMine() ? `<button class="btn small ghost" id="kvRef" title="把这条笔记交给当前对话里的 Claude">${icon("clip", 14)}引用到对话</button>` : ""}
      <button class="btn small ghost" id="kvEdit">${icon("edit", 14)}编辑</button>
      <button class="iconbtn" id="kvHist" data-tip="历史版本（${d.history}）">${icon("history", 17)}</button>
      <button class="iconbtn" id="kvStatus" data-tip="${n.status === "过时" ? "标记为有效" : "标记为过时"}">${icon(n.status === "过时" ? "check" : "clock", 17)}</button>
      <button class="iconbtn" id="kvDel" data-tip="删除（进回收站）">${icon("trash", 17)}</button>
    </div>
    <article class="kv k-${kcls(n.type)} ${n.status === "过时" ? "old" : ""}">
      <div class="kv-kick">${ktype(n.type)}${n.status === "过时" ? '<span class="tag">已过时</span>' : ""}<span class="muted">更新于 ${esc(n.updated || fdate(n.mtime))}</span></div>
      <h1 class="kv-title">${esc(n.title)}</h1>
      ${n.summary ? `<p class="kv-sum">${esc(n.summary)}</p>` : ""}
      <div class="kv-props">
        ${who ? `<span>${icon("user", 13)}${who}</span>` : ""}
        ${d.session ? `<a data-ksess="${esc(d.session.id)}">${icon("chat", 13)}来自会话「${esc(d.session.title)}」</a>` : ""}
        ${n.tags.map(t => `<a class="ktag" data-ktag2="${esc(t)}">#${esc(t)}</a>`).join("")}
      </div>
      ${n.source ? `<div class="kv-src ${srcWarn ? "warn" : ""}">
        <span class="fk" style="background:${KIND_COLOR[kindOf(n.source)] || KIND_COLOR.other}">${icon(/\.[A-Za-z0-9]{1,6}$/.test(n.source) ? "file" : "folder", 15)}</span>
        <div><b>${esc(baseName(n.source))}</b><small>${esc(srcWarn || shortPath(n.source_abs || n.source))}</small></div>
        ${n.source_state !== "missing" ? `<button class="btn small" id="kvOpenSrc">${icon("eye", 14)}看原文件</button>` : ""}
        ${srcWarn && n.source_state === "changed" && isMine() ? `<button class="btn small primary" id="kvRefresh">${icon("sync", 14)}让 Claude 更新</button>` : ""}
      </div>` : ""}
      <div class="md kv-body">${body.trim() ? md(kbLinkify(body, d.links)) : '<p class="muted">（还没有正文）</p>'}</div>
      <section class="kv-back">
        <h3>提到这条的笔记 <small>${d.backlinks.length}</small></h3>
        ${d.backlinks.length ? d.backlinks.map(b => `<div class="kv-bl k-${kcls(b.type)}" data-kp="${esc(b.path)}"><b>${esc(b.title)}</b><small>${esc(b.snippet)}</small></div>`).join("")
          : '<p class="muted" style="font-size:13px;margin:0">还没有别的笔记链接到这里。在其他笔记里写 <code>[[' + esc(n.title) + ']]</code> 就能连上。</p>'}
      </section>
    </article>`;
  $("kbView").scrollTop = 0;
  linkifyPaths($("kbView").querySelector(".kv-body"));
  $("kvBack").onclick = () => { $("kbNotes").classList.remove("viewing"); KB.sel = null; kbRenderList(); };
  $("kvEdit").onclick = () => kbEdit(d);
  $("kvHist").onclick = () => kbHistory(n);
  $("kvStatus").onclick = async () => {
    try { await post("/api/kb/status", { path: n.path, status: n.status === "过时" ? "有效" : "过时" }); await kbLoad(); kbShow(n.path); }
    catch (e) { toast(e.message, "err"); }
  };
  $("kvDel").onclick = () => dialog("删除笔记", `<p>删除“<b>${esc(n.title)}</b>”？</p><p class="muted" style="font-size:13px">会放进回收站，30 天内可以恢复。链接到它的 ${d.backlinks.length} 条笔记会显示成“还没写”。</p>`,
    [{ label: "取消" }, { label: "删除", danger: true, onClick: async () => {
      await post("/api/kb/delete", { path: n.path }); KB.sel = null; await kbLoad(); kbFilterBar(); kbRenderList(); kbEmptyView(); toast("已删除，可以在回收站里恢复", "ok");
    } }]);
  if ($("kvRef")) $("kvRef").onclick = () => { addRef({ abs: n.abs, name: n.title, dir: false }); kbClose(); toast("已引用到对话", "ok"); $("input").focus(); };
  if ($("kvOpenSrc")) $("kvOpenSrc").onclick = () => showFiles(n.source_abs || n.source);
  if ($("kvRefresh")) $("kvRefresh").onclick = () => kbOrganize([n.source_abs || n.source], n.title);
  $("kbView").querySelector("[data-ksess]")?.addEventListener("click", ev => { if (kbClose()) openSession(ev.currentTarget.dataset.ksess); });
  $("kbView").querySelectorAll("[data-ktag2]").forEach(a => a.onclick = () => { KB.tag = a.dataset.ktag2; KB.type = ""; KB.results = null; $("kbQ").value = ""; kbFilterBar(); kbRenderList(); });
}

// 打开 [[名字]]：有就打开，没有就问要不要新建
async function kbOpenByName(name) {
  if (!$("kbBox").classList.contains("show")) await openKB("notes");
  else if (!KB.data) await kbLoad();
  const n = kbFind(name);
  if (n) { if (KB.view !== "notes") kbTab("notes"); return kbShow(n.path); }
  dialog("还没有这条笔记", `<p>知识库里还没有“<b>${esc(name)}</b>”。</p><p class="muted" style="font-size:13px">要现在写一条吗？也可以先不管，等以后有人建了它，所有链接会自动连上。</p>`,
    [{ label: "以后再说" }, { label: "新建", primary: true, icon: "plus", onClick: () => { if (KB.view !== "notes") kbTab("notes"); kbEdit(null, { title: name, type: "主题" }); } }]);
}
window.kbOpenByName = kbOpenByName;

async function kbOpenPath(rel) {
  await openKB("notes");
  kbShow(rel);
}
window.kbOpenPath = kbOpenPath;

// ---------- 编辑 ----------

function kbEdit(d, preset = {}) {
  const n = d ? d.note : { title: preset.title || "", type: preset.type || "经验", tags: [], summary: "", body: "", source: "", mtime: 0 };
  const curSec = d ? (n.section || "") : (KB.section && KB.section !== "__uncategorized__" ? KB.section : "");
  KB.editing = true; KB.dirty = false;
  $("kbNotes").classList.add("viewing");
  const types = [...Object.keys(KB_TYPES), ...(Object.keys(KB_TYPES).includes(n.type) ? [] : [n.type])];
  $("kbView").innerHTML = `
    <div class="kv-bar">
      <span class="muted" style="font-size:13px">${d ? "编辑笔记" : "新建笔记"}</span><span class="sp"></span>
      <span class="muted ke-kbd">${IS_MAC ? "⌘" : "Ctrl"} S 保存</span>
      <button class="btn small ghost" id="keCancel">取消</button>
      <button class="btn small primary" id="keSave">${icon("check", 14)}保存</button>
    </div>
    <div class="kedit">
      <input class="ke-title" id="keTitle" placeholder="标题：写成以后别人会搜的样子" value="${esc(n.title)}" maxlength="80">
      <div class="ke-row">
        <span class="seg" id="keType">${types.map(t => `<button type="button" data-t="${esc(t)}" class="${t === n.type ? "on" : ""}">${esc(t)}</button>`).join("")}</span>
        <input class="ke-in" id="keTags" placeholder="标签，用逗号分开" value="${esc(n.tags.join("，"))}">
      </div>
      <input class="ke-in" id="keSum" placeholder="一句话摘要（可选）：这条笔记解决什么问题" value="${esc(n.summary)}" maxlength="200">
      <select class="ke-section" id="keSection" title="分区">${kbSectionOptions(curSec)}</select>
      <input class="ke-in" id="keSrc" placeholder="原文件或文件夹的完整路径（文件卡片用，可选）" value="${esc(n.source || "")}" ${n.type === "文件卡片" ? "" : "hidden"}>
      <div class="ke-body">
        <textarea id="keBody" placeholder="正文，支持 Markdown：## 小标题、- 列表、**加粗**。&#10;输入 [[ 可以链接到其他笔记，比如 [[客户A]]。">${esc(n.body)}</textarea>
        <div class="ke-sug" id="keSug" hidden></div>
      </div>
    </div>`;
  const dirty = () => { KB.dirty = true; };
  $("kbView").querySelectorAll("input, textarea, select").forEach(el => el.addEventListener("input", dirty));
  $("keSection").addEventListener("change", dirty);
  $("keType").onclick = e => {
    const b = e.target.closest("[data-t]"); if (!b) return;
    $("keType").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
    $("keSrc").hidden = b.dataset.t !== "文件卡片"; dirty();
  };
  $("keCancel").onclick = () => {
    if (KB.dirty && !confirm("放弃这次修改？")) return;
    KB.editing = false; KB.dirty = false;
    if (d) kbShow(n.path); else kbEmptyView();
  };
  const save = async () => {
    const title = $("keTitle").value.trim();
    if (!title) return toast("请写个标题", "err");
    const body = {
      path: d ? n.path : "", title, type: $("keType").querySelector(".on").dataset.t,
      tags: $("keTags").value.split(/[,，、\s]+/).map(t => t.replace(/^#/, "").trim()).filter(Boolean),
      summary: $("keSum").value.trim(), body: $("keBody").value, base_mtime: d ? n.mtime : null,
      section: $("keSection").value,
    };
    const src = $("keSrc").value.trim();
    if (src !== (n.source || "")) body.source = src;
    $("keSave").disabled = true;
    try {
      const r = await post("/api/kb/note", body, "PUT");
      KB.editing = false; KB.dirty = false;
      await kbLoad(); kbFilterBar();
      toast(d ? "已保存" : "笔记已建好", "ok");
      kbShow(r.path);
    } catch (e) { toast(e.message, "err"); $("keSave").disabled = false; }
  };
  $("keSave").onclick = save;
  $("kbView").onkeydown = e => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") { e.preventDefault(); save(); } };
  kbSuggest($("keBody"), $("keSug"));
  setTimeout(() => (n.title ? $("keBody") : $("keTitle")).focus(), 30);
}

// 正文里输入 [[ 时弹出笔记名候选
function kbSuggest(ta, box) {
  let items = [], sel = 0, start = -1;
  const close = () => { box.hidden = true; items = []; };
  const pick = i => {
    const it = items[i]; if (!it) return;
    const end = ta.selectionStart;
    const after = ta.value.slice(end).startsWith("]]") ? ta.value.slice(end + 2) : ta.value.slice(end);
    ta.value = ta.value.slice(0, start) + it + "]]" + after;
    const pos = start + it.length + 2;
    ta.setSelectionRange(pos, pos); ta.focus(); close(); KB.dirty = true;
  };
  const update = () => {
    const before = ta.value.slice(0, ta.selectionStart);
    const m = /\[\[([^\[\]\n|#]*)$/.exec(before);
    if (!m) return close();
    start = ta.selectionStart - m[1].length;
    const q = m[1].toLowerCase();
    items = KB.data.notes.map(n => n.title).filter(t => t.toLowerCase().includes(q)).slice(0, 8);
    if (m[1].trim() && !items.some(t => t.toLowerCase() === q)) items.push(m[1].trim());
    if (!items.length) return close();
    sel = Math.min(sel, items.length - 1);
    box.innerHTML = `<div class="ke-sh">链接到笔记</div>` + items.map((t, i) => {
      const n = kbFind(t);
      return `<div class="ke-si ${i === sel ? "on" : ""}" data-i="${i}">${n ? `<i class="kdot k-${kcls(n.type)}"></i>` : icon("plus", 12)}<span>${esc(t)}</span>${n ? "" : '<small>新的</small>'}</div>`;
    }).join("");
    box.hidden = false;
  };
  ta.addEventListener("input", () => { sel = 0; update(); });
  ta.addEventListener("click", update);
  ta.addEventListener("blur", () => setTimeout(close, 150));
  ta.addEventListener("keydown", e => {
    if (box.hidden) return;
    if (e.key === "ArrowDown") { e.preventDefault(); sel = (sel + 1) % items.length; update(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); sel = (sel - 1 + items.length) % items.length; update(); }
    else if ((e.key === "Enter" || e.key === "Tab") && !e.isComposing) { e.preventDefault(); pick(sel); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
  });
  box.onmousedown = e => { const it = e.target.closest("[data-i]"); if (it) { e.preventDefault(); pick(+it.dataset.i); } };
}

// ---------- 历史版本 ----------

async function kbHistory(n) {
  let v;
  try { v = (await api("/api/kb/history?path=" + encodeURIComponent(n.path))).versions; } catch (e) { return toast(e.message, "err"); }
  if (!v.length) return toast("这条笔记还没有历史版本");
  const body = dialog(`“${n.title}” 的历史版本`, `
    <div class="filev">
      <div class="list" id="khList">${v.map((x, i) => `<div data-h="${x.id}" class="${i ? "" : "on"}"><b style="display:block;font-weight:600">${ftime(x.created)}</b><span class="muted" style="font-size:12px">${esc(x.by_name || "")} · ${esc(x.note || "")}</span></div>`).join("")}</div>
      <pre id="khPre">读取中…</pre>
    </div>`, [{ label: "关闭" }, { label: "恢复到这个版本", primary: true, icon: "history", onClick: async () => {
      const sel = body.querySelector("#khList .on");
      if (!sel || sel === body.querySelector("#khList div")) { toast("这已经是最新的版本"); return false; }
      await post(`/api/kb/history/${sel.dataset.h}/restore`); await kbLoad(); kbShow(n.path); toast("已恢复", "ok");
    } }], true);
  const show = async id => {
    body.querySelectorAll("#khList div[data-h]").forEach(x => x.classList.toggle("on", x.dataset.h === String(id)));
    const r = await api("/api/kb/history/" + id).catch(e => ({ content: e.message }));
    body.querySelector("#khPre").textContent = r.content;
  };
  body.querySelector("#khList").onclick = e => { const x = e.target.closest("[data-h]"); if (x) show(x.dataset.h); };
  show(v[0].id);
}

// ---------- 新建 ----------

$("kbNew").onclick = () => { if (KB.view !== "notes") kbTab("notes"); kbEdit(null); };

// ---------- 关系图谱 ----------

async function kbGraphView() {
  $("kbGTools").innerHTML = `${Object.keys(KB_TYPES).concat(["还没写的"]).map(t => {
    const key = t === "还没写的" ? "ghost" : t;
    return `<button class="gt ${KB.hidden.has(key) ? "" : "on"}" data-gt="${esc(key)}"><i class="k-${kcls(key)}"></i>${t}</button>`;
  }).join("")}<button class="gt" data-gfit>${icon("eye", 13)}看全部</button>`;
  $("kbLegend").textContent = "拖动画布移动，滚轮缩放，点圆点打开笔记";
  try { KB.graphData = await api("/api/kb/graph" + (KB.section ? "?section=" + encodeURIComponent(KB.section) : "")); } catch (e) { return toast(e.message, "err"); }
  if (KB.view !== "graph") return;
  kbGraphDraw();
}
function kbGraphDraw() {
  KB.graph?.stop();
  const d = KB.graphData;
  const nodes = d.nodes.filter(n => !KB.hidden.has(n.type === "ghost" ? "ghost" : n.type));
  if (!nodes.length) { $("kbLegend").textContent = "还没有笔记，所以图谱是空的"; }
  const ids = new Set(nodes.map(n => n.id));
  KB.graph = new KGraph($("kbCanvas"), { nodes, edges: d.edges.filter(([a, b]) => ids.has(a) && ids.has(b)) }, {
    onClick: n => { if (n.type === "ghost") kbOpenByName(n.title); else { kbTab("notes"); kbShow(n.id); } },
  });
}
$("kbGTools").onclick = e => {
  const t = e.target.closest("[data-gt]");
  if (t) { const k = t.dataset.gt; KB.hidden.has(k) ? KB.hidden.delete(k) : KB.hidden.add(k); t.classList.toggle("on"); return kbGraphDraw(); }
  if (e.target.closest("[data-gfit]")) KB.graph?.fit(true);
};

// 力导向布局：同类笔记颜色相同，被链接得越多圆点越大；mini 模式是首页顶上缓慢漂浮的“星空”
class KGraph {
  constructor(canvas, data, opts = {}) {
    this.c = canvas; this.ctx = canvas.getContext("2d"); this.o = opts; this.mini = !!opts.mini;
    const byId = {};
    this.nodes = data.nodes.map((n, i) => {
      const a = i * 2.39996, r = 6 * Math.sqrt(i + 1);
      return byId[n.id] = { ...n, x: Math.cos(a) * r, y: Math.sin(a) * r, vx: 0, vy: 0, phase: Math.random() * 6.28 };
    });
    this.edges = data.edges.map(([a, b]) => [byId[a], byId[b]]).filter(([a, b]) => a && b);
    this.nb = new Map(this.nodes.map(n => [n, new Set()]));
    for (const [a, b] of this.edges) { this.nb.get(a).add(b); this.nb.get(b).add(a); }
    this.alpha = 1; this.scale = this.mini ? 1 : 0.9; this.tx = 0; this.ty = 0; this.userMoved = false;
    this.hover = null; this.drag = null; this.t0 = performance.now();
    this.colors();
    this.resize();
    this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(canvas);
    if (!this.mini) this.bind();
    this.running = true;
    this.loop = this.loop.bind(this);
    requestAnimationFrame(this.loop);
  }
  colors() {
    const cs = getComputedStyle(document.documentElement), v = k => cs.getPropertyValue(k).trim();
    this.col = { exp: v("--accent"), card: v("--info"), topic: v("--sage"), other: v("--muted"), ghost: v("--faint"),
                 line: v("--line2"), ink: v("--ink2"), bg: v("--raised") };
  }
  resize() {
    const r = this.c.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    this.w = r.width; this.h = r.height;
    this.c.width = Math.max(1, r.width * dpr); this.c.height = Math.max(1, r.height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  stop() { this.running = false; this.ro?.disconnect(); }
  radius(n) { return (this.mini ? 2.2 : 4) + Math.sqrt(n.deg || 0) * (this.mini ? 1.3 : 2.1) + (n.type === "主题" ? 1.5 : 0); }
  step() {
    const ns = this.nodes, a = this.alpha, k = this.mini ? 38 : 62;
    for (let i = 0; i < ns.length; i++) {
      const p = ns[i];
      for (let j = i + 1; j < ns.length; j++) {
        const q = ns[j]; let dx = p.x - q.x, dy = p.y - q.y; let d2 = dx * dx + dy * dy;
        if (d2 > 90000) continue;
        if (d2 < 1) { dx = Math.random() - .5; dy = Math.random() - .5; d2 = 1; }
        const f = (this.mini ? 260 : 900) * a / d2;
        p.vx += dx * f; p.vy += dy * f; q.vx -= dx * f; q.vy -= dy * f;
      }
    }
    for (const [p, q] of this.edges) {
      const dx = q.x - p.x, dy = q.y - p.y, d = Math.sqrt(dx * dx + dy * dy) || 1, f = (d - k) / d * 0.06 * a;
      p.vx += dx * f; p.vy += dy * f; q.vx -= dx * f; q.vy -= dy * f;
    }
    for (const n of ns) {
      n.vx -= n.x * 0.006 * a; n.vy -= n.y * 0.006 * a;
      if (n === this.drag) { n.vx = n.vy = 0; continue; }
      n.vx *= 0.82; n.vy *= 0.82; n.x += n.vx; n.y += n.vy;
    }
    this.alpha = Math.max(this.alpha * 0.985, this.mini ? 0.03 : 0.004);
  }
  fit(animate = false) {
    if (!this.nodes.length) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of this.nodes) { x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x); y1 = Math.max(y1, n.y); }
    const pad = this.mini ? 20 : 70;
    const s = Math.min((this.w - pad * 2) / Math.max(x1 - x0, 1), (this.h - pad * 2) / Math.max(y1 - y0, 1), this.mini ? 1.6 : 1.8);
    this.target = { s: Math.max(s, 0.2), x: -(x0 + x1) / 2, y: -(y0 + y1) / 2 };
    if (!animate) Object.assign(this, { scale: this.target.s, tx: this.target.x, ty: this.target.y });
    this.userMoved = false;
  }
  loop(t) {
    if (!this.running) return;
    if (this.alpha > (this.mini ? 0.031 : 0.005) || this.drag || this.mini) this.step();
    // 镜头慢慢对准整张图，用户动过以后就不再自动调整
    if (!this.userMoved && t - this.t0 < 4000) this.fit(true);
    if (this.target && !this.userMoved) {
      this.scale += (this.target.s - this.scale) * 0.08; this.tx += (this.target.x - this.tx) * 0.08; this.ty += (this.target.y - this.ty) * 0.08;
    }
    this.draw(t);
    requestAnimationFrame(this.loop);
  }
  toScreen(n) { return [(n.x + this.tx) * this.scale + this.w / 2, (n.y + this.ty) * this.scale + this.h / 2]; }
  draw(t) {
    const g = this.ctx, C = this.col, hv = this.hover, near = hv ? this.nb.get(hv) : null;
    g.clearRect(0, 0, this.w, this.h);
    const wob = n => this.mini ? [Math.sin(t / 1600 + n.phase) * 3, Math.cos(t / 1900 + n.phase) * 3] : [0, 0];
    g.lineWidth = this.mini ? 0.8 : 1;
    for (const [p, q] of this.edges) {
      const [ax, ay] = this.toScreen(p), [bx, by] = this.toScreen(q), [pw, ph] = wob(p), [qw, qh] = wob(q);
      const lit = hv && (p === hv || q === hv);
      g.strokeStyle = lit ? C[kcls(hv.type)] : C.line;
      g.globalAlpha = hv && !lit ? 0.25 : this.mini ? 0.7 : 1;
      g.beginPath(); g.moveTo(ax + pw, ay + ph); g.lineTo(bx + qw, by + qh); g.stroke();
    }
    for (const n of this.nodes) {
      const [x0, y0] = this.toScreen(n), [w, h] = wob(n), x = x0 + w, y = y0 + h;
      const r = this.radius(n) * (this.mini ? 1 : Math.min(Math.max(this.scale, 0.6), 1.4));
      const dim = hv && n !== hv && !near.has(n);
      const color = C[kcls(n.type)] || C.other;
      g.globalAlpha = dim ? 0.2 : 1;
      if (n.type === "ghost") {
        g.setLineDash([2, 2]); g.strokeStyle = color; g.lineWidth = 1.2;
        g.beginPath(); g.arc(x, y, r, 0, 6.283); g.stroke(); g.setLineDash([]);
      } else {
        if (n === hv || this.mini) { g.shadowColor = color; g.shadowBlur = this.mini ? 10 : 16; }
        g.fillStyle = color; g.beginPath(); g.arc(x, y, r, 0, 6.283); g.fill();
        g.shadowBlur = 0;
        if (n.status === "过时") { g.strokeStyle = C.bg; g.lineWidth = 1.5; g.beginPath(); g.moveTo(x - r * .6, y); g.lineTo(x + r * .6, y); g.stroke(); }
      }
      if (!this.mini && n.title && (n === hv || (near && near.has(n)) || this.scale > 1.25 || (n.deg || 0) >= 3 || this.nodes.length < 25)) {
        g.globalAlpha = dim ? 0.2 : n === hv ? 1 : 0.85;
        g.font = `${n === hv ? 600 : 500} 12px ${getComputedStyle(document.body).fontFamily}`;
        g.textAlign = "center"; g.fillStyle = C.ink;
        const label = n.title.length > 16 ? n.title.slice(0, 15) + "…" : n.title;
        g.lineWidth = 3; g.strokeStyle = C.bg; g.strokeText(label, x, y + r + 14); g.fillText(label, x, y + r + 14);
      }
    }
    g.globalAlpha = 1;
  }
  hit(mx, my) {
    let best = null, bd = Infinity;
    for (const n of this.nodes) {
      const [x, y] = this.toScreen(n), d = Math.hypot(x - mx, y - my), r = this.radius(n) + 6;
      if (d < r && d < bd) { best = n; bd = d; }
    }
    return best;
  }
  bind() {
    const c = this.c;
    const pos = e => { const r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    let down = null;
    c.onpointerdown = e => {
      const [mx, my] = pos(e); c.setPointerCapture(e.pointerId);
      down = { mx, my, tx: this.tx, ty: this.ty, node: this.hit(mx, my), moved: false };
      if (down.node) { this.drag = down.node; this.alpha = Math.max(this.alpha, 0.3); }
    };
    c.onpointermove = e => {
      const [mx, my] = pos(e);
      if (down) {
        if (Math.hypot(mx - down.mx, my - down.my) > 4) down.moved = true;
        if (down.node) { down.node.x = (mx - this.w / 2) / this.scale - this.tx; down.node.y = (my - this.h / 2) / this.scale - this.ty; this.alpha = Math.max(this.alpha, 0.2); }
        else { this.tx = down.tx + (mx - down.mx) / this.scale; this.ty = down.ty + (my - down.my) / this.scale; }
        if (down.moved) this.userMoved = true;
      } else {
        this.hover = this.hit(mx, my);
        c.style.cursor = this.hover ? "pointer" : "grab";
      }
    };
    c.onpointerup = () => {
      if (down && down.node && !down.moved) this.o.onClick?.(down.node);
      this.drag = null; down = null;
    };
    c.onpointerleave = () => { if (!down) this.hover = null; };
    c.onwheel = e => {
      e.preventDefault();
      const [mx, my] = pos(e), f = Math.exp(-e.deltaY * 0.0015), s = Math.min(Math.max(this.scale * f, 0.15), 4);
      const wx = (mx - this.w / 2) / this.scale - this.tx, wy = (my - this.h / 2) / this.scale - this.ty;
      this.scale = s; this.tx = (mx - this.w / 2) / s - wx; this.ty = (my - this.h / 2) / s - wy;
      this.userMoved = true;
    };
  }
}

// ---------- 和聊天、文件库的衔接 ----------

// 在 Claude 的回答下面点“沉淀成经验”
async function kbDistill() {
  if (!current || !isMine()) return;
  if (currentSess && currentSess.running) return toast("等这一轮做完再沉淀");
  try { await post(`/api/sessions/${current}/messages`, { action: "distill" }); }
  catch (e) { toast(e.message, "err"); }
}
window.kbDistill = kbDistill;

// 文件库里点“整理进知识库”：新开一个会话让 Claude 写文件卡片
async function kbOrganize(paths, label) {
  if (!KB.sectionsLoaded) await kbLoadSections();
  const name = label || baseName(paths[0]);
  const curId = currentSess?.section_id ?? (KB.section && KB.section !== "__uncategorized__"
    ? (KB.sections.find(s => s.name === KB.section)?.id ?? 0) : 0);
  const secOpts = [`<option value="0"${!curId ? " selected" : ""}>未分类</option>`]
    .concat(KB.sections.filter(s => !s.archived).map(s => `<option value="${s.id}"${Number(curId) === Number(s.id) ? " selected" : ""}>${esc(s.name)}</option>`)).join("");
  dialog("整理进知识库", `
    <div class="kb-org">${orb(46)}<div>
      <p style="margin-top:0">Claude 会读一遍“<b>${esc(name)}</b>”，为重要的文件写<b>文件卡片</b>：摘要、关键信息（金额、日期、对方……）、原文件位置，并和相关的项目、客户连起来。</p>
      <p class="muted" style="font-size:13px;margin-bottom:0">会新开一个会话来做，你可以随时去看进度。文件夹很大时会先做最重要的 30 个文件。</p>
    </div></div>
    <select class="ke-section" id="orgSection" title="分区">${secOpts}</select>`,
    [{ label: "取消" }, { label: "开始整理", primary: true, icon: "book", onClick: async () => {
      const section_id = Number($("orgSection").value || 0);
      const s = await post("/api/sessions", { title: ("整理：" + name).slice(0, 20), section_id });
      upsertSession(s);
      await post(`/api/sessions/${s.id}/messages`, { action: "organize", refs: paths });
      $("filesBox").classList.remove("show");
      if ($("kbBox").classList.contains("show")) kbClose();
      openSession(s.id);
    } }]);
}
window.kbOrganize = kbOrganize;

// 点 [[链接]]、点知识库里 .md 文件的路径 → 在知识库里打开
document.addEventListener("click", e => {
  const wl = e.target.closest("[data-kb]");
  if (wl) { e.preventDefault(); e.stopPropagation(); return kbOpenByName(wl.dataset.kb); }
  const fp = e.target.closest("[data-fpath]");
  const rel = fp && kbRelOf(fp.dataset.fpath);
  if (rel) { e.preventDefault(); e.stopPropagation(); kbOpenPath(rel); }
}, true);

// 文件预览时：这份文件有没有知识库卡片？本身是不是知识库笔记？
async function kbFileBanner(entry) {
  const box = $("fcontent");
  box.querySelector(".kb-fbanner")?.remove();
  if (!KB.data || entry.dir) return;
  const rel = kbRelOf(entry.abs);
  let html = "";
  if (rel) html = `${icon("book", 15)}<span>这是知识库里的笔记</span><button class="btn small" data-kopen="${esc(rel)}">在知识库中打开</button>`;
  else {
    const r = await api("/api/kb/cards?path=" + encodeURIComponent(entry.path)).catch(() => ({ cards: [] }));
    if (F.sel !== entry.path || !r.cards.length) return;
    const c = r.cards[0];
    html = `${icon("book", 15)}<span>知识库里有这份文件的卡片：<b>${esc(c.title)}</b>${c.source_state === "changed" ? '<span class="tag warn">文件改过，卡片可能过时</span>' : ""}</span><button class="btn small" data-kopen="${esc(c.path)}">看卡片</button>`;
  }
  if (F.sel !== entry.path) return;
  box.insertAdjacentHTML("afterbegin", `<div class="kb-fbanner">${html}</div>`);
  box.querySelector("[data-kopen]").onclick = ev => { $("filesBox").classList.remove("show"); kbOpenPath(ev.currentTarget.dataset.kopen); };
}
window.kbFileBanner = kbFileBanner;

$("kbLogo").innerHTML = icon("book", 19);
$("kbQIcon").innerHTML = icon("search", 15);
$("kbNew").innerHTML = icon("plus", 14) + "新建笔记";
$("kbNavIcon").innerHTML = icon("book", 16);
$("kbBtn").innerHTML = icon("book");
$("kbNav").onclick = () => openKB("home");
$("kbBtn").onclick = () => openKB("home");
