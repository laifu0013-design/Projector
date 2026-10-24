// Claude 工作台网页端：登录、会话、引用文件、技能、本机文件、文件预览。管理后台在 admin.js。
"use strict";
const $ = id => document.getElementById(id);
const store = {
  get(k) { try { return localStorage.getItem(k) || ""; } catch { return ""; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
  del(k) { try { localStorage.removeItem(k); } catch {} },
};
const IS_DESKTOP = !!(window.__TAURI__ && window.__TAURI__.core);
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

// ---------- 通用 ----------

function api(path, opts = {}) {
  const headers = { Authorization: "Bearer " + token, ...(opts.headers || {}) };
  if (!(opts.body instanceof Blob || opts.body instanceof ArrayBuffer || opts.raw)) headers["Content-Type"] = "application/json";
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
const baseName = p => p.split(/[\\/]/).filter(Boolean).pop() || p;

function flash(text, ms = 4000) {
  const d = document.createElement("div");
  d.textContent = text;
  d.style.cssText = "position:fixed;left:50%;bottom:32px;transform:translateX(-50%);background:var(--text);color:var(--bg);padding:8px 16px;border-radius:8px;font-size:14px;z-index:40;max-width:90vw";
  document.body.appendChild(d); setTimeout(() => d.remove(), ms);
}

// 通用小对话框：dialog(标题, 内容 html, [{label, primary, danger, onClick}])；onClick 返回 false 则不关闭
function dialog(title, html, buttons = [], wide = false) {
  $("dlgTitle").textContent = title;
  $("dlgBody").innerHTML = html;
  $("dlg").classList.toggle("narrow", !wide);
  $("dlgFoot").innerHTML = "";
  $("dlgFoot").hidden = !buttons.length;
  for (const b of buttons) {
    const el = document.createElement("button");
    el.className = "btn" + (b.primary ? " primary" : "") + (b.danger ? " danger" : "");
    el.textContent = b.label;
    el.onclick = async () => {
      el.disabled = true;
      try { if ((await b.onClick?.()) !== false) closeDialog(); }
      catch (e) { const err = $("dlgBody").querySelector(".err"); if (err) err.textContent = e.message; else alert(e.message); }
      finally { el.disabled = false; }
    };
    $("dlgFoot").appendChild(el);
  }
  $("dlgBox").classList.add("show");
  setTimeout(() => $("dlgBody").querySelector("input, textarea")?.focus(), 50);
  return $("dlgBody");
}
function closeDialog() { $("dlgBox").classList.remove("show"); }

document.querySelectorAll("[data-close]").forEach(b => b.onclick = () => b.closest(".overlay").classList.remove("show"));
document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  for (const id of ["dlgBox", "filesBox", "stepsBox", "adminBox"]) {
    if ($(id).classList.contains("show")) { $(id).classList.remove("show"); return; }
  }
  $("skillPop").hidden = true;
});

// ---------- 登录 ----------

let setupMode = false;
async function showLogin() {
  if (es) { es.close(); es = null; }
  $("login").classList.add("show");
  $("lgErr").textContent = "";
  try {
    const s = await fetch("/api/setup").then(r => r.json());
    setupMode = s.needed;
    if (s.needed && !s.local) {
      $("lgErr").textContent = "还没有管理员。请在台式机本机打开 http://localhost:" + location.port + " 创建管理员";
    }
  } catch { setupMode = false; }
  $("loginTitle").textContent = setupMode ? "创建管理员" : "登录 Claude 工作台";
  $("setupHint").hidden = !setupMode;
  $("lgName").hidden = $("lgPwd2").hidden = !setupMode;
  $("lgBtn").textContent = setupMode ? "创建并进入" : "登录";
  $("lgNote").hidden = setupMode;
  $("lgPwd").autocomplete = setupMode ? "new-password" : "current-password";
  $("lgUser").value = store.get("username");
  ($("lgUser").value ? $("lgPwd") : $("lgUser")).focus();
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
  } catch (err) { $("lgErr").textContent = err.message; }
  finally { $("lgBtn").disabled = false; }
};

function enter(user) {
  me = user;
  $("me").textContent = me.display_name;
  $("roleTag").innerHTML = me.role === "admin" ? '<span class="warn">管理员</span>' : "";
  $("adminLink").hidden = me.role !== "admin";
  sessions = []; current = null; currentSess = null; messages = []; live = null; approvals = []; links = [];
  resetComposer();
  connect();
  loadSkills();
  renderMessages();
}

$("logoutLink").onclick = async () => {
  if (!confirm("退出登录？")) return;
  try { await post("/api/logout"); } catch {}
  token = ""; store.del("token"); me = null;
  showLogin();
};

$("pwdLink").onclick = () => dialog("修改密码", `<div class="form">
    <input type="password" id="pwOld" placeholder="原密码" autocomplete="current-password">
    <input type="password" id="pwNew" placeholder="新密码（至少 6 位）" autocomplete="new-password">
    <input type="password" id="pwNew2" placeholder="再输一次新密码" autocomplete="new-password">
    <div class="err"></div></div>`, [{
  label: "保存", primary: true, onClick: async () => {
    if ($("pwNew").value !== $("pwNew2").value) throw new Error("两次新密码不一样");
    await post("/api/me/password", { old: $("pwOld").value, new: $("pwNew").value });
    flash("密码已修改");
  },
}]);

// ---------- 会话列表和消息 ----------

function toolChips(tools) {
  if (!tools || !tools.length) return "";
  return '<div class="tools">' + tools.map(t =>
    `<span class="chip" title="${esc(t.detail)}">${esc(t.name)}${t.detail ? " · " + esc(t.detail) : ""}</span>`).join("") + "</div>";
}

function approvalDetail(a) {
  const i = a.input || {};
  if (a.tool === "Bash") return esc(i.command) + (i.description ? `\n\n# ${esc(i.description)}` : "");
  if (a.tool === "Edit") return `<span class="del">- ${esc(i.old_string)}</span>\n<span class="add">+ ${esc(i.new_string)}</span>`;
  if (a.tool === "Write") { const c = i.content || ""; return esc(c.slice(0, 2000)) + (c.length > 2000 ? "\n……" : ""); }
  return esc(JSON.stringify(i, null, 1));
}

function sessRow(s, showOwner = false) {
  return `<div class="sess ${s.id === current ? "active" : ""}" data-id="${s.id}">
      ${s.running ? '<span class="dot"></span>' : ""}
      <span class="t">${esc(s.title)}</span>
      ${showOwner ? `<span class="meta" style="margin:0">${ftime(s.updated)}</span>` : ""}
      ${s.pending ? '<span class="warn">待确认</span>' : ""}
      ${showOwner ? "" : `<button class="x" data-del="${s.id}" title="删除">✕</button>`}
    </div>`;
}

function renderSessions() {
  $("sessions").innerHTML = sessions.map(s => sessRow(s)).join("") ||
    '<div class="meta" style="padding:8px 10px">还没有会话，直接在右边输入开始</div>';
}

const isMine = () => !currentSess || currentSess.user_id === me.id;

function renderTop() {
  const s = currentSess;
  const mine = isMine();
  $("title").textContent = s ? s.title : "新会话";
  const running = !!(s && s.running);
  $("stopBtn").hidden = !running;
  $("sendBtn").disabled = running;
  $("bypassWrap").hidden = !me.can_bypass || (s && s.source === "voice") || !mine;
  $("bypass").checked = !!(s && s.bypass);
  $("bypass").disabled = !s;
  $("bypassWrap").classList.toggle("on", !!(s && s.bypass));
  $("stepsBtn").disabled = !s;
  const wd = s ? s.work_dir : (composer.workDir || "");
  $("wdBtn").textContent = "📂 " + (wd ? baseName(wd) : "工作文件夹：默认");
  $("wdBtn").title = (wd || "默认工作目录") + "\n工作文件夹：Claude 在这个文件夹里干活。只能在会话开始前选";
  $("wdBtn").disabled = !mine || !!(s && s.claude_session);
  $("composer").hidden = !mine;
  $("otherBanner").hidden = mine;
  if (!mine) $("otherBanner").innerHTML = `👁 这是 <b>${esc(s.owner)}</b> 的会话，只能查看${s.source === "voice" ? "（板子语音）" : ""}。待确认的操作你可以代为处理。`;
  renderLinks();
}

function chipsHtml(meta) {
  const refs = (meta && meta.refs) || [];
  const skill = meta && meta.skill;
  if (!refs.length && !skill) return "";
  return '<div class="chips">' + (skill ? `<span class="chip skill">🧩 ${esc(skill)}</span>` : "") +
    refs.map(r => `<span class="chip ref" data-fpath="${esc(r)}" title="${esc(r)}">📎 ${esc(baseName(r))}</span>`).join("") + "</div>";
}

function renderMessages() {
  if (!me) return;
  renderTop();
  if (!messages.length && !live && !approvals.length) {
    $("list").innerHTML = `<div class="empty">Claude 在台式机上干活，文件库是 D 盘。<br>
      📎 引用文件库里的文件，💻 把本机文件交给 Claude，🧩 选用技能。<br>
      删除、移动文件和运行命令前会在这里请你确认。</div>`;
    return;
  }
  let html = messages.map(m => {
    const meta = m.meta || {};
    if (m.role === "user") {
      const tag = meta.bypass ? " · 完全放行" : "";
      return `<div class="msg user">${chipsHtml(meta)}<div class="body">${esc(m.content)}</div><div class="meta">${esc(meta.author || "")}${tag}</div></div>`;
    }
    if (m.role === "event") return `<div class="msg event">${esc(m.content)}</div>`;
    const info = [meta.seconds != null ? meta.seconds + " 秒" : "", meta.cost ? "$" + meta.cost.toFixed(4) : ""].filter(Boolean).join(" · ");
    const body = m.role === "error" ? esc(m.content) : md(m.content);
    return `<div class="msg ${m.role}">${toolChips(meta.tools)}<div class="body">${body}</div>${info ? `<div class="meta">${info}</div>` : ""}</div>`;
  }).join("");
  if (live) html += `<div class="msg assistant">${toolChips(live.tools)}<div class="body ${approvals.length ? "" : "typing"}">${md(live.text)}</div></div>`;
  html += approvals.map(a => `
    <div class="approval">
      <strong>⚠ 需要确认：</strong>${esc(a.summary)}
      <pre>${approvalDetail(a)}</pre>
      <div class="acts">
        <button class="btn" data-deny="${a.id}">拒绝</button>
        <button class="btn primary" data-allow="${a.id}">同意</button>
      </div>
    </div>`).join("");
  const box = $("messages");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  $("list").innerHTML = html;
  linkifyPaths($("list"));
  if (atBottom || approvals.length) box.scrollTop = box.scrollHeight;
}

async function loadSessions() {
  sessions = await api("/api/sessions");
  renderSessions();
}

async function openSession(id) {
  current = id;
  $("side").classList.remove("open");
  if (!id) {
    currentSess = null; messages = []; live = null; approvals = []; links = [];
    renderSessions(); renderMessages(); $("input").focus(); return;
  }
  const data = await api("/api/sessions/" + id);
  if (current !== id) return;
  currentSess = data.session; messages = data.messages; live = data.live; approvals = data.approvals;
  links = await api(`/api/sessions/${id}/links`).catch(() => []);
  renderSessions(); renderMessages();
  $("messages").scrollTop = $("messages").scrollHeight;
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
  if (s.id === current) { currentSess = s; if (!s.running) live = null; renderMessages(); }
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
    $("conn").textContent = "连接中断，正在重连…";
    api("/api/me").then(u => { me = { ...me, ...u }; }).catch(() => {});  // 登录失效时会弹出登录框
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
      if (ev.message.role === "user") live = { text: "", tools: [] };
      else if (ev.message.role !== "event") live = null;
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
  if (!text || $("sendBtn").disabled) return;
  $("sendBtn").disabled = true;
  if (!IS_DESKTOP && "Notification" in window && Notification.permission === "default") Notification.requestPermission();
  try {
    const sid = await ensureSession(text);
    await post(`/api/sessions/${sid}/messages`, { text, refs: composer.refs.map(r => r.path), skill: composer.skill });
    $("input").value = ""; autosize(); resetComposer();
  } catch (e) { alert(e.message); }
  finally { renderTop(); }
}

async function showSteps() {
  if (!current) return;
  $("steps").innerHTML = '<div class="meta">读取中…</div>';
  $("stepsNote").textContent = "";
  $("stepsBox").classList.add("show");
  try {
    const data = await api(`/api/sessions/${current}/transcript`);
    $("stepsNote").textContent = data.note || "";
    const label = { user: "用户", assistant: "Claude", thinking: "思考", tool_use: "调用工具", tool_result: "工具返回" };
    $("steps").innerHTML = data.steps.map(s => `
      <div class="step ${s.kind} ${s.error ? "err" : ""}">
        <div class="k">${label[s.kind] || s.kind}${s.name ? " · " + esc(s.name) : ""}</div>
        <pre>${esc(s.text)}</pre>
      </div>`).join("") || '<div class="meta">没有记录</div>';
    $("steps").scrollTop = $("steps").scrollHeight;
  } catch (e) { $("steps").innerHTML = `<div class="meta">${esc(e.message)}</div>`; }
}

function autosize() { const t = $("input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 240) + "px"; }

// ---------- 输入框上方：引用、技能 ----------

function resetComposer() { composer.refs = []; composer.skill = ""; renderComposerChips(); }

function renderComposerChips() {
  $("composerChips").innerHTML =
    (composer.skill ? `<span class="chip skill">🧩 ${esc(composer.skill)}<button data-unskill title="不用这个技能">✕</button></span>` : "") +
    composer.refs.map((r, i) => `<span class="chip ref" data-fpath="${esc(r.path)}" title="${esc(r.abs)}">${r.dir ? "📁" : "📎"} ${esc(r.name)}<button data-unref="${i}" title="移除">✕</button></span>`).join("");
}

function addRef(entry) {
  if (composer.refs.some(r => r.abs === entry.abs)) return;
  composer.refs.push({ path: entry.abs, abs: entry.abs, name: entry.name, dir: !!entry.dir });
  renderComposerChips();
}

$("composerChips").onclick = e => {
  if (e.target.dataset.unref !== undefined) { composer.refs.splice(+e.target.dataset.unref, 1); renderComposerChips(); return; }
  if (e.target.dataset.unskill !== undefined) { composer.skill = ""; renderComposerChips(); return; }
  const chip = e.target.closest("[data-fpath]");
  if (chip) showFiles(chip.dataset.fpath);
};

async function loadSkills() {
  mySkills = await api("/api/skills/mine").catch(() => []);
}

$("skillBtn").onclick = e => {
  e.stopPropagation();
  const pop = $("skillPop");
  if (!pop.hidden) { pop.hidden = true; return; }
  pop.innerHTML = (mySkills.length ? mySkills.map(s => `<div class="it" data-skill="${esc(s.name)}"><b>${esc(s.name)}</b><div class="d">${esc(s.description)}</div></div>`).join("")
    : '<div class="meta" style="padding:8px">管理员还没给你分配技能。</div>')
    + '<div class="it" data-skills-mgr style="border-top:1px solid var(--border);margin-top:4px">📤 提交新技能 / 查看我的提交</div>';
  pop.hidden = false;
};
$("skillPop").onclick = e => {
  const it = e.target.closest(".it");
  if (!it) return;
  $("skillPop").hidden = true;
  if (it.dataset.skillsMgr !== undefined) return openMySkills();
  composer.skill = it.dataset.skill; renderComposerChips(); $("input").focus();
};
document.addEventListener("click", e => { if (!e.target.closest(".anchor")) $("skillPop").hidden = true; });

// ---------- 本机文件：上传到台式机工作区，处理完同步回来 ----------

async function uploadFiles(files, name) {
  // files: [{file: File, rel: "子目录/a.txt"}]
  if (!files.length) return;
  const total = files.reduce((n, f) => n + f.file.size, 0);
  if (total > 2 * 1073741824) return alert("一次最多上传 2 GB");
  const sid = await ensureSession(name);
  const link = await post(`/api/sessions/${sid}/links`, { name });
  let done = 0;
  for (const f of files) {
    flash(`正在上传 ${++done}/${files.length}：${f.rel}`, 1500);
    await api(`/api/sessions/${sid}/links/${link.id}/file?path=${encodeURIComponent(f.rel)}`, { method: "PUT", body: f.file, raw: true });
  }
  await post(`/api/sessions/${sid}/links/${link.id}/snapshot`);
  links = await api(`/api/sessions/${sid}/links`);
  addRef({ abs: link.abs, name: link.name, dir: true });
  renderTop();
  flash(`已上传 ${files.length} 个文件（${fsize(total)}），已引用到对话里`);
}

$("localBtn").onclick = () => {
  if (!isMine()) return;
  const buttons = [
    { label: "选择文件…", onClick: () => { $("uploadInput").click(); } },
    { label: "选择文件夹…", onClick: () => { $("uploadDirInput").click(); } },
  ];
  if (IS_DESKTOP) buttons.unshift({ label: "添加本机文件夹（可同步回来）", primary: true, onClick: addLocalFolder });
  dialog("把本机文件交给 Claude", `<p style="margin:0 0 8px">文件会上传到台式机上你自己的工作区（别人看不到），Claude 在那里处理。</p>
    ${IS_DESKTOP ? "<p style='margin:0 0 8px'><b>添加本机文件夹</b>：处理完可以一键把改动同步回这台电脑的原文件夹（覆盖前会自动备份）。</p>" : ""}
    <p class="meta" style="margin:0">也可以直接把文件拖进窗口。${IS_DESKTOP ? "" : "处理结果可以在“📁 文件 → 我的工作区”里下载。"}</p>`, buttons);
};

$("uploadInput").onchange = async e => {
  const files = [...e.target.files].map(f => ({ file: f, rel: f.name }));
  e.target.value = "";
  try { await uploadFiles(files, "上传的文件"); } catch (err) { alert(err.message); }
};
$("uploadDirInput").onchange = async e => {
  const list = [...e.target.files];
  e.target.value = "";
  if (!list.length) return;
  const top = list[0].webkitRelativePath.split("/")[0];
  const files = list.map(f => ({ file: f, rel: f.webkitRelativePath.split("/").slice(1).join("/") || f.name }));
  try { await uploadFiles(files, top); } catch (err) { alert(err.message); }
};

async function addLocalFolder() {
  try {
    const sid = await ensureSession("本机文件");
    flash("选择文件夹后会开始上传，文件多的话要等一会儿…", 3000);
    const r = await invoke("upload_local_folder", { sessionId: sid, token });
    if (!r) return;  // 取消了
    links = await api(`/api/sessions/${sid}/links`);
    addRef({ abs: r.abs, name: r.name, dir: true });
    renderTop();
    flash(`已上传 ${r.files} 个文件（${fsize(r.bytes)}），已引用到对话里`);
  } catch (err) { alert(String(err)); }
}

// 拖文件进窗口
let dragDepth = 0;
const hasFiles = e => [...(e.dataTransfer?.types || [])].includes("Files");
window.addEventListener("dragenter", e => { if (!hasFiles(e) || !me || !isMine()) return; dragDepth++; $("dropzone").classList.add("show"); });
window.addEventListener("dragleave", e => { if (!hasFiles(e)) return; if (--dragDepth <= 0) { dragDepth = 0; $("dropzone").classList.remove("show"); } });
window.addEventListener("dragover", e => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener("drop", async e => {
  if (!hasFiles(e)) return;
  e.preventDefault(); dragDepth = 0; $("dropzone").classList.remove("show");
  if (!me || !isMine()) return;
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
  const name = entries.length === 1 && entries[0].isDirectory ? entries[0].name : "上传的文件";
  try { await uploadFiles(files, name); } catch (err) { alert(err.message); }
});

function renderLinks() {
  const bar = $("linksBar");
  if (!links.length || !currentSess) { bar.hidden = true; return; }
  bar.hidden = false;
  bar.innerHTML = "💻 本机文件：" + links.map(l => `<span class="chip ref" data-fpath="${esc(l.path)}" title="${esc(l.abs)}">📁 ${esc(l.name)}</span>
     ${isMine() ? `<button class="btn small" data-sync="${l.id}">查看改动 / 同步回本机</button>` : ""}`).join(" ");
}

$("linksBar").onclick = e => {
  const chip = e.target.closest("[data-fpath]");
  if (chip) return showFiles(chip.dataset.fpath);
  if (e.target.dataset.sync) syncBack(e.target.dataset.sync);
};

async function syncBack(lid) {
  const sid = current;
  const link = links.find(l => l.id === lid);
  let ch;
  try { ch = await api(`/api/sessions/${sid}/links/${lid}/changes`); } catch (e) { return alert(e.message); }
  const n = ch.added.length + ch.modified.length;
  const local = IS_DESKTOP ? await invoke("local_link_path", { linkId: lid }).catch(() => null) : null;
  const row = (it, tag) => `<div>${tag} <a class="fpath" data-fp="${esc(it.path)}">${esc(it.rel)}</a> <span class="meta">${fsize(it.size)}</span>
     ${local ? "" : ` <a class="btn small" href="/api/files/raw?path=${encodeURIComponent(it.path)}&download=1&token=${encodeURIComponent(token)}" download>下载</a>`}</div>`;
  const body = n || ch.deleted.length ? `
    ${ch.added.map(i => row(i, "🆕 新增")).join("")}
    ${ch.modified.map(i => row(i, "✏️ 修改")).join("")}
    ${ch.deleted.map(r => `<div class="meta">🗑 Claude 删除了 ${esc(r)}（同步时不会删除你电脑上的文件）</div>`).join("")}
    <p class="meta">${local ? `会写回到：${esc(local)}<br>被覆盖的文件会先备份到那里的 “.projector备份” 文件夹。` :
      IS_DESKTOP ? "这个文件夹不是在这台电脑上添加的，只能逐个下载。" : "浏览器里不能直接写回你的文件夹，请逐个下载；用桌面软件“添加本机文件夹”可以一键同步。"}</p>`
    : "<p>Claude 还没有改动这个文件夹里的文件。</p>";
  dialog(`“${link ? link.name : ""}” 的改动`, body, local && n ? [{
    label: `同步 ${n} 个文件回本机`, primary: true, onClick: async () => {
      const r = await invoke("sync_back", { sessionId: sid, linkId: lid, token });
      flash(`已写回 ${r.written} 个文件` + (r.backups ? `，备份了 ${r.backups} 个原文件` : ""));
    },
  }] : [], true);
  $("dlgBody").onclick = e => { const a = e.target.closest("[data-fp]"); if (a) { closeDialog(); showFiles(a.dataset.fp); } };
}

// ---------- 文件浏览和预览 ----------
// 模式：browse 浏览；workdir 选工作文件夹

const F = { roots: [], office: false, path: "", abs: "", entries: [], sel: null, selEntry: null, searching: false, opened: false, mode: "browse" };
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
const ICON = { dir: "📁", image: "🖼", pdf: "📕", video: "🎬", audio: "🎵", md: "📝", text: "📄", docx: "📘", sheet: "📊", office: "📙", other: "📦" };
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
  $("crumbs").innerHTML = crumbs.map(c => `<a data-go="${esc(c.path)}">${esc(c.label)}</a>`).join(" › ") + (F.searching ? " › 搜索结果" : "");
  const ws = F.path.startsWith("ws");
  $("areas").querySelectorAll("button").forEach(b => b.classList.toggle("on", (b.dataset.area === "ws:") === ws));
}

function renderList() {
  const list = F.entries;
  $("flist").innerHTML = list.length ? list.map((e, i) => `
    <div class="fi ${e.path === F.sel ? "sel" : ""}" data-i="${i}" title="${esc(e.abs)}">
      <span>${e.dir ? ICON.dir : ICON[kindOf(e.name)]}</span>
      <span class="n">${esc(e.name)}${F.searching ? `<span class="sub">${esc(e.path)}</span>` : ""}</span>
      <span class="s">${e.dir ? "" : fsize(e.size) + " · "}${fdate(e.mtime)}</span>
    </div>`).join("") : '<div class="meta" style="padding:12px">这里是空的</div>';
  const sel = $("flist").querySelector(".sel");
  if (sel) sel.scrollIntoView({ block: "nearest" });
}

async function openDir(path) {
  $("fq").value = ""; F.searching = false;
  $("flist").innerHTML = '<div class="meta" style="padding:12px">读取中…</div>';
  try {
    const d = await api("/api/files?path=" + encodeURIComponent(path));
    Object.assign(F, { roots: d.roots, office: d.office, path: d.path, abs: d.abs, entries: d.entries });
    renderCrumbs(d.crumbs);
    if (F.mode === "workdir") $("pickText").textContent = "当前：" + d.abs;
    if (d.select) { const e = d.entries.find(x => x.path === d.select); if (e) return preview(e); }
    renderList();
  } catch (e) { $("flist").innerHTML = `<div class="meta" style="padding:12px">${esc(e.message)}</div>`; }
}

let searchTimer;
$("fq").addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = $("fq").value.trim();
  if (!q) return openDir(F.path);
  searchTimer = setTimeout(async () => {
    $("flist").innerHTML = '<div class="meta" style="padding:12px">搜索中…</div>';
    try {
      const d = await api(`/api/files/search?path=${encodeURIComponent(F.path)}&q=${encodeURIComponent(q)}`);
      if ($("fq").value.trim() !== q) return;
      F.searching = true; F.entries = d.results; renderList();
      $("crumbs").insertAdjacentText("beforeend", "");
      if (d.partial) $("flist").insertAdjacentHTML("beforeend", '<div class="meta" style="padding:8px">结果太多，只显示一部分，换个更具体的词试试</div>');
    } catch (e) { $("flist").innerHTML = `<div class="meta" style="padding:12px">${esc(e.message)}</div>`; }
  }, 400);
});

async function preview(e) {
  if (e.dir) return openDir(e.path);
  F.sel = e.path; F.selEntry = e; renderList();
  $("fbody").classList.add("viewing");
  $("fname").textContent = e.name; $("fname").title = e.abs;
  $("fdown").hidden = false; $("fdown").href = fileUrl(e.path) + "&download=1"; $("fdown").setAttribute("download", e.name);
  $("fref").hidden = !isMine();
  $("flocal").hidden = !IS_DESKTOP;
  const box = $("fcontent");
  const kind = kindOf(e.name);
  const url = fileUrl(e.path);
  const msg = t => box.innerHTML = `<div class="center">${t}</div>`;
  // 预览不了时的提示：桌面软件里给“用本机程序打开”，浏览器里给“下载”
  const cannot = why => msg(`${esc(e.name)}<br>${fsize(e.size)} · ${fdate(e.mtime)}<br><br>${why}<br>`
    + (IS_DESKTOP ? `可以用这台电脑上的软件打开。<br><button class="btn primary" data-local>用本机程序打开</button>`
                  : `可以下载后用本机软件打开。<br><a class="btn primary" href="${url}&download=1" download="${esc(e.name)}">下载</a>`));
  msg("加载中…");
  try {
    if (kind === "image") box.innerHTML = `<img src="${url}" alt="">`;
    else if (kind === "pdf") box.innerHTML = `<iframe src="${url}"></iframe>`;
    else if (kind === "video") box.innerHTML = `<video src="${url}" controls></video>`;
    else if (kind === "audio") box.innerHTML = `<audio src="${url}" controls></audio>`;
    else if (kind === "md" || kind === "text") {
      const d = await api("/api/files/text?path=" + encodeURIComponent(e.path));
      if (F.sel !== e.path) return;
      const note = d.truncated ? '<div class="meta" style="padding:8px 16px">文件太大，只显示前 1 MB</div>' : "";
      box.innerHTML = note + (kind === "md" ? `<div class="mdv msg">${md(d.text)}</div>` : `<pre>${esc(d.text)}</pre>`);
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
        box.innerHTML = `<div class="tabs">${wb.SheetNames.map(n => `<button class="${n === name ? "on" : ""}" data-sheet="${esc(n)}">${esc(n)}</button>`).join("")}</div>`
          + `<div class="sheet">${window.DOMPurify ? DOMPurify.sanitize(html) : html}</div>`;
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
  $("frefDir").hidden = mode !== "browse" || !isMine();
  if (mode === "workdir") $("pickText").textContent = "选一个文件夹作为这个会话的工作文件夹";
  $("filesBox").classList.add("show");
  $("side").classList.remove("open");
  if (path !== null) openDir(path);
  else if (!F.opened || mode === "workdir") openDir(F.path);
  F.opened = true;
}

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
    acceptNode: n => n.parentElement.closest("a, textarea, .approval, .chip") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  });
  const nodes = [];
  while (walker.nextNode()) if (walker.currentNode.nodeValue.match(re)) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const frag = document.createDocumentFragment();
    let last = 0;
    node.nodeValue.replace(re, (m, idx) => {
      frag.append(node.nodeValue.slice(last, idx));
      const a = document.createElement("a");
      a.className = "fpath"; a.textContent = m; a.dataset.fpath = m; a.title = "点击预览";
      frag.append(a); last = idx + m.length;
    });
    frag.append(node.nodeValue.slice(last));
    node.replaceWith(frag);
  }
}

async function openLocal() {
  const btns = document.querySelectorAll("#flocal, [data-local]");
  btns.forEach(b => { b.disabled = true; b.dataset.label = b.textContent; b.textContent = "正在下载…"; });
  try {
    await invoke("open_remote_file", { path: F.sel, token });
    flash("已用本机程序打开。这是一份副本，修改后不会保存回台式机。");
  } catch (err) { alert(String(err)); }
  finally { btns.forEach(b => { b.disabled = false; b.textContent = b.dataset.label; }); }
}

$("areas").onclick = e => { if (e.target.dataset.area !== undefined) openDir(e.target.dataset.area); };
$("crumbs").onclick = e => { if (e.target.dataset.go !== undefined) openDir(e.target.dataset.go); };
$("flist").onclick = e => {
  const row = e.target.closest(".fi");
  if (!row) return;
  const en = F.entries[+row.dataset.i];
  if (F.mode === "workdir" && !en.dir) return;
  preview(en);
};
$("fback").onclick = () => $("fbody").classList.remove("viewing");
$("flocal").onclick = openLocal;
$("fcontent").addEventListener("click", e => { if (e.target.closest("[data-local]")) openLocal(); });
$("fref").onclick = () => { addRef(F.selEntry); flash("已引用：" + F.selEntry.name, 2000); };
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
  } catch (e) { alert(e.message); }
};
$("messages").addEventListener("click", e => {
  const a = e.target.closest("[data-fpath]");
  if (a) showFiles(a.dataset.fpath);
});

// ---------- 我的技能：提交和查看 ----------

async function openMySkills() {
  const subs = await api("/api/skills/submissions").catch(() => []);
  const st = { pending: "待审核", approved: "已入库", rejected: "未通过" };
  const body = dialog("技能", `
    <h4 style="margin:0 0 6px">我能用的技能</h4>
    ${mySkills.length ? mySkills.map(s => `<div class="card"><b>${esc(s.name)}</b><div class="meta">${esc(s.description)}</div></div>`).join("") : '<p class="meta">还没有，管理员分配后会出现在这里。</p>'}
    <h4 style="margin:14px 0 6px">提交新技能</h4>
    <p class="meta" style="margin:0 0 8px">技能是一个文件夹，里面必须有 SKILL.md（开头写 name 和 description）。提交后由管理员审核，通过后入库。</p>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn small" id="skZip">上传 zip 压缩包…</button>
      <button class="btn small" id="skDir">选择技能文件夹…</button>
    </div>
    <input type="file" id="skZipIn" accept=".zip" hidden><input type="file" id="skDirIn" webkitdirectory hidden>
    <div class="err" id="skErr"></div>
    <h4 style="margin:14px 0 6px">我的提交</h4>
    ${subs.filter(s => s.submitted_by === me.id).map(s => `<div class="card"><b>${esc(s.name)}</b> <span class="status ${s.status}">${st[s.status] || s.status}</span>
      <div class="meta">${ftime(s.created)}${s.note ? " · 管理员备注：" + esc(s.note) : ""}</div></div>`).join("") || '<p class="meta">还没有提交过。</p>'}
  `, [], true);
  body.querySelector("#skZip").onclick = () => body.querySelector("#skZipIn").click();
  body.querySelector("#skDir").onclick = () => body.querySelector("#skDirIn").click();
  const submit = async upload => {
    body.querySelector("#skErr").textContent = "正在提交…";
    try {
      const sub = await post("/api/skills/submissions");
      await upload(sub.id);
      const r = await post(`/api/skills/submissions/${sub.id}/submit`);
      flash(`已提交技能 ${r.name}，等管理员审核`);
      openMySkills();
    } catch (e) { body.querySelector("#skErr").textContent = e.message; }
  };
  body.querySelector("#skZipIn").onchange = e => {
    const f = e.target.files[0]; if (!f) return;
    submit(id => api(`/api/skills/submissions/${id}/zip`, { method: "PUT", body: f, raw: true }));
  };
  body.querySelector("#skDirIn").onchange = e => {
    const files = [...e.target.files]; if (!files.length) return;
    submit(async id => {
      for (const f of files) {
        const rel = f.webkitRelativePath.split("/").slice(1).join("/") || f.name;
        await api(`/api/skills/submissions/${id}/file?path=${encodeURIComponent(rel)}`, { method: "PUT", body: f, raw: true });
      }
    });
  };
}
$("skillsLink").onclick = () => { loadSkills().then(openMySkills); };

// ---------- 事件绑定 ----------

$("newBtn").onclick = () => { resetComposer(); openSession(null); };
$("menuBtn").onclick = () => $("side").classList.toggle("open");
$("sendBtn").onclick = send;
$("stopBtn").onclick = () => current && post(`/api/sessions/${current}/stop`);
$("stepsBtn").onclick = showSteps;
$("filesBtn").onclick = () => showFiles();
$("refBtn").onclick = () => showFiles();
$("wdBtn").onclick = () => showFiles(currentSess ? currentSess.work_dir : (composer.workDir || ""), "workdir");
$("bypass").onchange = async () => {
  const on = $("bypass").checked;
  if (on && !confirm("打开后，本会话里 Claude 的所有操作（包括删除文件、运行命令）都不再询问，直接执行。确定吗？")) {
    $("bypass").checked = false; return;
  }
  try { await post("/api/sessions/" + current, { bypass: on }, "PATCH"); }
  catch (e) { alert(e.message); renderTop(); }
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
$("list").onclick = e => {
  const id = e.target.dataset.allow || e.target.dataset.deny;
  if (!id) return;
  e.target.closest(".approval").querySelectorAll("button").forEach(b => b.disabled = true);
  post("/api/approvals/" + id, { allow: !!e.target.dataset.allow }).catch(err => alert(err.message));
};
$("sessions").onclick = async e => {
  const del = e.target.dataset.del;
  if (del) {
    e.stopPropagation();
    if (confirm("删除这个会话、聊天记录和上传到工作区的文件？")) api("/api/sessions/" + del, { method: "DELETE" }).catch(err => alert(err.message));
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
