// 管理后台：用户、所有人的聊天记录、技能库、待审核的技能。只有管理员能打开。
"use strict";
(() => {
  let tab = "users";
  let users = [];
  const roleName = { admin: "管理员", user: "普通用户", device: "板子（语音）" };
  const box = () => $("adminContent");

  async function loadUsers() { users = await api("/api/admin/users"); return users; }
  const userOf = id => users.find(u => u.id === id) || { display_name: "#" + id, avatar: "orange" };

  async function refreshPending() {
    const subs = await api("/api/skills/submissions").catch(() => []);
    const n = subs.filter(s => s.status === "pending").length;
    $("pendingCount").innerHTML = n ? `<span class="tag warn">${n}</span>` : "";
    return subs;
  }

  async function show(t) {
    tab = t;
    $("adminTabs").querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.tab === t));
    box().innerHTML = '<div class="empty-note">读取中…</div>';
    try {
      await loadUsers();
      if (t === "users") await renderUsers();
      else if (t === "chats") await renderChats();
      else if (t === "library") await renderLibrary();
      else if (t === "settings") await renderSettings();
      else if (t === "files") await renderFileOps();
      else await renderReview();
    } catch (e) { box().innerHTML = `<div class="err">${esc(e.message)}</div>`; }
    refreshPending();
  }

  // ---------- 用户 ----------

  async function renderUsers() {
    const [all, subs] = await Promise.all([api("/api/admin/sessions"), refreshPending()]);
    const today = new Date(new Date().toDateString()) / 1000;
    const people = users.filter(u => u.role !== "device");
    const stats = [
      ["users", "账号", people.length],
      ["chat", "今天的会话", all.filter(s => s.updated >= today).length],
      ["bolt", "正在干活", all.filter(s => s.running).length],
      ["puzzle", "待审核技能", subs.filter(s => s.status === "pending").length],
    ];
    box().innerHTML = `
      <div class="stats">${stats.map(([ic, l, v]) => `<div class="stat">${icon(ic, 18)}<div class="v" data-count="${v}">0</div><div class="l">${l}</div></div>`).join("")}</div>
      <div class="card"><h4>${icon("plus", 16)}新建账号</h4>
        <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px">
          <input class="field" id="nuUser" placeholder="用户名（登录用）" style="width:170px">
          <input class="field" id="nuName" placeholder="显示的名字" style="width:150px">
          <input class="field" id="nuPwd" placeholder="初始密码（至少 6 位）" style="width:190px">
          <button class="btn primary" id="nuBtn">创建</button>
        </div>
        <div class="err" id="nuErr"></div>
        <div class="muted" style="font-size:12.5px">同事只能登录、不能注册。把用户名和初始密码告诉他们，登录后可以自己改密码、换头像。</div>
      </div>
      <table class="grid"><tr><th>用户</th><th>角色</th><th title="允许打开“完全放行”">完全放行</th><th>会话</th><th>最近登录</th><th>状态</th><th></th></tr>
      ${users.map((u, i) => `<tr data-uid="${u.id}" style="animation-delay:${i * 30}ms">
        <td><div class="ucell">${avatarEl(u.avatar, 34)}<div><a class="fpath" style="font-family:var(--f-body);font-size:14px" data-act="rename">${esc(u.display_name)}</a><div class="un">@${esc(u.username)}</div></div></div></td>
        <td>${u.role === "device" ? `<span class="tag">${roleName.device}</span>` : `<select class="mini" data-act="role"><option value="user" ${u.role === "user" ? "selected" : ""}>普通用户</option><option value="admin" ${u.role === "admin" ? "selected" : ""}>管理员</option></select>`}</td>
        <td>${u.role === "user" ? `<label class="switch"><input type="checkbox" data-act="bypass" ${u.can_bypass ? "checked" : ""}><span class="sw"></span></label>` : u.role === "admin" ? `<span class="muted">${icon("check", 16)}</span>` : "—"}</td>
        <td>${u.sessions}</td>
        <td class="muted" style="font-size:13px">${u.last_seen ? ftime(u.last_seen) : "—"}</td>
        <td>${u.role === "device" ? "" : `<label class="switch"><input type="checkbox" data-act="disabled" ${u.disabled ? "checked" : ""}><span class="sw"></span>停用</label>`}</td>
        <td style="white-space:nowrap;text-align:right">
          ${u.role === "device" ? `<button class="btn small ghost" data-act="ava">${icon("user", 14)}头像</button>` : `<button class="btn small ghost" data-act="reset">${icon("key", 14)}重置密码</button>`}
          ${u.role === "device" || u.id === me.id ? "" : `<button class="btn small ghost danger" data-act="delete">${icon("trash", 14)}</button>`}</td>
      </tr>`).join("")}</table>`;
    box().querySelectorAll("[data-count]").forEach(el => countUp(el, +el.dataset.count));
    $("nuBtn").onclick = async () => {
      try {
        await post("/api/admin/users", { username: $("nuUser").value.trim(), display_name: $("nuName").value.trim(), password: $("nuPwd").value });
        toast("已创建账号 " + $("nuUser").value.trim(), "ok");
        show("users");
      } catch (e) { $("nuErr").textContent = e.message; }
    };
  }

  async function patchUser(uid, body, msg = "已保存") {
    try { await post("/api/admin/users/" + uid, body, "PATCH"); toast(msg, "ok", 1800); }
    catch (e) { toast(e.message, "err"); }
    show("users");
  }

  box().addEventListener("change", e => {
    const act = e.target.dataset.act, tr = e.target.closest("tr[data-uid]");
    if (!act || !tr || tab !== "users") return;
    const uid = +tr.dataset.uid;
    if (act === "role") patchUser(uid, { role: e.target.value });
    if (act === "bypass") patchUser(uid, { can_bypass: e.target.checked }, e.target.checked ? "已开通完全放行" : "已关闭完全放行");
    if (act === "disabled") patchUser(uid, { disabled: e.target.checked }, e.target.checked ? "已停用" : "已启用");
  });

  box().addEventListener("click", async e => {
    const el = e.target.closest("[data-act]"), tr = e.target.closest("tr[data-uid]");
    if (tab !== "users" || !el || !tr || el.tagName === "SELECT" || el.type === "checkbox") return;
    const act = el.dataset.act, uid = +tr.dataset.uid, u = users.find(x => x.id === uid);
    if (act === "rename") {
      const n = prompt("显示的名字", u.display_name);
      if (n) patchUser(uid, { display_name: n });
    } else if (act === "reset") {
      const p = prompt(`给 ${u.display_name} 设一个新密码（至少 6 位），他会被要求重新登录：`);
      if (p) patchUser(uid, { password: p }, "密码已重置");
    } else if (act === "ava") {
      const body = dialog(`${u.display_name} 的头像`, `<div class="ava-grid">${AVATAR_LIST.map(([id, n], i) =>
        `<button class="ava-opt ${id === u.avatar ? "on" : ""}" data-ava="${id}" style="animation-delay:${i * 25}ms">${avatarEl(id, 56)}${n}</button>`).join("")}</div>`);
      body.onclick = ev => { const b = ev.target.closest("[data-ava]"); if (b) { closeDialog(); patchUser(uid, { avatar: b.dataset.ava }); } };
    } else if (act === "delete") {
      if (confirm(`删除 ${u.display_name}？他的所有会话、聊天记录和工作区文件都会删除，不能恢复。\n只是不让他用的话，打开“停用”就行。`)) {
        try { await api("/api/admin/users/" + uid, { method: "DELETE" }); toast("已删除", "ok"); } catch (err) { toast(err.message, "err"); }
        show("users");
      }
    }
  });

  // ---------- 聊天记录：按用户分组折叠 ----------

  async function renderChats() {
    const all = await api("/api/admin/sessions");
    const groups = users.map(u => ({ u, list: all.filter(s => s.user_id === u.id) })).sort((a, b) => b.list.length - a.list.length);
    box().innerHTML = '<p class="muted" style="margin-top:0;font-size:13px">点开会话可以查看完整聊天记录和过程；别人的会话只能看，不能接着聊。</p>' +
      groups.map((g, i) => `<details class="grp" data-uid="${g.u.id}" style="animation-delay:${i * 40}ms">
        <summary>${avatarEl(g.u.avatar, 34)}<div><b>${esc(g.u.display_name)}</b><div class="muted" style="font-size:12.5px">${esc(roleName[g.u.role] || g.u.role)} · ${g.list.length} 个会话
          ${g.list.some(s => s.running) ? ' · <span style="color:var(--accent)">有任务在进行</span>' : ""}${g.list.some(s => s.pending) ? ' · <span style="color:var(--amber)">有待确认</span>' : ""}</div></div>${icon("chevD", 18)}</summary>
        <div class="list">${g.list.map(s => sessRow(s, true)).join("") || '<div class="empty-note" style="padding:4px 10px">没有会话</div>'}</div>
      </details>`).join("");
    box().querySelectorAll(".sess").forEach(row => row.onclick = () => {
      $("adminBox").classList.remove("show");
      openSession(row.dataset.id);
    });
  }

  let chatsTimer;
  window.adminOnSession = () => {
    if (tab !== "chats" || !$("adminBox").classList.contains("show")) return;
    clearTimeout(chatsTimer);
    chatsTimer = setTimeout(() => {
      const open = [...box().querySelectorAll("details[open]")].map(d => d.dataset.uid);
      renderChats().then(() => box().querySelectorAll("details").forEach(d => { if (open.includes(d.dataset.uid)) d.open = true; }));
    }, 800);
  };

  // ---------- 技能库 ----------

  async function renderLibrary() {
    const [lib, local] = await Promise.all([api("/api/admin/library"), api("/api/admin/local-skills")]);
    const people = users.filter(u => u.role !== "admin");
    const localNew = local.skills.filter(s => !s.in_library);
    box().innerHTML = `
      ${local.skills.length ? `<div class="card warnc">
        <h4>${icon("alert", 16)}台式机本机还有 ${local.skills.length} 个 skill</h4>
        <div class="muted" style="font-size:13px">位置：<code>${esc(local.dir)}</code>。这里的 skill 对<b>所有人</b>都生效，不受分配控制。建议导入技能库后，把这个文件夹里的移走。</div>
        <details style="margin-top:6px"><summary class="muted" style="cursor:pointer;font-size:13px">展开查看</summary>
        ${local.skills.map(s => `<div style="margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:13px" title="${esc(s.description)}"><b>${esc(s.name)}</b> ${s.in_library ? '<span class="status approved">已在库里</span>' : ""} <span class="muted">${esc(s.description)}</span></div>`).join("")}
        </details>
        ${localNew.length ? `<button class="btn small" id="importLocal" style="margin-top:10px">${icon("download", 14)}把 ${localNew.length} 个导入技能库</button>` : ""}
      </div>` : ""}
      ${lib.length ? "" : `<div style="text-align:center;padding:30px 0">${orb(56)}<p class="muted">技能库是空的。同事提交的 skill 审核通过后会出现在这里。</p></div>`}
      ${lib.map((s, i) => `<div class="card" data-skill="${esc(s.name)}" style="animation-delay:${i * 40}ms">
        <h4>${icon("puzzle", 16)}${esc(s.name)} <span class="vtag">v${s.version || 1}</span>${s.pending ? ` <span class="tag warn">${s.pending} 个新版本待审核</span>` : ""}</h4>
        <div class="muted" style="font-size:13px">${esc(s.description)}</div>
        <div class="muted" style="font-size:12px;margin-top:2px">提交人：${esc(s.submitter || "—")} · 更新：${ftime(s.updated)}</div>
        <div class="users-pick">
          <label class="upick all"><input type="checkbox" data-everyone ${s.everyone ? "checked" : ""}>所有人可用</label>
          ${people.map(u => `<label class="upick"><input type="checkbox" data-u="${u.id}" ${s.users.includes(u.id) ? "checked" : ""} ${s.everyone ? "disabled" : ""}>${avatarEl(u.avatar, 22)}${esc(u.display_name)}</label>`).join("")}
        </div>
        <div style="margin-top:10px;display:flex;gap:6px;align-items:center">
          <button class="btn small" data-hist>${icon("history", 14)}版本记录</button>
          <button class="btn small" data-view>${icon("eye", 14)}查看文件</button>
          <button class="btn small ghost danger" data-remove>${icon("trash", 14)}从库里删除</button>
          <span class="muted" style="font-size:12px;margin-left:auto">管理员默认都能用</span>
        </div>
      </div>`).join("")}`;
    $("importLocal") && ($("importLocal").onclick = async () => {
      const r = await post("/api/admin/local-skills/import", { names: localNew.map(s => s.name) });
      toast(`已导入 ${r.imported.length} 个，记得分配给需要的人`, "ok");
      show("library");
    });
    box().querySelectorAll(".card[data-skill]").forEach(card => {
      const name = card.dataset.skill;
      const save = async () => {
        const everyone = card.querySelector("[data-everyone]").checked;
        card.querySelectorAll("[data-u]").forEach(c => c.disabled = everyone);
        const ids = [...card.querySelectorAll("[data-u]:checked")].map(c => +c.dataset.u);
        try { await post(`/api/admin/library/${name}/access`, { everyone, users: ids }, "PUT"); toast("已保存分配", "ok", 1500); }
        catch (e) { toast(e.message, "err"); }
      };
      card.querySelectorAll("input[type=checkbox]").forEach(c => c.onchange = save);
      card.querySelector("[data-hist]").onclick = () => openSkillDetail(name);
      card.querySelector("[data-view]").onclick = async () => {
        const d = await api(`/api/admin/library/${name}/files`);
        fileViewer(`技能 ${name} 的文件`, d.files);
      };
      card.querySelector("[data-remove]").onclick = async () => {
        if (!confirm(`从技能库删除 ${name}？所有人都不能再用它。`)) return;
        await api(`/api/admin/library/${name}`, { method: "DELETE" });
        toast("已删除", "ok");
        show("library");
      };
    });
  }

  // ---------- 待审核 ----------

  async function renderReview() {
    const subs = await refreshPending();
    const st = { pending: "待审核", approved: "已入库", rejected: "未通过" };
    const pending = subs.filter(s => s.status === "pending"), done = subs.filter(s => s.status !== "pending");
    const card = (s, i) => `<div class="card" style="animation-delay:${i * 40}ms"><h4>${avatarEl(userOf(s.submitted_by).avatar, 26)}${esc(s.name)} <span class="status ${s.status}">${st[s.status]}</span>${s.current_version && s.status === "pending" ? ` <span class="vtag">更新 v${s.current_version}</span>` : ""}</h4>
      ${s.changelog ? `<div style="font-size:13.5px;margin:2px 0">${esc(s.changelog)}</div>` : ""}
      <div class="muted" style="font-size:13px">${esc(s.description)}</div>
      <div class="muted" style="font-size:12px;margin-top:2px">提交人：${esc(s.submitter || "—")} · ${ftime(s.created)}${s.note ? " · 备注：" + esc(s.note) : ""}</div>
      <div style="margin-top:10px;display:flex;gap:6px">
        <button class="btn small ${s.status === "pending" ? "primary" : ""}" data-review="${s.id}">${icon(s.status === "pending" ? "shield" : "eye", 14)}${s.status === "pending" ? "审核" : "查看"}</button>
        <button class="btn small ghost danger" data-delsub="${s.id}">${icon("trash", 14)}</button></div></div>`;
    box().innerHTML = `<div class="card warnc" style="padding:10px 14px"><div style="font-size:13px">${icon("alert", 14)} skill 里的脚本会在台式机上以完整权限运行，审核时请把标了“脚本”的文件都看一遍。</div></div>
      <div style="font-weight:600;margin:16px 0 8px">待审核（${pending.length}）</div>${pending.map(card).join("") || '<p class="empty-note">没有待审核的提交。</p>'}
      <div style="font-weight:600;margin:16px 0 8px">历史</div>${done.map(card).join("") || '<p class="empty-note">没有。</p>'}`;
    box().querySelectorAll("[data-review]").forEach(b => b.onclick = () => review(b.dataset.review));
    box().querySelectorAll("[data-delsub]").forEach(b => b.onclick = async () => {
      if (!confirm("删除这个提交记录？（已入库的技能不受影响）")) return;
      await api("/api/skills/submissions/" + b.dataset.delsub, { method: "DELETE" });
      show("review");
    });
  }

  async function review(id) {
    const d = await api(`/api/skills/submissions/${id}/files`);
    const s = d.submission;
    const pending = s.status === "pending";
    const decide = approve => async () => {
      const note = $("rvNote").value.trim();
      await post(`/api/skills/submissions/${id}/review`, { approve, note });
      toast(approve ? `已入库：${s.name}。到“技能库”里分配给需要的人` : "已退回", approve ? "ok" : "info");
      show(approve ? "library" : "review");
    };
    const ch = d.changes;
    const summary = ch ? [["新增", ch.added.length], ["修改", ch.modified.length], ["删除", ch.removed.length]].filter(x => x[1]).map(x => x.join(" ")).join("，") || "文件没有变化" : "";
    fileViewer(`审核技能：${s.name}`, d.files,
      pending ? [{ label: "退回", icon: "x", danger: true, onClick: decide(false) }, { label: d.updates_existing ? `通过，发布为 v${d.current_version + 1}` : "通过并入库", icon: "check", primary: true, onClick: decide(true) }] : [],
      `<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">${avatarEl(userOf(s.submitted_by).avatar, 36)}<div>
        <div><b>${esc(s.submitter || userOf(s.submitted_by).display_name)}</b> 提交 · ${d.files.length} 个文件
          ${d.updates_existing ? ` · <span class="status pending">更新 v${d.current_version} → v${d.current_version + 1}：${summary}</span>` : ' · <span class="status approved">新技能</span>'}</div>
        <div class="muted" style="font-size:13px">${esc(s.description)}</div></div></div>
       <div class="changelog"><b>更新说明</b>${esc(s.changelog || "（没写）")}</div>
       ${pending ? '<input class="field" id="rvNote" placeholder="给提交人的备注（可选）" style="margin-bottom:10px">' : ""}`, ch);
  }

  // ---------- 文件记录：谁在什么时候上传、改名、删除了什么 ----------

  async function renderFileOps() {
    const ops = await api("/api/admin/file-ops");
    const color = { 上传: "approved", 覆盖上传: "pending", 新建文件夹: "approved", 改名: "pending", 删除: "rejected", 恢复: "approved", 彻底删除: "rejected", 知识库新建: "approved", 知识库修改: "pending", 知识库改名: "pending", 知识库删除: "rejected" };
    box().innerHTML = `
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
        <div class="muted" style="font-size:13px;flex:1">在文件库里上传、新建、改名、删除、恢复的操作都会记在这里（最近 300 条）。删除的文件在回收站里保留 30 天。</div>
        <button class="btn small" id="opTrash">${icon("trash", 14)}打开回收站</button>
      </div>
      ${ops.length ? `<table class="grid"><tr><th>时间</th><th>谁</th><th>操作</th><th>文件</th></tr>
      ${ops.map((o, i) => `<tr style="animation-delay:${Math.min(i, 30) * 15}ms">
        <td class="muted" style="font-size:12.5px;white-space:nowrap">${ftime(o.created)}</td>
        <td><div class="ucell">${avatarEl(userOf(o.user_id).avatar, 24)}${esc(o.who || "—")}</div></td>
        <td><span class="status ${color[o.action] || ""}">${esc(o.action)}</span></td>
        <td style="font-size:13px;word-break:break-all" title="${esc(o.path)}">${esc(shortPath(o.path))}${o.detail ? ` <span class="muted">${esc(o.detail)}</span>` : ""}</td>
      </tr>`).join("")}</table>` : `<div style="text-align:center;padding:30px 0">${orb(48)}<p class="muted">还没有文件操作记录。</p></div>`}`;
    $("opTrash").onclick = () => openTrash();
  }

  // ---------- 设置：权限档位、命令名单 ----------

  async function renderSettings() {
    const st = await api("/api/admin/settings");
    const tagEditor = (key, list, def, hint) => `
      <div class="tagbox" data-key="${key}">${list.map(c => `<span class="chip">${esc(c)}<button data-rm="${esc(c)}">${icon("x", 11)}</button></span>`).join("")}
        <input placeholder="${esc(hint)}，回车添加"></div>
      <div style="margin-top:6px;display:flex;gap:8px"><button class="btn small ghost" data-reset="${key}">恢复默认</button></div>`;
    box().innerHTML = `
      <div style="font-weight:600;margin-bottom:10px">权限档位 <span class="muted" style="font-weight:400;font-size:13px">对所有人生效；“完全放行”在“用户”里按人开通</span></div>
      <div class="modes">
        <div class="mode ${st.perm_mode === "standard" ? "on" : ""}" data-mode="standard"><b>${icon("bolt", 17)}标准（推荐）</b>
          <p>读文件、改文件、运行 python / unzip 等命令都直接执行，只有删除、移动、格式化这类<b>危险命令</b>才弹确认。适合不懂技术的同事。</p></div>
        <div class="mode ${st.perm_mode === "strict" ? "on" : ""}" data-mode="strict"><b>${icon("shield", 17)}严格</b>
          <p>只有 ls、cat 等只读命令和下面白名单里的命令自动执行，其他所有操作都要确认。</p></div>
      </div>
      <div class="card" style="margin-top:16px" ${st.perm_mode === "standard" ? "" : "hidden"}>
        <h4>${icon("alert", 16)}标准档：仍然要确认的命令</h4>
        <div class="muted" style="font-size:13px">命令以这些开头时会弹确认，比如写 <code>rm</code> 会拦住 <code>rm -rf 文件夹</code>。注意：用 python 脚本删文件这类间接操作拦不住。</div>
        ${tagEditor("ask_cmds", st.ask_cmds, st.default_ask, "比如 git push")}
      </div>
      <div class="card" style="margin-top:16px" ${st.perm_mode === "strict" ? "" : "hidden"}>
        <h4>${icon("check", 16)}严格档：额外自动放行的命令</h4>
        <div class="muted" style="font-size:13px">已默认放行：${st.safe.map(c => `<code>${esc(c)}</code>`).join(" ")}</div>
        ${tagEditor("bash_allow", st.bash_allow, st.default_allow, "比如 python")}
      </div>`;
    const save = async patch => { try { await post("/api/admin/settings", patch, "PUT"); toast("已保存，新的对话开始生效", "ok", 2000); } catch (e) { toast(e.message, "err"); } show("settings"); };
    box().querySelectorAll("[data-mode]").forEach(m => m.onclick = () => { if (!m.classList.contains("on")) save({ perm_mode: m.dataset.mode }); });
    box().querySelectorAll(".tagbox").forEach(tb => {
      const key = tb.dataset.key, list = st[key];
      tb.querySelector("input").onkeydown = e => {
        if (e.key !== "Enter" || e.isComposing) return;
        const v = e.target.value.trim(); if (!v) return;
        save({ [key]: [...list, v] });
      };
      tb.onclick = e => { const rm = e.target.closest("[data-rm]"); if (rm) save({ [key]: list.filter(c => c !== rm.dataset.rm) }); };
    });
    box().querySelectorAll("[data-reset]").forEach(b => b.onclick = () => {
      const key = b.dataset.reset;
      if (confirm("恢复成默认名单？")) save({ [key]: key === "ask_cmds" ? st.default_ask : st.default_allow });
    });
  }

  $("adminTabs").onclick = e => { const b = e.target.closest("[data-tab]"); if (b) show(b.dataset.tab); };
  window.openAdmin = () => { $("adminBox").classList.add("show"); $("side").classList.remove("open"); show(tab); };
})();
