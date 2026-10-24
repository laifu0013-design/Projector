// Claude 工作台网页端：登录、会话、引用文件、技能、本机文件、文件预览、命令面板。管理后台在 admin.js，小零件在 ui.js。
"use strict";
const $ = id => document.getElementById(id);
const store = {
  get(k) { try { return localStorage.getItem(k) || ""; } catch { return ""; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
  del(k) { try { localStorage.removeItem(k); } catch {} },
};
const IS_DESKTOP = !!(window.__TAURI__ && window.__TAURI__.core);
const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform);
const invoke = (cmd, args) => window.__TAURI__.core.invoke(cmd, args);

let token = store.get("token");
let me = null;               // 当前用户
let sessions = [];           // 自己的会话
let current = null;          // 当前会话 id，null 表示还没发消息的新会话
let currentSess = null;      // 当前会话对象（可能是别人的，管理员查看时）
let messages = [];
let live = null;             // 正在进行的回复 {text, tools}
let approvals = [];
let links = [];              // 当前会话上传的本机文件夹
let es = null;
const composer = { refs: [], skill: "", workDir: "" };  // workDir：新会话还没创建时先记着
let mySkills = [];
const seen = new Set();      // 已经显示过的消息，只给新消息播入场动画
let liveSteps = 0;           // 实时步骤里已经显示过几步

// ---------- 通用 ----------

function api(path, opts = {}) {
  const headers = { Authorization: "Bearer " + token, ...(opts.headers || {}) };
  if (!opts.raw) headers["Content-Type"] = "application/json";
  return fetch(path, { ...opts, headers }).then(async r => {
    if (r.status === 401 && !path.startsWith("/api/login")) { showLogin(); throw new Error("请先登录"); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || ("出错了：" + r.status));
    return data;
  });
}
const post = (path, body, method = "POST") => api(path, { method, body: JSON.stringify(body || {}) });

function md(text) {
  if (window.marked && window.DOMPurify) return DOMPurify.sanitize(marked.parse(text || ""));
  return `<div style="white-space:pre-wrap">${esc(text)}</div>`;
}
function esc(s) { const d = document.createElement("div"); d.textContent = s ?? ""; return d.innerHTML.replaceAll('"', "&quot;"); }
function fsize(n) {
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(0) + " KB";
  if (n < 1073741824) return (n / 1048576).toFixed(1) + " MB";
  return (n / 1073741824).toFixed(1) + " GB";
}
const pad = n => String(n).padStart(2, "0");
const fdate = t => { const d = new Date(t * 1000); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
const ftime = t => { const d = new Date(t * 1000); return `${fdate(t)} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const hm = t => { const d = new Date(t * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
function rel(t) {
  const d = new Date(t * 1000), now = new Date();
  const day = Math.floor((new Date(now.toDateString()) - new Date(d.toDateString())) / 86400000);
  if (day === 0) return hm(t);
  if (day === 1) return "昨天";
  if (day < 7) return day + " 天前";
  return `${d.getMonth() + 1}/${d.getDate()}`;
}
const baseName = p => p.split(/[\\/]/).filter(Boolean).pop() || p;
const flash = (text, ms) => toast(text, "info", ms);

// 通用对话框：dialog(标题, 内容 html, [{label, primary, danger, onClick}])；onClick 返回 false 则不关闭
function dialog(title, html, buttons = [], wide = false) {
  $("dlgTitle").textContent = title;
  $("dlgBody").innerHTML = html;
  $("dlg").classList.toggle("narrow", !wide);
  $("dlgFoot").innerHTML = "";
  $("dlgFoot").hidden = !buttons.length;
  for (const b of buttons) {
    const el = document.createElement("button");
    el.className = "btn" + (b.primary ? " primary" : "") + (b.danger ? " danger" : "");
    el.innerHTML = (b.icon ? icon(b.icon, 15) : "") + esc(b.label);
    el.onclick = async () => {
      el.disabled = true;
      try { if ((await b.onClick?.()) !== false) closeDialog(); }
      catch (e) { const err = $("dlgBody").querySelector(".err"); if (err) err.textContent = e.message; else toast(e.message, "err"); }
      finally { el.disabled = false; }
    };
    $("dlgFoot").appendChild(el);
  }
  $("dlgBox").classList.add("show");
  setTimeout(() => $("dlgBody").querySelector("input:not([type=checkbox]), textarea")?.focus(), 60);
  return $("dlgBody");
}
function closeDialog() { $("dlgBox").classList.remove("show"); }

// ---------- 静态图标 ----------

function paintStatic() {
  $("brand").innerHTML = `${orb(30)}<div><div class="b1">Projector</div><div class="b2">CLAUDE 工作台</div></div>`;
  $("newBtn").innerHTML = icon("plus", 18) + "新会话";
  $("sqIcon").innerHTML = icon("search", 15);
  $("kbdK").textContent = IS_MAC ? "⌘K" : "Ctrl K";
  $("menuBtn").innerHTML = icon("menu", 20);
  $("filesBtn").innerHTML = icon("folder");
  $("stepsBtn").innerHTML = icon("steps");
  $("paletteBtn").innerHTML = icon("command");
  $("meChev").innerHTML = icon("chevD", 16, "muted");
  $("refBtn").innerHTML = icon("clip", 16) + "<span>引用文件</span>";
  $("localBtn").innerHTML = icon("laptop", 16) + "<span>本机文件</span>";
  $("skillBtn").innerHTML = icon("puzzle", 16) + "<span>技能</span>";
  $("sendBtn").innerHTML = icon("send", 18);
  $("stopBtn").innerHTML = icon("stop", 14);
  $("dzOrb").innerHTML = orb(64, "busy");
  $("frefDir").innerHTML = icon("clip", 14) + "引用此文件夹";
  $("fback").innerHTML = icon("chevL");
  $("fref").innerHTML = icon("clip", 14) + "引用到对话";
  $("flocal").innerHTML = icon("external", 14) + "用本机程序打开";
  $("fdown").innerHTML = icon("download", 14) + "下载";
  $("fUpFile").innerHTML = icon("upload", 14) + "上传文件";
  $("fUpDir").innerHTML = icon("folder", 14) + "上传文件夹";
  $("fMkdir").innerHTML = icon("plus", 14) + "新建文件夹";
  $("fTrash").innerHTML = icon("trash", 14) + "回收站";
  $("fren").innerHTML = icon("edit", 16);
  $("fdel").innerHTML = icon("trash", 16);
  $("palIcon").innerHTML = icon("search", 18);
  document.querySelectorAll(".closebtn").forEach(b => b.innerHTML = icon("x"));
  // 登录页：橙光球外面两圈慢慢转动的水果头像
  const ring = (ids, r, cls, size) => `<div class="ring ${cls}">${ids.map((id, i) => {
    const a = i / ids.length * Math.PI * 2;
    return `<span style="transform:translate(${Math.cos(a) * r - size / 2}px, ${Math.sin(a) * r - size / 2}px)">${avatarEl(id, size)}</span>`;
  }).join("")}</div>`;
  $("orbit").innerHTML = ring(["orange", "grape", "kiwi", "peach", "blueberry", "pineapple", "lemon", "strawberry"], 190, "", 46)
    + ring(["avocado", "cherry", "watermelon", "plum", "lime"], 120, "r2", 34) + orb(120);
}
paintStatic();

document.querySelectorAll("[data-close]").forEach(b => b.onclick = () => b.closest(".overlay").classList.remove("show"));
document.querySelectorAll(".overlay").forEach(o => o.addEventListener("mousedown", e => {
  if (e.target === o && o.id !== "login") o.classList.remove("show");
}));

// ---------- 登录 ----------

let setupMode = false;
async function showLogin() {
  if (es) { es.close(); es = null; }
  $("login").classList.add("show");
  $("lgErr").textContent = "";
  try {
    const s = await fetch("/api/setup").then(r => r.json());
    setupMode = s.needed;
    if (s.needed && !s.local) $("lgErr").textContent = "还没有管理员。请在台式机本机打开 http://localhost:" + location.port + " 创建";
  } catch { setupMode = false; }
  $("loginTitle").textContent = setupMode ? "创建管理员" : "欢迎回来";
  $("loginLead").textContent = setupMode ? "这个账号可以管理用户、聊天记录和技能库" : "用管理员给你的账号登录";
  $("setupHint").hidden = !setupMode;
  for (const id of ["lgName", "lgNameL", "lgPwd2", "lgPwd2L"]) $(id).hidden = !setupMode;
  $("lgBtn").textContent = setupMode ? "创建并进入" : "登录";
  $("lgNote").hidden = setupMode;
  $("lgPwd").autocomplete = setupMode ? "new-password" : "current-password";
  $("lgUser").value = store.get("username");
  setTimeout(() => ($("lgUser").value ? $("lgPwd") : $("lgUser")).focus(), 100);
}

$("loginForm").onsubmit = async e => {
  e.preventDefault();
  $("lgErr").textContent = "";
  const username = $("lgUser").value.trim(), password = $("lgPwd").value;
  if (setupMode && password !== $("lgPwd2").value) { $("lgErr").textContent = "两次密码不一样"; return; }
  $("lgBtn").disabled = true;
  try {
    const r = await fetch(setupMode ? "/api/setup" : "/api/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, display_name: $("lgName").value.trim() }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || "登录失败");
    token = data.token; store.set("token", token); store.set("username", username);
    $("lgPwd").value = $("lgPwd2").value = "";
    $("login").classList.remove("show");
    enter(data.user);
    toast(`欢迎，${data.user.display_name}`, "ok");
  } catch (err) {
    $("lgErr").textContent = err.message;
    $("loginForm").animate([{ transform: "translateX(0)" }, { transform: "translateX(-8px)" }, { transform: "translateX(8px)" }, { transform: "translateX(0)" }], { duration: 300 });
  } finally { $("lgBtn").disabled = false; }
};

function paintMe() {
  $("me").textContent = me.display_name;
  $("meAva").innerHTML = avatarEl(me.avatar, 36);
  $("roleTag").innerHTML = me.role === "admin" ? '<span class="tag admin">管理员</span>' : esc("@" + me.username);
}

function enter(user) {
  me = user;
  paintMe();
  sessions = []; current = null; currentSess = null; messages = []; live = null; approvals = []; links = [];
  seen.clear();
  resetComposer();
  connect();
  loadSkills().then(() => { if (!messages.length && !live) renderMessages(true); });
  renderSessions();
  renderMessages();
}

// ---------- 用户菜单：头像、技能、管理、主题、密码、退出 ----------

const THEMES = { auto: ["跟随系统", "sparkle"], light: ["浅色", "sun"], dark: ["深色", "moon"] };
function renderUserMenu() {
  const th = THEMES[store.get("theme") || "auto"];
  $("userMenu").innerHTML = `
    <button data-act="avatar">${icon("user", 16)}更换头像</button>
    <button data-act="skills">${icon("puzzle", 16)}我的技能</button>
    <button data-act="mobile">${icon("phone", 16)}在手机上打开</button>
    ${me.role === "admin" ? `<button data-act="admin">${icon("shield", 16)}管理后台</button>` : ""}
    <hr>
    <button data-act="theme">${icon(th[1], 16)}主题：${th[0]}</button>
    <button data-act="pwd">${icon("key", 16)}修改密码</button>
    <button data-act="logout">${icon("logout", 16)}退出登录</button>`;
}
$("userCard").onclick = e => {
  const act = e.target.closest("[data-act]")?.dataset.act;
  if (!act) {
    if (e.target.closest("#userMenu")) return;
    renderUserMenu(); $("userMenu").hidden = !$("userMenu").hidden; return;
  }
  $("userMenu").hidden = true;
  if (act === "avatar") pickAvatar();
  if (act === "skills") loadSkills().then(openMySkills);
  if (act === "mobile") showMobile();
  if (act === "admin") window.openAdmin && window.openAdmin();
  if (act === "theme") {
    const order = ["auto", "light", "dark"], cur = store.get("theme") || "auto";
    const next = order[(order.indexOf(cur) + 1) % 3];
    store.set("theme", next); applyTheme(next);
    toast("主题：" + THEMES[next][0]);
  }
  if (act === "pwd") changePassword();
  if (act === "logout") logout();
};
document.addEventListener("click", e => {
  if (!e.target.closest("#userCard")) $("userMenu").hidden = true;
  if (!e.target.closest(".anchor")) $("skillPop").hidden = true;
});

function pickAvatar() {
  const body = dialog("选一个头像", `<div class="ava-grid">${AVATAR_LIST.map(([id, n], i) =>
    `<button class="ava-opt ${id === me.avatar ? "on" : ""}" data-ava="${id}" style="animation-delay:${i * 25}ms">${avatarEl(id, 56)}${n}</button>`).join("")}</div>`);
  body.onclick = async e => {
    const b = e.target.closest("[data-ava]");
    if (!b) return;
    body.querySelectorAll(".ava-opt").forEach(x => x.classList.toggle("on", x === b));
    try {
      me = { ...me, ...(await post("/api/me", { avatar: b.dataset.ava }, "PATCH")) };
      paintMe(); renderMessages(true);
      setTimeout(closeDialog, 250);
      toast("头像换成了" + AVATAR_NAMES[b.dataset.ava], "ok");
    } catch (err) { toast(err.message, "err"); }
  };
}

function changePassword() {
  dialog("修改密码", `<div class="form">
    <input class="field" type="password" id="pwOld" placeholder="原密码" autocomplete="current-password">
    <input class="field" type="password" id="pwNew" placeholder="新密码（至少 6 位）" autocomplete="new-password">
    <input class="field" type="password" id="pwNew2" placeholder="再输一次新密码" autocomplete="new-password">
    <div class="err"></div></div>`, [{
    label: "保存", primary: true, onClick: async () => {
      if ($("pwNew").value !== $("pwNew2").value) throw new Error("两次新密码不一样");
      await post("/api/me/password", { old: $("pwOld").value, new: $("pwNew").value });
      toast("密码已修改", "ok");
    },
  }]);
}

async function logout() {
  if (!confirm("退出登录？")) return;
  try { await post("/api/logout"); } catch {}
  token = ""; store.del("token"); me = null;
  showLogin();
}

// ---------- 会话列表 ----------

function sessRow(s, adminView = false) {
  const lead = s.running ? orb(14, "busy") : s.pending ? '<span class="pend"></span>' : (s.source === "voice" ? icon("mic", 14) : icon("chat", 14));
  return `<div class="sess ${s.id === current ? "active" : ""}" data-id="${s.id}" title="${esc(s.title)}">
      <span class="lead">${lead}</span>
      <span class="t">${esc(s.title)}</span>
      ${s.pending ? '<span class="tag warn">待确认</span>' : ""}
      <span class="tm">${adminView ? ftime(s.updated) : rel(s.updated)}</span>
      ${adminView ? "" : `<button class="x" data-del="${s.id}" title="删除">${icon("trash", 14)}</button>`}
    </div>`;
}

function renderSessions() {
  const q = $("sessQ").value.trim().toLowerCase();
  const list = sessions.filter(s => !q || s.title.toLowerCase().includes(q));
  if (!list.length) {
    $("sessions").innerHTML = `<div class="empty-note" style="padding:20px 10px;text-align:center">${q ? "没有匹配的会话" : "还没有会话<br>在右边输入就能开始"}</div>`;
    return;
  }
  const today = new Date(new Date().toDateString()) / 1000;
  const groups = [["今天", s => s.updated >= today], ["昨天", s => s.updated >= today - 86400], ["7 天内", s => s.updated >= today - 6 * 86400], ["更早", () => true]];
  const used = new Set();
  let html = "", idx = 0;
  for (const [name, test] of groups) {
    const items = list.filter(s => !used.has(s.id) && test(s));
    if (!items.length) continue;
    items.forEach(s => used.add(s.id));
    html += `<div class="sgroup">${name}</div>` + items.map(s => sessRow(s).replace('class="sess', `style="animation-delay:${Math.min(idx++, 12) * 18}ms" class="sess`)).join("");
  }
  $("sessions").innerHTML = html;
}
$("sessQ").addEventListener("input", renderSessions);

// ---------- 顶栏和消息 ----------

const isMine = () => !currentSess || currentSess.user_id === me.id;

function renderTop() {
  const s = currentSess;
  const mine = isMine();
  $("title").textContent = s ? s.title : "新会话";
  const running = !!(s && s.running);
  $("stopBtn").hidden = !running;
  $("sendBtn").hidden = running;
  $("cbox").classList.toggle("running", running);
  $("bypassWrap").hidden = !me.can_bypass || (s && s.source === "voice") || !mine;
  $("bypass").checked = !!(s && s.bypass);
  $("bypass").disabled = !s;
  $("bypassWrap").classList.toggle("on", !!(s && s.bypass));
  $("stepsBtn").disabled = !s;
  const wd = s ? s.work_dir : (composer.workDir || "");
  $("wdBtn").innerHTML = icon("folder", 13) + `<span>${esc(wd ? baseName(wd) : "默认工作目录")}</span>`;
  $("wdBtn").title = (wd || "默认工作目录") + "\n工作文件夹：Claude 在这个文件夹里干活。只能在会话开始前选";
  $("wdBtn").disabled = !mine || !!(s && s.claude_session);
  $("ownerTag").innerHTML = s && !mine ? `${avatarEl(s.owner_avatar, 18)} ${esc(s.owner)}` : (s && s.bypass ? '<span class="tag warn">完全放行中</span>' : "");
  $("ownerTag").style.cssText = "display:inline-flex;align-items:center;gap:6px";
  $("composer").hidden = !mine;
  $("otherBanner").hidden = mine;
  if (!mine) $("otherBanner").innerHTML = `${icon("eye", 16)}你在查看 <b>${esc(s.owner)}</b> 的会话${s.source === "voice" ? "（板子语音）" : ""}，只能看。待确认的操作可以代为处理。`;
  $("cfoot").textContent = s && s.bypass ? "完全放行已打开：所有操作都会直接执行，不再询问" : "Claude 在台式机上干活，重要的改动请再核对一遍";
  renderLinks();
}

const TOOL_META = {
  Read: ["读取", "book"], Glob: ["查找文件", "search"], Grep: ["搜索内容", "search"], Edit: ["修改", "edit"], MultiEdit: ["修改", "edit"],
  Write: ["写入", "file"], Bash: ["运行命令", "terminal"], WebFetch: ["打开网页", "globe"], Skill: ["使用技能", "puzzle"],
  Task: ["子任务", "bolt"], TodoWrite: ["列计划", "steps"], NotebookEdit: ["修改笔记本", "edit"],
};
// 把文件库、工作区下的完整路径缩短成“文件库/…”“工作区/…”
function shortPath(p) {
  if (!p) return p;
  const names = ["文件库", "工作区"];
  for (const i of [1, 0]) {  // 工作区可能在文件库里面，先认工作区
    if (!F.roots[i]) continue;
    const root = F.roots[i].replace(/[\\/]$/, "");
    if (p.toLowerCase().startsWith(root.toLowerCase())) {
      let rest = p.slice(root.length).replace(/^[\\/]/, "").replaceAll("\\", "/");
      if (i === 1) rest = rest.split("/").slice(2).join("/");  // 去掉用户和会话两层
      return names[i] + "/" + rest;
    }
  }
  return p;
}
function stepLi(t, cls = "") {
  const [label, ic] = TOOL_META[t.name] || [t.name.replace(/^mcp__\w+__/, ""), "bolt"];
  const d = shortPath(t.detail);
  return `<li class="${cls}"><span class="dot">${icon(ic, 12)}</span><span class="lb">${esc(label)}</span>${d ? `<span class="dt" title="${esc(t.detail)}">${esc(d)}</span>` : ""}</li>`;
}
function stepsDone(tools) {
  if (!tools || !tools.length) return "";
  return `<details class="steps"><summary>${icon("steps", 14)}做了 ${tools.length} 步${icon("chevD", 14)}</summary><ol class="tl">${tools.map(t => stepLi(t)).join("")}</ol></details>`;
}
function stepsLive(tools, waiting) {
  if (!tools.length) return "";
  const html = tools.map((t, i) => stepLi(t, (i >= liveSteps ? "new " : "") + (i === tools.length - 1 && !waiting ? "cur" : ""))).join("");
  liveSteps = tools.length;
  return `<ol class="tl">${html}</ol>`;
}

function chipsHtml(meta) {
  const refs = (meta && meta.refs) || [];
  const skill = meta && meta.skill;
  if (!refs.length && !skill) return "";
  return '<div class="chips">' + (skill ? `<span class="chip skill">${icon("puzzle", 12)}<span>${esc(skill)}</span></span>` : "") +
    refs.map(r => `<span class="chip ref" data-fpath="${esc(r)}" title="${esc(r)}">${icon("clip", 12)}<span>${esc(baseName(r))}</span></span>`).join("") + "</div>";
}

function aiHead(t, state = "idle") {
  return `<div class="ai-head">${orb(26, state)}<span class="who">Claude</span>${t ? `<span class="tm">${hm(t)}</span>` : ""}</div>`;
}

function greeting() {
  const h = new Date().getHours();
  return h < 5 ? "夜深了" : h < 11 ? "早上好" : h < 13 ? "中午好" : h < 18 ? "下午好" : "晚上好";
}

function welcomeHtml() {
  const cards = [
    ["search", "找文件", "比如：帮我在 D 盘里找上个月的合同", "fill:帮我在 D 盘里找一下上个月的合同，列出路径和修改时间"],
    ["clip", "读懂一份文档", "引用文件库里的文件，让 Claude 提炼要点", "ref"],
    ["laptop", "处理本机文件", "把这台电脑上的表格交给 Claude 汇总，结果同步回来", "local"],
    ["puzzle", "使用技能", mySkills.length ? `你有 ${mySkills.length} 个可用技能，选一个开始` : "看看你能用哪些技能，或者提交一个新的", "skill"],
  ];
  return `<div class="welcome">
    <div class="w-orb">${orb(76)}</div>
    <h1 class="w-hi"><em>${greeting()}</em><span class="cn">，${esc(me.display_name)}</span></h1>
    <p class="w-sub">Claude 已经在台式机上准备好了。删除、移动文件和运行命令前，都会先在这里请你确认。</p>
    <div class="w-cards">${cards.map(([ic, t, d, act]) => `<div class="w-card" data-act="${esc(act)}"><span class="wi">${icon(ic, 19)}</span><div><b>${t}</b><small>${esc(d)}</small></div></div>`).join("")}</div>
  </div>`;
}

function renderMessages(force = false) {
  if (!me) return;
  renderTop();
  if (!messages.length && !live && !approvals.length) {
    if (force || !$("list").querySelector(".welcome")) $("list").innerHTML = welcomeHtml();
    return;
  }
  const ownerAva = currentSess ? currentSess.owner_avatar : me.avatar;
  let html = messages.map(m => {
    const meta = m.meta || {};
    const isNew = !seen.has(m.id) ? " new" : "";
    if (m.role === "user") {
      const tag = meta.bypass ? " · 完全放行" : "";
      return `<div class="msg me${isNew}"><div class="me-col">${chipsHtml(meta)}<div class="bubble">${esc(m.content)}</div><div class="meta">${esc(meta.author || "")} · ${hm(m.created)}${tag}</div></div>${avatarEl(ownerAva, 32)}</div>`;
    }
    if (m.role === "event") {
      const kind = m.content.startsWith("✔") ? "ok" : (m.content.startsWith("✘") || m.content.startsWith("⌛")) ? "no" : "";
      return `<div class="msg evt ${kind}${isNew}"><span>${icon(kind === "ok" ? "check" : kind === "no" ? "x" : "sparkle", 13)}${esc(m.content.replace(/^[✔✘⌛]\s*/, ""))}</span></div>`;
    }
    const info = [meta.seconds != null ? meta.seconds + " 秒" : "", meta.cost ? "$" + meta.cost.toFixed(4) : ""].filter(Boolean).join(" · ");
    const body = m.role === "error" ? `<div class="errbox">${icon("alert", 17)}<div>${esc(m.content)}</div></div>` : `<div class="md">${md(m.content)}</div>`;
    return `<div class="msg ai${isNew}">${aiHead(m.created)}<div class="ai-body">${stepsDone(meta.tools)}${body}${info ? `<div class="meta" style="margin-top:8px">${info}</div>` : ""}</div></div>`;
  }).join("");
  if (live) {
    const waiting = approvals.length > 0;
    html += `<div class="msg ai${seen.has("live") ? "" : " new"}">${aiHead(null, waiting ? "wait" : "busy")}<div class="ai-body">${stepsLive(live.tools, waiting)}<div class="md ${waiting ? "" : "typing"}">${md(live.text)}</div></div></div>`;
  }
  html += approvals.map(a => `
    <div class="ticket">
      <div class="t-side">${orb(34, "wait")}</div>
      <div class="t-main">
        <div class="t-kick">需要你确认</div>
        <div class="t-title">${esc(a.summary)}</div>
        <pre>${approvalDetail(a)}</pre>
        <div class="t-acts">
          <button class="btn" data-deny="${a.id}">${icon("x", 15)}拒绝</button>
          <button class="btn primary" data-allow="${a.id}">${icon("check", 15)}同意</button>
        </div>
      </div>
    </div>`).join("");
  const box = $("messages");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
  $("list").innerHTML = html;
  messages.forEach(m => seen.add(m.id));
  if (live) seen.add("live");
  linkifyPaths($("list"));
  if (atBottom || approvals.length) box.scrollTop = box.scrollHeight;
}

function approvalDetail(a) {
  const i = a.input || {};
  if (a.tool === "Bash") return esc(i.command) + (i.description ? `\n\n# ${esc(i.description)}` : "");
  if (a.tool === "Edit") return `<span class="del">- ${esc(i.old_string)}</span>\n<span class="add">+ ${esc(i.new_string)}</span>`;
  if (a.tool === "Write") { const c = i.content || ""; return esc(c.slice(0, 2000)) + (c.length > 2000 ? "\n……" : ""); }
  return esc(JSON.stringify(i, null, 1));
}

$("list").addEventListener("click", e => {
  const card = e.target.closest(".w-card");
  if (card) {
    const act = card.dataset.act;
    if (act.startsWith("fill:")) { $("input").value = act.slice(5); autosize(); $("input").focus(); }
    if (act === "ref") showFiles();
    if (act === "local") $("localBtn").click();
    if (act === "skill") { if (mySkills.length) openSkillPop(); else loadSkills().then(openMySkills); }
    return;
  }
  const id = e.target.closest("[data-allow],[data-deny]");
  if (id) {
    const allow = !!id.dataset.allow;
    id.closest(".ticket").querySelectorAll("button").forEach(b => b.disabled = true);
    id.closest(".ticket").animate([{ opacity: 1 }, { opacity: .4, transform: "scale(.98)" }], { duration: 250, fill: "forwards" });
    post("/api/approvals/" + (id.dataset.allow || id.dataset.deny), { allow }).catch(err => toast(err.message, "err"));
  }
});

async function loadSessions() {
  sessions = await api("/api/sessions");
  renderSessions();
}

async function openSession(id) {
  current = id;
  $("side").classList.remove("open");
  seen.clear(); liveSteps = 0;
  if (!id) {
    currentSess = null; messages = []; live = null; approvals = []; links = [];
    renderSessions(); renderMessages(true); $("input").focus(); return;
  }
  const data = await api("/api/sessions/" + id);
  if (current !== id) return;
  currentSess = data.session; messages = data.messages; live = data.live; approvals = data.approvals;
  links = await api(`/api/sessions/${id}/links`).catch(() => []);
  renderSessions(); renderMessages(true);
  $("messages").style.scrollBehavior = "auto";
  $("messages").scrollTop = $("messages").scrollHeight;
  $("messages").style.scrollBehavior = "";
}

function upsertSession(s) {
  if (s.user_id === me.id) {
    const i = sessions.findIndex(x => x.id === s.id);
    if (i >= 0) sessions.splice(i, 1);
    sessions.unshift(s);
    sessions.sort((a, b) => b.updated - a.updated);
    renderSessions();
  }
  if (window.adminOnSession) window.adminOnSession(s);
  if (s.id === current) { currentSess = s; if (!s.running) { live = null; liveSteps = 0; } renderMessages(); }
}

function notify(text) {
  if (document.hasFocus()) return;
  if (IS_DESKTOP) {
    invoke("plugin:notification|notify", { options: { title: "Claude 工作台", body: text } }).catch(() => {});
    return;
  }
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  try { new Notification("Claude 工作台", { body: text }); } catch {}
}

function connect() {
  if (es) es.close();
  es = new EventSource("/api/events?token=" + encodeURIComponent(token));
  es.onopen = () => {
    $("conn").textContent = ""; loadSessions(); if (current) openSession(current);
    if (!F.roots.length) api("/api/files").then(d => { F.roots = d.roots; F.office = d.office; renderMessages(); }).catch(() => {});
  };
  es.onerror = () => {
    $("conn").textContent = "重新连接中";
    api("/api/me").then(u => { me = { ...me, ...u }; }).catch(() => {});  // 登录失效时会弹出登录页
  };
  es.onmessage = e => {
    const ev = JSON.parse(e.data);
    if (ev.type === "session") return upsertSession(ev.session);
    if (ev.type === "session_deleted") {
      sessions = sessions.filter(s => s.id !== ev.session_id); renderSessions();
      if (current === ev.session_id) openSession(null);
      return;
    }
    const sid = ev.session_id || (ev.approval && ev.approval.session_id);
    if (ev.type === "approval" && sessions.some(s => s.id === sid)) notify("需要确认：" + ev.approval.summary);
    if (sid !== current) return;
    if (ev.type === "message") {
      if (!messages.some(m => m.id === ev.message.id)) messages.push(ev.message);
      if (ev.message.role === "user") { live = { text: "", tools: [] }; liveSteps = 0; seen.delete("live"); }
      else if (ev.message.role !== "event") { live = null; liveSteps = 0; }
    } else if (ev.type === "approval") { approvals.push(ev.approval); }
    else if (ev.type === "approval_done") { approvals = approvals.filter(a => a.id !== ev.id); }
    else if (ev.type === "delta_reset") { live = { text: "", tools: live ? live.tools : [] }; }
    else if (ev.type === "delta") { live = live || { text: "", tools: [] }; live.text += ev.text; }
    else if (ev.type === "tool") { live = live || { text: "", tools: [] }; live.tools.push({ name: ev.name, detail: ev.detail }); }
    renderMessages();
  };
}

async function ensureSession(title) {
  if (current) return current;
  const s = await post("/api/sessions", { title: (title || "新会话").slice(0, 20), work_dir: composer.workDir || null });
  current = s.id; currentSess = s; messages = []; approvals = []; links = [];
  composer.workDir = "";
  upsertSession(s);
  return s.id;
}

async function send() {
  const text = $("input").value.trim();
  if (!text || $("sendBtn").disabled || !$("sendBtn").offsetParent) return;
  $("sendBtn").disabled = true;
  if (!IS_DESKTOP && "Notification" in window && Notification.permission === "default") Notification.requestPermission();
  try {
    const sid = await ensureSession(text);
    await post(`/api/sessions/${sid}/messages`, { text, refs: composer.refs.map(r => r.path), skill: composer.skill });
    $("input").value = ""; autosize(); resetComposer();
  } catch (e) { toast(e.message, "err"); }
  finally { $("sendBtn").disabled = false; renderTop(); }
}

async function showSteps() {
  if (!current) return;
  $("steps").innerHTML = '<div class="empty-note">读取中…</div>';
  $("stepsNote").textContent = "";
  $("stepsBox").classList.add("show");
  try {
    const data = await api(`/api/sessions/${current}/transcript`);
    $("stepsNote").textContent = data.note || "";
    const label = { user: "用户", assistant: "CLAUDE", thinking: "思考", tool_use: "调用工具", tool_result: "工具返回" };
    $("steps").innerHTML = data.steps.map((s, i) => `
      <div class="step ${s.kind} ${s.error ? "err" : ""}" style="animation-delay:${Math.min(i, 20) * 15}ms">
        <div class="k">${label[s.kind] || s.kind}${s.name ? " · " + esc(s.name) : ""}</div>
        <pre>${esc(s.text)}</pre>
      </div>`).join("") || '<div class="empty-note">没有记录</div>';
    $("steps").scrollTop = $("steps").scrollHeight;
  } catch (e) { $("steps").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}

function autosize() { const t = $("input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 260) + "px"; }

// ---------- 输入框上方：引用、技能 ----------

function resetComposer() { composer.refs = []; composer.skill = ""; renderComposerChips(); }

function renderComposerChips() {
  $("composerChips").innerHTML =
    (composer.skill ? `<span class="chip skill">${icon("puzzle", 12)}<span>${esc(composer.skill)}</span><button data-unskill title="不用这个技能">${icon("x", 12)}</button></span>` : "") +
    composer.refs.map((r, i) => `<span class="chip ref" data-fpath="${esc(r.path)}" title="${esc(r.abs)}">${icon(r.dir ? "folder" : "clip", 12)}<span>${esc(r.name)}</span><button data-unref="${i}" title="移除">${icon("x", 12)}</button></span>`).join("");
  $("skillBtn").classList.toggle("on", !!composer.skill);
}

function addRef(entry) {
  if (composer.refs.some(r => r.abs === entry.abs)) return;
  composer.refs.push({ path: entry.abs, abs: entry.abs, name: entry.name, dir: !!entry.dir });
  renderComposerChips();
}

$("composerChips").onclick = e => {
  const un = e.target.closest("[data-unref]");
  if (un) { composer.refs.splice(+un.dataset.unref, 1); renderComposerChips(); return; }
  if (e.target.closest("[data-unskill]")) { composer.skill = ""; renderComposerChips(); return; }
  const chip = e.target.closest("[data-fpath]");
  if (chip) showFiles(chip.dataset.fpath);
};

async function loadSkills() {
  mySkills = await api("/api/skills/mine").catch(() => []);
}

function openSkillPop() {
  const pop = $("skillPop");
  pop.innerHTML = (mySkills.length ? mySkills.map(s => `<div class="it" data-skill="${esc(s.name)}"><span class="ii">${icon("puzzle", 15)}</span><div style="flex:1;min-width:0"><b>${esc(s.name)} <span class="vtag" style="font-size:10px">v${s.version || 1}</span></b><small>${esc(s.description)}</small></div><button class="iconbtn" data-detail="${esc(s.name)}" title="详情和版本记录">${icon("history", 15)}</button></div>`).join("")
    : '<div class="empty-note" style="padding:10px 12px">管理员还没给你分配技能。</div>')
    + `<div class="sep"></div><div class="it" data-skills-mgr><span class="ii">${icon("upload", 15)}</span><div><b>提交新技能</b><small>查看我的提交和审核状态</small></div></div>`;
  pop.hidden = false;
}
$("skillBtn").onclick = e => { e.stopPropagation(); if ($("skillPop").hidden) openSkillPop(); else $("skillPop").hidden = true; };
$("skillPop").onclick = e => {
  const it = e.target.closest(".it");
  if (!it) return;
  $("skillPop").hidden = true;
  const det = e.target.closest("[data-detail]");
  if (det) return openSkillDetail(det.dataset.detail);
  if (it.dataset.skillsMgr !== undefined) return openMySkills();
  composer.skill = it.dataset.skill; renderComposerChips(); $("input").focus();
};

// ---------- 本机文件：上传到台式机工作区，处理完同步回来 ----------

async function uploadFiles(files, name) {
  // files: [{file: File, rel: "子目录/a.txt"}]
  if (!files.length) return;
  const total = files.reduce((n, f) => n + f.file.size, 0);
  if (total > 2 * 1073741824) return toast("一次最多上传 2 GB", "err");
  const sid = await ensureSession(name);
  const link = await post(`/api/sessions/${sid}/links`, { name });
  let done = 0;
  for (const f of files) {
    if (++done % 5 === 1 || files.length < 6) flash(`正在上传 ${done}/${files.length}：${f.rel}`, 1400);
    await api(`/api/sessions/${sid}/links/${link.id}/file?path=${encodeURIComponent(f.rel)}`, { method: "PUT", body: f.file, raw: true });
  }
  await post(`/api/sessions/${sid}/links/${link.id}/snapshot`);
  links = await api(`/api/sessions/${sid}/links`);
  addRef({ abs: link.abs, name: link.name, dir: true });
  renderTop();
  toast(`已上传 ${files.length} 个文件（${fsize(total)}），已引用到对话里`, "ok");
}

$("localBtn").onclick = () => {
  if (!isMine()) return;
  const buttons = [
    { label: "选择文件", icon: "file", onClick: () => { $("uploadInput").click(); } },
    { label: "选择文件夹", icon: "folder", onClick: () => { $("uploadDirInput").click(); } },
  ];
  if (IS_DESKTOP) buttons.push({ label: "添加本机文件夹", icon: "sync", primary: true, onClick: addLocalFolder });
  dialog("把本机文件交给 Claude", `
    <p style="margin:0 0 10px">文件会上传到台式机上<b>你自己的工作区</b>（别人看不到），Claude 在那里用台式机的技能处理。</p>
    ${IS_DESKTOP ? `<div class="card" style="margin:0 0 10px"><h4>${icon("sync", 16)}添加本机文件夹</h4><div class="muted" style="font-size:13px">处理完可以一键把改动同步回这台电脑的原文件夹，覆盖前会自动备份。</div></div>` : ""}
    <p class="muted" style="margin:0;font-size:13px">也可以直接把文件拖进窗口。${IS_DESKTOP ? "" : "处理结果可以在“文件库 → 我的工作区”里下载。"}</p>`, buttons);
};

$("uploadInput").onchange = async e => {
  const files = [...e.target.files].map(f => ({ file: f, rel: f.name }));
  e.target.value = "";
  try { await uploadFiles(files, "上传的文件"); } catch (err) { toast(err.message, "err"); }
};
$("uploadDirInput").onchange = async e => {
  const list = [...e.target.files];
  e.target.value = "";
  if (!list.length) return;
  const top = list[0].webkitRelativePath.split("/")[0];
  const files = list.map(f => ({ file: f, rel: f.webkitRelativePath.split("/").slice(1).join("/") || f.name }));
  try { await uploadFiles(files, top); } catch (err) { toast(err.message, "err"); }
};

async function addLocalFolder() {
  try {
    const sid = await ensureSession("本机文件");
    flash("选好文件夹后开始上传，文件多的话要等一会儿…", 3000);
    const r = await invoke("upload_local_folder", { sessionId: sid, token });
    if (!r) return;  // 取消了
    links = await api(`/api/sessions/${sid}/links`);
    addRef({ abs: r.abs, name: r.name, dir: true });
    renderTop();
    toast(`已上传 ${r.files} 个文件（${fsize(r.bytes)}），已引用到对话里`, "ok");
  } catch (err) { toast(String(err), "err", 6000); }
}

// 拖文件进窗口
let dragDepth = 0;
const hasFiles = e => [...(e.dataTransfer?.types || [])].includes("Files");
window.addEventListener("dragenter", e => {
  if (!hasFiles(e) || !me) return;
  const toLibrary = $("filesBox").classList.contains("show") && F.mode === "browse";
  if (!toLibrary && !isMine()) return;
  dragDepth++;
  $("dropzone").querySelector(".dz div").textContent = toLibrary ? "松手，上传到这个文件夹" : "松手，交给 Claude";
  $("dropzone").querySelector(".dz p").textContent = toLibrary ? (F.writable ? F.abs : "这个位置只能看，不能上传") : "文件会上传到你自己的工作区";
  $("dropzone").classList.add("show");
});
window.addEventListener("dragleave", e => { if (!hasFiles(e)) return; if (--dragDepth <= 0) { dragDepth = 0; $("dropzone").classList.remove("show"); } });
window.addEventListener("dragover", e => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener("drop", async e => {
  if (!hasFiles(e)) return;
  e.preventDefault(); dragDepth = 0; $("dropzone").classList.remove("show");
  const toLibrary = $("filesBox").classList.contains("show") && F.mode === "browse";
  if (!me || (!toLibrary && !isMine())) return;
  if (toLibrary && !F.writable) return toast("这个位置只能看，不能上传", "err");
  const files = [];
  const walk = (entry, prefix) => new Promise(resolve => {
    if (entry.isFile) entry.file(f => { files.push({ file: f, rel: prefix + f.name }); resolve(); }, resolve);
    else if (entry.isDirectory) {
      const reader = entry.createReader(), all = [];
      const read = () => reader.readEntries(async batch => {
        if (!batch.length) { for (const c of all) await walk(c, prefix + entry.name + "/"); resolve(); }
        else { all.push(...batch); read(); }
      }, resolve);
      read();
    } else resolve();
  });
  const entries = [...e.dataTransfer.items].map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (entries.length) for (const en of entries) await walk(en, "");
  else files.push(...[...e.dataTransfer.files].map(f => ({ file: f, rel: f.name })));
  if (toLibrary) return uploadToLibrary(files);
  // 只拖了一个文件夹：去掉最外层，直接用文件夹名
  let name = "上传的文件";
  if (entries.length === 1 && entries[0].isDirectory) {
    name = entries[0].name;
    files.forEach(f => { f.rel = f.rel.slice(name.length + 1); });
  }
  try { await uploadFiles(files, name); } catch (err) { toast(err.message, "err"); }
});

function renderLinks() {
  const bar = $("linksBar");
  if (!links.length || !currentSess) { bar.hidden = true; return; }
  bar.hidden = false;
  bar.innerHTML = icon("laptop", 15) + "<span>本机文件</span>" + links.map(l => `<span class="chip ref" data-fpath="${esc(l.path)}" title="${esc(l.abs)}">${icon("folder", 12)}<span>${esc(l.name)}</span></span>
     ${isMine() ? `<button class="btn small" data-sync="${l.id}">${icon("sync", 13)}查看改动 / 同步回本机</button>` : ""}`).join(" ");
}

$("linksBar").onclick = e => {
  const chip = e.target.closest("[data-fpath]");
  if (chip) return showFiles(chip.dataset.fpath);
  const sync = e.target.closest("[data-sync]");
  if (sync) syncBack(sync.dataset.sync);
};

async function syncBack(lid) {
  const sid = current;
  const link = links.find(l => l.id === lid);
  let ch;
  try { ch = await api(`/api/sessions/${sid}/links/${lid}/changes`); } catch (e) { return toast(e.message, "err"); }
  const n = ch.added.length + ch.modified.length;
  const local = IS_DESKTOP ? await invoke("local_link_path", { linkId: lid }).catch(() => null) : null;
  const row = (it, tag, cls) => `<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--line)">
     <span class="status ${cls}">${tag}</span><a class="fpath" data-fp="${esc(it.path)}" style="flex:1">${esc(it.rel)}</a><span class="muted" style="font-size:12px">${fsize(it.size)}</span>
     ${local ? "" : `<a class="btn small" href="/api/files/raw?path=${encodeURIComponent(it.path)}&download=1&token=${encodeURIComponent(token)}" download>${icon("download", 13)}</a>`}</div>`;
  const body = n || ch.deleted.length ? `
    ${ch.added.map(i => row(i, "新增", "approved")).join("")}
    ${ch.modified.map(i => row(i, "修改", "pending")).join("")}
    ${ch.deleted.map(r => `<div class="muted" style="padding:5px 0;font-size:13px">Claude 删除了 ${esc(r)}（同步时不会删除你电脑上的文件）</div>`).join("")}
    <p class="muted" style="font-size:13px;margin:12px 0 0">${local ? `会写回到：<code>${esc(local)}</code><br>被覆盖的文件会先备份到那里的 “.projector备份” 文件夹。` :
      IS_DESKTOP ? "这个文件夹不是在这台电脑上添加的，只能逐个下载。" : "浏览器不能直接写回你的文件夹，请逐个下载；用桌面软件“添加本机文件夹”可以一键同步。"}</p>`
    : `<div style="text-align:center;padding:20px 0">${orb(48)}<p class="muted">Claude 还没有改动这个文件夹里的文件。</p></div>`;
  dialog(`“${link ? link.name : ""}” 的改动`, body, local && n ? [{
    label: `同步 ${n} 个文件回本机`, icon: "sync", primary: true, onClick: async () => {
      const r = await invoke("sync_back", { sessionId: sid, linkId: lid, token });
      toast(`已写回 ${r.written} 个文件` + (r.backups ? `，备份了 ${r.backups} 个原文件` : ""), "ok");
    },
  }] : [], true);
  $("dlgBody").onclick = e => { const a = e.target.closest("[data-fp]"); if (a) { closeDialog(); showFiles(a.dataset.fp); } };
}

// ---------- 文件浏览和预览 ----------
// 模式：browse 浏览；workdir 选工作文件夹

const F = { roots: [], office: false, path: "", abs: "", entries: [], sel: null, selEntry: null, searching: false, opened: false, mode: "browse", writable: false };
const EXT = {
  image: "png jpg jpeg gif webp bmp svg ico avif tif tiff heic",
  pdf: "pdf",
  video: "mp4 webm mov m4v ogv",
  audio: "mp3 wav ogg m4a flac aac opus",
  md: "md markdown",
  text: "txt log json xml yaml yml ini cfg conf toml py js ts jsx tsx css html htm vue bat cmd ps1 sh c cpp h hpp cs java kt go rs rb php sql lua r m srt properties env gitignore",
  docx: "docx",
  sheet: "xlsx xlsm xls csv ods",
  office: "doc ppt pptx rtf odt odp wps et dps pps ppsx",
};
const kindOf = name => {
  const ext = (name.split(".").pop() || "").toLowerCase();
  for (const k in EXT) if (EXT[k].split(" ").includes(ext)) return k;
  return "other";
};
// 文件类型色块：扩展名缩写 + 颜色
const KIND_COLOR = { image: "#c46ad6", pdf: "#d9443a", video: "#7a5cf0", audio: "#e0559b", md: "#5b6b7a", text: "#7d8a96", docx: "#2f6fd6", sheet: "#1f9a5a", office: "#e2742c", other: "#a39a8c" };
function fileBadge(e) {
  if (e.dir) return `<span class="fk" style="background:linear-gradient(135deg,#f3b45a,#e8892e)">${icon("folder", 15)}</span>`;
  const k = kindOf(e.name), ext = (e.name.includes(".") ? e.name.split(".").pop() : "").slice(0, 4).toUpperCase() || "FILE";
  return `<span class="fk" style="background:${KIND_COLOR[k]}">${esc(ext)}</span>`;
}
const fileUrl = (p, extra = "") => `/api/files/${extra || "raw"}?path=${encodeURIComponent(p)}&token=${encodeURIComponent(token)}`;

const libs = {};
function loadScript(src) {
  return libs[src] || (libs[src] = new Promise((ok, fail) => {
    const s = document.createElement("script"); s.src = src; s.onload = ok;
    s.onerror = () => { delete libs[src]; fail(new Error("预览组件加载失败，检查这台电脑能不能上网")); };
    document.head.appendChild(s);
  }));
}

function renderCrumbs(crumbs) {
  $("crumbs").innerHTML = crumbs.map(c => `<a data-go="${esc(c.path)}">${esc(c.label)}</a>`).join(" / ") + (F.searching ? " / 搜索结果" : "");
  const ws = F.path.startsWith("ws");
  $("areas").querySelectorAll("button").forEach(b => b.classList.toggle("on", (b.dataset.area === "ws:") === ws));
}

function renderList() {
  const list = F.entries;
  $("flist").innerHTML = list.length ? list.map((e, i) => `
    <div class="fi ${e.path === F.sel ? "sel" : ""}" data-i="${i}" title="${esc(e.abs)}" style="animation-delay:${Math.min(i, 25) * 12}ms">
      ${fileBadge(e)}
      <span class="n">${esc(e.name)}${F.searching ? `<span class="sub">${esc(e.path)}</span>` : ""}</span>
      <span class="s">${e.dir ? "" : fsize(e.size) + " · "}${fdate(e.mtime)}</span>
      ${F.writable && F.mode === "browse" && !F.searching ? `<span class="acts"><button class="iconbtn" data-ren="${i}" title="重命名">${icon("edit", 14)}</button><button class="iconbtn del" data-delf="${i}" title="删除（进回收站）">${icon("trash", 14)}</button></span>` : ""}
    </div>`).join("") : `<div class="empty-note" style="padding:24px;text-align:center">这里是空的${F.writable && F.mode === "browse" ? "<br>可以把文件拖进来上传" : ""}</div>`;
  const sel = $("flist").querySelector(".sel");
  if (sel) sel.scrollIntoView({ block: "nearest" });
}

function fileEmpty(text, ic = "file") {
  $("fcontent").innerHTML = `<div class="center"><div class="big">${icon(ic, 28)}</div>${text}</div>`;
}

async function openDir(path) {
  $("fq").value = ""; F.searching = false;
  $("flist").innerHTML = '<div class="empty-note" style="padding:14px">读取中…</div>';
  try {
    const d = await api("/api/files?path=" + encodeURIComponent(path));
    Object.assign(F, { roots: d.roots, office: d.office, path: d.path, abs: d.abs, entries: d.entries, writable: d.writable });
    renderCrumbs(d.crumbs);
    paintFileTools();
    if (F.mode === "workdir") $("pickText").textContent = "当前：" + d.abs;
    if (d.select) { const e = d.entries.find(x => x.path === d.select); if (e) return preview(e); }
    renderList();
  } catch (e) { $("flist").innerHTML = `<div class="err" style="padding:14px">${esc(e.message)}</div>`; }
}

let searchTimer;
$("fq").addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = $("fq").value.trim();
  if (!q) return openDir(F.path);
  searchTimer = setTimeout(async () => {
    $("flist").innerHTML = '<div class="empty-note" style="padding:14px">搜索中…</div>';
    try {
      const d = await api(`/api/files/search?path=${encodeURIComponent(F.path)}&q=${encodeURIComponent(q)}`);
      if ($("fq").value.trim() !== q) return;
      F.searching = true; F.entries = d.results; renderList();
      if (d.partial) $("flist").insertAdjacentHTML("beforeend", '<div class="empty-note" style="padding:10px">结果太多，只显示一部分，换个更具体的词试试</div>');
    } catch (e) { $("flist").innerHTML = `<div class="err" style="padding:14px">${esc(e.message)}</div>`; }
  }, 350);
});

async function preview(e) {
  if (e.dir) return openDir(e.path);
  F.sel = e.path; F.selEntry = e; renderList();
  $("fbody").classList.add("viewing");
  $("fname").textContent = e.name; $("fname").title = e.abs;
  $("fdown").hidden = false; $("fdown").href = fileUrl(e.path) + "&download=1"; $("fdown").setAttribute("download", e.name);
  $("fref").hidden = !isMine();
  $("flocal").hidden = !IS_DESKTOP;
  $("fren").hidden = $("fdel").hidden = !(F.writable && F.mode === "browse");
  const box = $("fcontent");
  const kind = kindOf(e.name);
  const url = fileUrl(e.path);
  // 预览不了时的提示：桌面软件里给“用本机程序打开”，浏览器里给“下载”
  const cannot = why => fileEmpty(`<b style="color:var(--ink)">${esc(e.name)}</b><br>${fsize(e.size)} · ${fdate(e.mtime)}<br><br>${why}<br>`
    + (IS_DESKTOP ? `可以用这台电脑上的软件打开。<br><button class="btn primary" data-local>${icon("external", 15)}用本机程序打开</button>`
                  : `可以下载后用本机软件打开。<br><a class="btn primary" href="${url}&download=1" download="${esc(e.name)}">${icon("download", 15)}下载</a>`), "file");
  fileEmpty("加载中…", "clock");
  try {
    if (kind === "image") box.innerHTML = `<img src="${url}" alt="">`;
    else if (kind === "pdf") box.innerHTML = `<iframe src="${url}"></iframe>`;
    else if (kind === "video") box.innerHTML = `<video src="${url}" controls></video>`;
    else if (kind === "audio") box.innerHTML = `<audio src="${url}" controls></audio>`;
    else if (kind === "md" || kind === "text") {
      const d = await api("/api/files/text?path=" + encodeURIComponent(e.path));
      if (F.sel !== e.path) return;
      const note = d.truncated ? '<div class="empty-note" style="padding:8px 18px">文件太大，只显示前 1 MB</div>' : "";
      box.innerHTML = note + (kind === "md" ? `<div class="mdv md">${md(d.text)}</div>` : `<pre>${esc(d.text)}</pre>`);
    } else if (kind === "docx") {
      if (e.size > 50 * 1048576) return cannot("文件太大，不适合在这里预览。");
      await loadScript("https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js");
      await loadScript("https://cdn.jsdelivr.net/npm/docx-preview@0.3.5/dist/docx-preview.min.js");
      const blob = await fetch(url).then(r => r.ok ? r.blob() : Promise.reject(new Error("读取失败")));
      if (F.sel !== e.path) return;
      box.innerHTML = "";
      await docx.renderAsync(blob, box, null, { inWrapper: true, ignoreLastRenderedPageBreak: true });
    } else if (kind === "sheet") {
      if (e.size > 30 * 1048576) return cannot("表格太大，不适合在这里预览。");
      await loadScript("https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js");
      const buf = await fetch(url).then(r => r.ok ? r.arrayBuffer() : Promise.reject(new Error("读取失败")));
      if (F.sel !== e.path) return;
      const wb = XLSX.read(buf, { type: "array", codepage: 936 });
      const show = name => {
        const html = XLSX.utils.sheet_to_html(wb.Sheets[name], { header: "", footer: "" });
        box.innerHTML = `<div class="sheet"><div class="tabs">${wb.SheetNames.map(n => `<button class="${n === name ? "on" : ""}" data-sheet="${esc(n)}">${esc(n)}</button>`).join("")}</div>`
          + `${window.DOMPurify ? DOMPurify.sanitize(html) : html}</div>`;
        box.querySelectorAll("[data-sheet]").forEach(b => b.onclick = () => show(b.dataset.sheet));
      };
      show(wb.SheetNames[0]);
    } else if (kind === "office") {
      if (!F.office) return cannot("这种格式需要台式机装了 LibreOffice 才能预览。");
      box.innerHTML = `<iframe src="${fileUrl(e.path, "pdf")}"></iframe>`;
    } else {
      cannot("这种格式不能在这里预览。");
    }
  } catch (err) { if (F.sel === e.path) cannot("预览失败：" + esc(err.message)); }
  // 图片、视频、音频格式浏览器不支持时（比如 HEIC、TIFF、部分视频编码）
  const media = box.querySelector("img, video, audio");
  if (media) media.onerror = () => { if (F.sel === e.path) cannot("这个文件的格式或编码这里放不了。"); };
}

function showFiles(path = null, mode = "browse") {
  F.mode = mode;
  $("pickbar").hidden = mode !== "workdir";
  $("ftools").hidden = mode !== "browse";
  $("frefDir").hidden = mode !== "browse" || !isMine();
  if (mode === "workdir") $("pickText").textContent = "选一个文件夹作为这个会话的工作文件夹";
  if (!F.sel) fileEmpty("选择左边的文件预览", "eye");
  $("filesBox").classList.add("show");
  $("side").classList.remove("open");
  if (path !== null) openDir(path);
  else if (!F.opened || mode === "workdir") openDir(F.path);
  F.opened = true;
}

// ---------- 文件库管理：上传、新建、改名、删除、回收站 ----------

function paintFileTools() {
  const w = F.writable;
  for (const id of ["fUpFile", "fUpDir", "fMkdir"]) $(id).disabled = !w;
  $("fRO").hidden = w;
  $("fRO").textContent = "这个位置只能看，不能修改";
}

async function mkdirHere() {
  const name = prompt("新文件夹的名字");
  if (!name || !name.trim()) return;
  try {
    const e = await post("/api/files/mkdir", { path: F.path, name: name.trim() });
    toast("已新建文件夹：" + e.name, "ok", 2000);
    await openDir(F.path);
  } catch (err) { toast(err.message, "err"); }
}

async function renameEntry(e) {
  const name = prompt("改成什么名字？", e.name);
  if (!name || name.trim() === e.name) return;
  try {
    const r = await post("/api/files/rename", { path: e.path, name: name.trim() });
    toast("已改名为 " + r.name, "ok", 2000);
    if (F.sel === e.path) { F.sel = r.path; }
    await openDir(F.path);
  } catch (err) { toast(err.message, "err"); }
}

async function deleteEntries(list) {
  const names = list.map(e => (e.dir ? "📁 " : "") + e.name).join("\n");
  if (!confirm(`把下面的${list.length > 1 ? " " + list.length + " 项" : ""}移到回收站？30 天内可以恢复。\n\n${names}`)) return;
  try {
    const r = await post("/api/files/delete", { paths: list.map(e => e.path) });
    if (r.failed.length) toast("有 " + r.failed.length + " 项没删掉：" + r.failed[0].error, "err", 6000);
    if (r.deleted.length) toast(`已移到回收站：${r.deleted.map(d => d.name).join("、")}`, "ok");
    if (list.some(e => e.path === F.sel)) { F.sel = null; fileEmpty("选择左边的文件预览", "eye"); $("fbody").classList.remove("viewing"); }
    await openDir(F.path);
  } catch (err) { toast(err.message, "err"); }
}

// 同名文件怎么办：覆盖 / 保留两份 / 跳过，可以勾“后面都这样”
function askConflict(name) {
  return new Promise(resolve => {
    let done = false;
    const pick = mode => () => { done = true; resolve({ mode, all: $("dlgBody").querySelector("#cfAll")?.checked }); };
    dialog("有同名文件", `<p style="margin:0 0 10px">这个文件夹里已经有 <b>${esc(name)}</b> 了。</p>
      <p class="muted" style="font-size:13px;margin:0 0 10px">选“覆盖”的话，旧文件会放进回收站，30 天内可以找回。</p>
      <label class="switch"><input type="checkbox" id="cfAll"><span class="sw"></span>后面再遇到同名文件也这样处理</label>`,
      [{ label: "跳过", onClick: pick("skip") }, { label: "保留两份", onClick: pick("rename") }, { label: "覆盖", primary: true, onClick: pick("overwrite") }]);
    const timer = setInterval(() => {
      if (!$("dlgBox").classList.contains("show")) { clearInterval(timer); if (!done) resolve({ mode: "skip", all: true }); }
    }, 200);
  });
}

function progress(done, total, text) {
  $("fprog").hidden = total === 0;
  $("fprogBar").style.width = total ? Math.round(done / total * 100) + "%" : "0";
  $("fprogText").textContent = text || "";
}

// 把本机文件上传到文件库当前打开的文件夹；files: [{file, rel}]
async function uploadToLibrary(files) {
  if (!files.length || !F.writable) return;
  const dir = F.path, total = files.reduce((n, f) => n + f.file.size, 0);
  let sent = 0, ok = 0, skipped = 0, policy = null;
  for (let i = 0; i < files.length; i++) {
    const f = files[i];
    progress(sent, total, `正在上传 ${i + 1}/${files.length}：${f.rel}`);
    let mode = policy || "ask";
    for (;;) {
      const r = await fetch(`/api/files/upload?dir=${encodeURIComponent(dir)}&name=${encodeURIComponent(f.rel)}&on_conflict=${mode}`,
        { method: "PUT", body: f.file, headers: { Authorization: "Bearer " + token } });
      if (r.ok) { ok++; break; }
      const data = await r.json().catch(() => ({}));
      let info = null;
      try { info = JSON.parse(data.detail); } catch {}
      if (r.status === 409 && info && info.exists) {
        progress(sent, total, `等你决定：${info.name}`);
        const c = await askConflict(info.name);
        if (c.all) policy = c.mode;
        if (c.mode === "skip") { skipped++; break; }
        mode = c.mode;
        continue;
      }
      toast(`${f.rel} 上传失败：${data.detail || r.status}`, "err", 6000);
      break;
    }
    sent += f.file.size;
  }
  progress(0, 0);
  toast(`已上传 ${ok} 个文件${skipped ? `，跳过 ${skipped} 个` : ""}`, "ok");
  if (F.path === dir) openDir(dir);
}

async function openTrash() {
  let d;
  try { d = await api("/api/trash"); } catch (e) { return toast(e.message, "err"); }
  const admin = me.role === "admin";
  const body = dialog("回收站", d.items.length ? `
    <p class="muted" style="margin:0 0 6px;font-size:13px">删除的文件会在这里保留 ${d.days} 天。${admin ? "你是管理员，能看到所有人删除的文件。" : "这里只有你自己删除的文件。"}</p>
    ${d.items.map((t, i) => `<div class="trash-row" style="animation-delay:${Math.min(i, 20) * 25}ms">
      ${fileBadge({ name: t.name, dir: !!t.is_dir })}
      <div class="tn"><b>${esc(t.name)}</b><small title="${esc(t.orig_path)}">原来在 ${esc(shortPath(t.orig_path))} · ${t.is_dir ? "文件夹" : fsize(t.size)}</small>
        <small>${admin ? esc(t.who || "") + " · " : ""}${ftime(t.deleted_at)}${t.reason && t.reason !== "删除" ? " · " + esc(t.reason) : ""} · 还剩 ${t.days_left} 天</small></div>
      <button class="btn small" data-restore="${t.id}">${icon("history", 13)}恢复</button>
      ${admin ? `<button class="iconbtn" data-purge="${t.id}" title="彻底删除">${icon("x", 15)}</button>` : ""}
    </div>`).join("")}` : `<div style="text-align:center;padding:24px 0">${orb(48)}<p class="muted">回收站是空的。</p></div>`, [], true);
  body.onclick = async e => {
    const rs = e.target.closest("[data-restore]"), pg = e.target.closest("[data-purge]");
    try {
      if (rs) {
        const r = await post(`/api/trash/${rs.dataset.restore}/restore`);
        toast(r.renamed ? `已恢复（原位置有同名文件，改名为 ${baseName(r.restored)}）` : "已恢复到原来的位置", "ok", 4000);
        if ($("filesBox").classList.contains("show")) openDir(F.path);
        openTrash();
      } else if (pg) {
        if (!confirm("彻底删除后就找不回来了，确定吗？")) return;
        await api(`/api/trash/${pg.dataset.purge}`, { method: "DELETE" });
        openTrash();
      }
    } catch (err) { toast(err.message, "err"); }
  };
}

$("fUpFile").onclick = () => $("fUpIn").click();
$("fUpDir").onclick = () => $("fUpDirIn").click();
$("fMkdir").onclick = mkdirHere;
$("fTrash").onclick = openTrash;
$("fren").onclick = () => F.selEntry && renameEntry(F.selEntry);
$("fdel").onclick = () => F.selEntry && deleteEntries([F.selEntry]);
$("fUpIn").onchange = e => { const fs = [...e.target.files].map(f => ({ file: f, rel: f.name })); e.target.value = ""; uploadToLibrary(fs); };
$("fUpDirIn").onchange = e => {
  const fs = [...e.target.files].map(f => ({ file: f, rel: f.webkitRelativePath || f.name }));  // 保留最外层文件夹名
  e.target.value = ""; uploadToLibrary(fs);
};

// 把回复里提到的文件库、工作区路径变成可点的链接
function linkifyPaths(container) {
  if (!F.roots.length) return;
  const parts = F.roots.map(root => {
    const sep = root.includes("\\") ? "\\\\" : "/";
    return root.replace(/[\\/]$/, "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + sep;
  });
  const tail = `[^\\s<>"'\`|*?，。；：、！？（）()【】「」]*[^\\s<>"'\`|*?，。；：、！？（）()【】「」.,:;]`;
  const re = new RegExp(`(?:${parts.join("|")})${tail}`, "gi");
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
    acceptNode: n => n.parentElement.closest("a, textarea, .ticket, .chip, .tl") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  });
  const nodes = [];
  while (walker.nextNode()) if (walker.currentNode.nodeValue.match(re)) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const frag = document.createDocumentFragment();
    let last = 0;
    node.nodeValue.replace(re, (m, idx) => {
      frag.append(node.nodeValue.slice(last, idx));
      // 显示成“文件名小标签”：完整路径太长，放在悬停提示里
      const a = document.createElement("a");
      const name = baseName(m), isDir = !/\.[A-Za-z0-9]{1,6}$/.test(name);
      a.className = "fchip"; a.dataset.fpath = m; a.title = m + "\n点击预览";
      a.innerHTML = (isDir ? icon("folder", 13) : `<i style="background:${KIND_COLOR[kindOf(name)]}"></i>`) + "<span></span>";
      a.querySelector("span").textContent = name;
      frag.append(a); last = idx + m.length;
    });
    frag.append(node.nodeValue.slice(last));
    node.replaceWith(frag);
  }
}

async function openLocal() {
  const btns = document.querySelectorAll("#flocal, [data-local]");
  btns.forEach(b => { b.disabled = true; });
  try {
    await invoke("open_remote_file", { path: F.sel, token });
    toast("已用本机程序打开。这是一份副本，修改后不会保存回台式机。", "ok", 5000);
  } catch (err) { toast(String(err), "err", 6000); }
  finally { btns.forEach(b => { b.disabled = false; }); }
}

$("areas").onclick = e => { const b = e.target.closest("[data-area]"); if (b) openDir(b.dataset.area); };
$("crumbs").onclick = e => { if (e.target.dataset.go !== undefined) openDir(e.target.dataset.go); };
$("flist").onclick = e => {
  const ren = e.target.closest("[data-ren]"), del = e.target.closest("[data-delf]");
  if (ren) { e.stopPropagation(); return renameEntry(F.entries[+ren.dataset.ren]); }
  if (del) { e.stopPropagation(); return deleteEntries([F.entries[+del.dataset.delf]]); }
  const row = e.target.closest(".fi");
  if (!row) return;
  const en = F.entries[+row.dataset.i];
  if (F.mode === "workdir" && !en.dir) return;
  preview(en);
};
$("fback").onclick = () => $("fbody").classList.remove("viewing");
$("flocal").onclick = openLocal;
$("fcontent").addEventListener("click", e => { if (e.target.closest("[data-local]")) openLocal(); });
$("fref").onclick = () => { addRef(F.selEntry); toast("已引用：" + F.selEntry.name, "ok", 2000); };
$("frefDir").onclick = () => {
  addRef({ abs: F.abs, name: baseName(F.abs), dir: true });
  $("filesBox").classList.remove("show"); $("input").focus();
};
$("pickOk").onclick = async () => {
  try {
    if (current) await post("/api/sessions/" + current, { work_dir: F.abs }, "PATCH");
    else composer.workDir = F.abs;
    $("filesBox").classList.remove("show");
    renderTop();
    toast("工作文件夹：" + baseName(F.abs), "ok");
  } catch (e) { toast(e.message, "err"); }
};
$("messages").addEventListener("click", e => {
  const a = e.target.closest("[data-fpath]");
  if (a) showFiles(a.dataset.fpath);
});

// ---------- 技能：我的技能、技能详情和版本记录、提交新版本 ----------

// 通用文件查看器（技能审核、版本文件都用）：左边文件列表，右边内容；changes 用来标出新增、修改、删除
function fileViewer(title, files, buttons = [], extraHtml = "", changes = null) {
  const badge = p => !changes ? "" : changes.added.includes(p) ? '<span class="cbadge add">新增</span>' : changes.modified.includes(p) ? '<span class="cbadge mod">修改</span>' : "";
  const removed = changes && changes.removed.length ? `<div class="muted" style="font-size:12.5px;margin:-4px 0 10px">删除了：${changes.removed.map(esc).join("、")}</div>` : "";
  const body = dialog(title, `${extraHtml}${removed}<div class="filev"><div class="list">${files.map((f, i) =>
    `<div data-i="${i}">${badge(f.path)}${/\.(py|sh|bat|cmd|ps1|js|ts|exe|dll)$/i.test(f.path) ? '<span class="status pending">脚本</span> ' : ""}${esc(f.path)} <span class="muted" style="font-size:11.5px">${fsize(f.size)}</span></div>`).join("")}</div>
    <pre id="fvPre">选左边的文件查看内容</pre></div>`, buttons, true);
  const pick = i => {
    const f = files[i];
    body.querySelectorAll(".filev .list div").forEach(d => d.classList.toggle("on", +d.dataset.i === i));
    body.querySelector("#fvPre").textContent = f.text == null ? `（二进制文件，${fsize(f.size)}，不能显示）` : f.text + (f.truncated ? "\n……（太长，已截断）" : "");
  };
  body.querySelector(".filev .list").onclick = e => { const d = e.target.closest("[data-i]"); if (d) pick(+d.dataset.i); };
  const changed = changes ? files.findIndex(f => changes.modified.includes(f.path) || changes.added.includes(f.path)) : -1;
  const first = changed >= 0 ? changed : files.findIndex(f => f.path === "SKILL.md");
  if (files.length) pick(first >= 0 ? first : 0);
  return body;
}

// 上传技能文件（zip 或文件夹）并提交审核；changelog 是更新说明
async function submitSkill(upload, changelog) {
  const sub = await post("/api/skills/submissions");
  await upload(sub.id);
  return post(`/api/skills/submissions/${sub.id}/submit`, { changelog });
}

function skillUploadHtml(prefix, withLog = true) {
  return `${withLog ? `<input class="field" id="${prefix}Log" placeholder="更新说明：这次改了什么（更新已有技能时必填）" style="margin-bottom:8px">` : ""}
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn small" id="${prefix}Zip">${icon("upload", 14)}上传 zip 压缩包</button>
      <button class="btn small" id="${prefix}Dir">${icon("folder", 14)}选择技能文件夹</button>
    </div>
    <input type="file" id="${prefix}ZipIn" accept=".zip" hidden><input type="file" id="${prefix}DirIn" webkitdirectory hidden>
    <div class="err" id="${prefix}Err" style="margin-top:6px"></div>`;
}

function bindSkillUpload(body, prefix, done, needLog = false) {
  const err = body.querySelector(`#${prefix}Err`);
  const go = async upload => {
    const log = (body.querySelector(`#${prefix}Log`)?.value || "").trim();
    if (needLog && !log) { err.textContent = "请先写更新说明"; body.querySelector(`#${prefix}Log`).focus(); return; }
    err.textContent = "正在提交…";
    try { const r = await submitSkill(upload, log); done(r); } catch (e) { err.textContent = e.message; }
  };
  body.querySelector(`#${prefix}Zip`).onclick = () => body.querySelector(`#${prefix}ZipIn`).click();
  body.querySelector(`#${prefix}Dir`).onclick = () => body.querySelector(`#${prefix}DirIn`).click();
  body.querySelector(`#${prefix}ZipIn`).onchange = e => {
    const f = e.target.files[0]; e.target.value = ""; if (!f) return;
    go(id => api(`/api/skills/submissions/${id}/zip`, { method: "PUT", body: f, raw: true }));
  };
  body.querySelector(`#${prefix}DirIn`).onchange = e => {
    const files = [...e.target.files]; e.target.value = ""; if (!files.length) return;
    go(async id => {
      for (const f of files) {
        const rel = f.webkitRelativePath.split("/").slice(1).join("/") || f.name;
        await api(`/api/skills/submissions/${id}/file?path=${encodeURIComponent(rel)}`, { method: "PUT", body: f, raw: true });
      }
    });
  };
}

async function openMySkills() {
  const subs = await api("/api/skills/submissions").catch(() => []);
  const st = { pending: "待审核", approved: "已入库", rejected: "未通过" };
  const mine = subs.filter(s => s.submitted_by === me.id);
  const body = dialog("技能", `
    <div style="font-weight:600;margin-bottom:8px">我能用的技能 <span class="muted" style="font-weight:400;font-size:12.5px">点开看版本记录、修改</span></div>
    ${mySkills.length ? mySkills.map((s, i) => `<div class="card" data-skill="${esc(s.name)}" style="padding:10px 14px;cursor:pointer;animation-delay:${i * 30}ms"><h4>${icon("puzzle", 15)}${esc(s.name)} <span class="vtag">v${s.version || 1}</span></h4><div class="muted" style="font-size:13px">${esc(s.description)}</div></div>`).join("") : '<p class="empty-note">还没有，管理员分配后会出现在这里。</p>'}
    <div style="font-weight:600;margin:18px 0 6px">提交新技能</div>
    <p class="muted" style="margin:0 0 10px;font-size:13px">技能是一个文件夹，里面必须有 SKILL.md（开头写 name 和 description）。也可以直接在对话里说“帮我做一个××技能”，让 Claude 写好提交。提交后由管理员审核。</p>
    ${skillUploadHtml("sk")}
    <div style="font-weight:600;margin:12px 0 6px">我的提交</div>
    ${mine.map(s => `<div class="card" style="padding:10px 14px"><h4>${esc(s.name)} <span class="status ${s.status}">${st[s.status] || s.status}</span></h4>
      ${s.changelog ? `<div style="font-size:13px">${esc(s.changelog)}</div>` : ""}
      <div class="muted" style="font-size:12.5px">${ftime(s.created)}${s.note ? " · 管理员备注：" + esc(s.note) : ""}</div></div>`).join("") || '<p class="empty-note">还没有提交过。</p>'}
  `, [], true);
  body.querySelectorAll(".card[data-skill]").forEach(c => c.onclick = () => openSkillDetail(c.dataset.skill));
  bindSkillUpload(body, "sk", r => { toast(`已提交技能 ${r.name}，等管理员审核`, "ok"); openMySkills(); });
}

// 技能详情：说明、版本记录（谁改的、改了什么）、在对话里修改、上传新版本；管理员还能回滚
async function openSkillDetail(name) {
  let d;
  try { d = await api("/api/skills/" + encodeURIComponent(name)); } catch (e) { return toast(e.message, "err"); }
  const sk = d.skill, isAdmin = me.role === "admin";
  const body = dialog("技能详情", `
    <div class="sk-head"><span class="big">${icon("puzzle", 26)}</span><div><h3>${esc(sk.name)} <span class="vtag">v${sk.version}</span></h3><p>${esc(sk.description)}</p></div></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px">
      <button class="btn small primary" id="sdChat">${icon("chat", 14)}在对话里修改</button>
      <button class="btn small" id="sdUse">${icon("sparkle", 14)}用它开始一个任务</button>
      <button class="btn small" id="sdUp">${icon("upload", 14)}上传新版本</button>
    </div>
    <div id="sdUpBox" hidden class="card" style="margin-bottom:14px">${skillUploadHtml("sdu")}<div class="muted" style="font-size:12.5px">SKILL.md 里的 name 要保持 <code>${esc(sk.name)}</code>，否则会变成一个新技能。</div></div>
    ${d.pending.length ? `<div class="card warnc" style="padding:10px 14px">${icon("clock", 14)} 有 ${d.pending.length} 个新版本在等管理员审核：${d.pending.map(p => esc(p.submitter) + "「" + esc(p.changelog || "没写说明") + "」").join("；")}</div>` : ""}
    <div style="font-weight:600;margin-top:6px">${icon("history", 15)} 版本记录</div>
    <ol class="vtl">${d.versions.map((v, i) => `<li class="${v.version === sk.version ? "cur" : ""}" style="animation-delay:${i * 40}ms">
      <span class="vn">v${v.version}</span>
      <div class="vb">
        <div class="vm">${avatarEl(v.submitter_avatar, 18)}${esc(v.submitter || "—")} · ${ftime(v.created)}${v.approver ? ` · ${esc(v.approver)} 审核` : ""}${v.version === sk.version ? ' · <span class="tag admin">当前</span>' : ""}</div>
        <div class="vc">${esc(v.changelog || "没写更新说明")}</div>
        <div class="va"><button class="btn small ghost" data-vfiles="${v.version}">${icon("eye", 13)}查看文件</button>
          ${isAdmin && v.version !== sk.version ? `<button class="btn small ghost" data-rollback="${v.version}">${icon("history", 13)}回滚到这个版本</button>` : ""}</div>
      </div></li>`).join("")}</ol>
  `, [], true);
  body.querySelector("#sdChat").onclick = () => {
    closeDialog(); $("dlgBox").classList.remove("show");
    resetComposer(); openSession(null);
    $("input").value = `我想修改技能「${sk.name}」：`; autosize();
    setTimeout(() => { const t = $("input"); t.focus(); t.setSelectionRange(t.value.length, t.value.length); }, 50);
    toast("说清楚要改什么，Claude 会取出技能、改好后提交新版本给管理员审核", "info", 5000);
  };
  body.querySelector("#sdUse").onclick = () => { closeDialog(); composer.skill = sk.name; renderComposerChips(); $("input").focus(); };
  body.querySelector("#sdUp").onclick = () => { body.querySelector("#sdUpBox").hidden = !body.querySelector("#sdUpBox").hidden; };
  bindSkillUpload(body, "sdu", r => { toast(`已提交 ${r.name} 的新版本，等管理员审核`, "ok"); openSkillDetail(name); }, true);
  body.querySelectorAll("[data-vfiles]").forEach(b => b.onclick = async () => {
    const v = +b.dataset.vfiles;
    const f = await api(`/api/skills/${encodeURIComponent(name)}/versions/${v}/files`);
    fileViewer(`${name} v${v} 的文件`, f.files, [{ label: "返回", onClick: () => { setTimeout(() => openSkillDetail(name), 0); } }],
      f.changes ? '<div class="muted" style="font-size:12.5px;margin-bottom:8px">标出了相对上一个版本的改动</div>' : "", f.changes);
  });
  body.querySelectorAll("[data-rollback]").forEach(b => b.onclick = async () => {
    const v = +b.dataset.rollback;
    if (!confirm(`回滚到 v${v}？会生成一个内容和 v${v} 一样的新版本，所有能用这个技能的人马上生效。`)) return;
    try { const r = await post(`/api/admin/library/${encodeURIComponent(name)}/rollback`, { version: v }); toast(`已回滚，现在是 v${r.version}`, "ok"); openSkillDetail(name); loadSkills(); }
    catch (e) { toast(e.message, "err"); }
  });
}

// ---------- 在手机上打开：二维码 ----------

async function showMobile() {
  let lan;
  try { lan = await api("/api/lan"); } catch (e) { return toast(e.message, "err"); }
  const urls = lan.urls.length ? lan.urls : [location.origin];
  try { await loadScript("https://cdnjs.cloudflare.com/ajax/libs/qrcode-generator/1.4.4/qrcode.min.js"); }
  catch (e) { return toast(e.message, "err"); }
  const qrSvg = url => {
    const qr = qrcode(0, "H"); qr.addData(url); qr.make();
    const n = qr.getModuleCount(), c = Math.floor(n / 2), hole = Math.ceil(n * 0.13);
    let rects = "";
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
      if (!qr.isDark(y, x) || (Math.abs(x - c) <= hole && Math.abs(y - c) <= hole)) continue;  // 中间留给橙光球
      const finder = (x < 7 && y < 7) || (x >= n - 7 && y < 7) || (x < 7 && y >= n - 7);
      rects += `<rect x="${x + .06}" y="${y + .06}" width=".88" height=".88" rx="${finder ? .2 : .42}" fill="${finder ? "#c24f1c" : "#201b15"}"/>`;
    }
    return `<svg viewBox="0 0 ${n} ${n}" shape-rendering="geometricPrecision">${rects}</svg>`;
  };
  const render = url => {
    $("dlgBody").querySelector(".qrcard").innerHTML = qrSvg(url) + orb(46);
    $("dlgBody").querySelector("#qrUrl").textContent = url;
  };
  const body = dialog("在手机上打开", `<div class="qrwrap">
    <div class="qrcard"></div>
    <div class="qrurl"><span id="qrUrl"></span><button class="iconbtn" id="qrCopy" data-tip="复制">${icon("copy", 16)}</button></div>
    ${urls.length > 1 ? `<div class="seg" id="qrPick" style="margin-top:10px">${urls.map((u, i) => `<button class="${i ? "" : "on"}" data-u="${esc(u)}">${esc(u.replace(/^https?:\/\//, ""))}</button>`).join("")}</div>` : ""}
    <p class="muted" style="font-size:13px;margin:14px 0 0">手机连上和这台台式机<b>同一个 Wi-Fi</b>，用相机或微信扫一扫。</p>
    <div class="qrsteps">
      <div><b>iPhone</b>用 Safari 打开 → 底部分享按钮 → “添加到主屏幕”，之后像 App 一样全屏打开</div>
      <div><b>安卓</b>用 Chrome 打开 → 右上角 ⋮ → “添加到主屏幕”，桌面上会出现橙光球图标</div>
    </div></div>`);
  render(urls[0]);
  body.querySelector("#qrCopy").onclick = () => navigator.clipboard?.writeText($("qrUrl").textContent).then(() => toast("已复制", "ok", 1500));
  body.querySelector("#qrPick")?.addEventListener("click", e => {
    const b = e.target.closest("[data-u]"); if (!b) return;
    body.querySelectorAll("#qrPick button").forEach(x => x.classList.toggle("on", x === b));
    render(b.dataset.u);
  });
}

// ---------- 命令面板（⌘K / Ctrl+K） ----------

let palItems = [], palSel = 0;
function paletteActions() {
  const acts = [
    ["plus", "新会话", "", () => { resetComposer(); openSession(null); }],
    ["folder", "打开文件库", "", () => showFiles()],
    ["clip", "引用文件到对话", "@", () => showFiles()],
    ["laptop", "把本机文件交给 Claude", "", () => $("localBtn").click()],
    ["puzzle", "选择技能", "", () => openSkillPop()],
    ["upload", "提交新技能", "", () => loadSkills().then(openMySkills)],
    ["user", "更换头像", "", pickAvatar],
    ["phone", "在手机上打开", "", () => showMobile()],
    ["trash", "回收站", "", () => openTrash()],
    [store.get("theme") === "dark" ? "sun" : "moon", "切换主题", "", () => { $("userMenu").hidden = true; const o = ["auto", "light", "dark"], c = store.get("theme") || "auto", n = o[(o.indexOf(c) + 1) % 3]; store.set("theme", n); applyTheme(n); toast("主题：" + THEMES[n][0]); }],
    ["key", "修改密码", "", changePassword],
  ];
  if (me.role === "admin") acts.push(["shield", "打开管理后台", "", () => window.openAdmin()]);
  if (current) acts.splice(1, 0, ["steps", "查看完整过程", "", showSteps]);
  return acts;
}
function renderPalette() {
  const q = $("palQ").value.trim().toLowerCase();
  const acts = paletteActions().filter(a => !q || a[1].toLowerCase().includes(q));
  const ss = sessions.filter(s => !q || s.title.toLowerCase().includes(q)).slice(0, q ? 20 : 6);
  palItems = [...acts.map(a => ({ kind: "act", ic: a[0], label: a[1], hint: a[2], run: a[3] })),
    ...ss.map(s => ({ kind: "sess", ic: s.source === "voice" ? "mic" : "chat", label: s.title, hint: rel(s.updated), run: () => openSession(s.id) }))];
  palSel = Math.min(palSel, Math.max(0, palItems.length - 1));
  let html = "", lastKind = "";
  palItems.forEach((it, i) => {
    if (it.kind !== lastKind) { html += `<div class="pgrp">${it.kind === "act" ? "操作" : "会话"}</div>`; lastKind = it.kind; }
    html += `<div class="pi ${i === palSel ? "on" : ""}" data-i="${i}">${icon(it.ic, 16)}<span>${esc(it.label)}</span><span class="ph">${esc(it.hint)}</span></div>`;
  });
  $("palList").innerHTML = html || '<div class="empty-note" style="padding:16px;text-align:center">没有匹配的结果</div>';
  $("palList").querySelector(".pi.on")?.scrollIntoView({ block: "nearest" });
}
function openPalette() {
  if (!me) return;
  $("palQ").value = ""; palSel = 0; renderPalette();
  $("palette").classList.add("show");
  setTimeout(() => $("palQ").focus(), 30);
}
function runPal(i) {
  const it = palItems[i];
  if (!it) return;
  $("palette").classList.remove("show");
  setTimeout(it.run, 60);
}
$("palQ").addEventListener("input", () => { palSel = 0; renderPalette(); });
$("palQ").addEventListener("keydown", e => {
  if (e.key === "ArrowDown") { e.preventDefault(); palSel = Math.min(palSel + 1, palItems.length - 1); renderPalette(); }
  if (e.key === "ArrowUp") { e.preventDefault(); palSel = Math.max(palSel - 1, 0); renderPalette(); }
  if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); runPal(palSel); }
});
$("palList").onclick = e => { const p = e.target.closest(".pi"); if (p) runPal(+p.dataset.i); };
$("palList").onmousemove = e => {
  const p = e.target.closest(".pi");
  if (p && +p.dataset.i !== palSel) { palSel = +p.dataset.i; $("palList").querySelectorAll(".pi").forEach(x => x.classList.toggle("on", x === p)); }
};

document.addEventListener("keydown", e => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    if ($("palette").classList.contains("show")) $("palette").classList.remove("show"); else openPalette();
    return;
  }
  if (e.key !== "Escape") return;
  for (const id of ["palette", "dlgBox", "filesBox", "stepsBox", "adminBox"]) {
    if ($(id).classList.contains("show")) { $(id).classList.remove("show"); return; }
  }
  $("skillPop").hidden = true; $("userMenu").hidden = true;
});

// ---------- 事件绑定 ----------

$("newBtn").onclick = () => { resetComposer(); openSession(null); };
$("menuBtn").onclick = e => { e.stopPropagation(); $("side").classList.toggle("open"); };
document.addEventListener("click", e => { if ($("side").classList.contains("open") && !e.target.closest("#side")) $("side").classList.remove("open"); });
$("sendBtn").onclick = send;
$("stopBtn").onclick = () => current && post(`/api/sessions/${current}/stop`).then(() => toast("已发送停止指令"));
$("stepsBtn").onclick = showSteps;
$("filesBtn").onclick = () => showFiles();
$("paletteBtn").onclick = openPalette;
$("refBtn").onclick = () => showFiles();
$("wdBtn").onclick = () => showFiles(currentSess ? currentSess.work_dir : (composer.workDir || ""), "workdir");
$("bypass").onchange = async () => {
  const on = $("bypass").checked;
  if (on && !confirm("打开后，本会话里 Claude 的所有操作（包括删除文件、运行命令）都不再询问，直接执行。确定吗？")) {
    $("bypass").checked = false; return;
  }
  try { await post("/api/sessions/" + current, { bypass: on }, "PATCH"); }
  catch (e) { toast(e.message, "err"); renderTop(); }
};
$("input").addEventListener("input", e => {
  autosize();
  if (e.data === "@") {  // 打 @ 打开文件选择
    const t = $("input"); t.value = t.value.slice(0, t.selectionStart - 1) + t.value.slice(t.selectionStart);
    showFiles();
  }
});
$("input").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
});
$("sessions").onclick = async e => {
  const del = e.target.closest("[data-del]");
  if (del) {
    e.stopPropagation();
    if (confirm("删除这个会话、聊天记录和上传到工作区的文件？")) {
      const row = del.closest(".sess");
      row.animate([{ opacity: 1 }, { opacity: 0, transform: "translateX(-20px)" }], { duration: 220, fill: "forwards" });
      api("/api/sessions/" + del.dataset.del, { method: "DELETE" }).catch(err => toast(err.message, "err"));
    }
    return;
  }
  const row = e.target.closest(".sess");
  if (row) openSession(row.dataset.id);
};
$("title").ondblclick = async () => {
  if (!current || !isMine()) return;
  const t = prompt("会话名称", $("title").textContent);
  if (t) post("/api/sessions/" + current, { title: t }, "PATCH");
};

// ---------- 启动 ----------

if (token) api("/api/me").then(enter).catch(() => {});
else showLogin();
