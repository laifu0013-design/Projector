// 界面共用的小零件：线条图标、预设水果头像、橙光球、消息提示、数字滚动。app.js 和 admin.js 都会用到。
"use strict";

// ---------- 图标（24×24 线条图标） ----------
const ICON_PATHS = {
  plus: "M12 5v14M5 12h14",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-3.6-3.6",
  folder: "M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v7a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 16.5z",
  clip: "M20.5 11.5l-8.3 8.3a5 5 0 0 1-7-7L13.6 4.4a3.4 3.4 0 0 1 4.8 4.8l-8.4 8.4a1.8 1.8 0 0 1-2.6-2.6L15 7.4",
  laptop: "M5 5.5h14a1 1 0 0 1 1 1V15H4V6.5a1 1 0 0 1 1-1zM2.5 18.5h19",
  puzzle: "M9 4.5a2 2 0 1 1 4 0V6h4v4h1.5a2 2 0 1 1 0 4H17v4.5H5V14h1.5a2 2 0 1 0 0-4H5V6h4z",
  send: "M12 19V5M5.5 11.5L12 5l6.5 6.5",
  x: "M6 6l12 12M18 6L6 18",
  chevD: "M6 9l6 6 6-6",
  chevR: "M9 6l6 6-6 6",
  chevL: "M15 6l-6 6 6 6",
  file: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5",
  steps: "M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01",
  gear: "M12 9.2a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 0 0 0-5.6zM12 2.8v2.4M12 18.8v2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M2.8 12h2.4M18.8 12h2.4M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7",
  logout: "M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 16.5L14.5 12 10 7.5M14.5 12H4",
  key: "M8 11.5a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM10.9 12.6L19.5 4M16.5 7l2.5 2.5M14.5 9l2 2",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4",
  moon: "M20.5 13.3A8.5 8.5 0 1 1 10.7 3.5a6.6 6.6 0 0 0 9.8 9.8z",
  eye: "M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12zM12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z",
  sync: "M20 11.5A8 8 0 0 0 6.2 6.3L4 8.5M4 12.5a8 8 0 0 0 13.8 5.2L20 15.5M4 4v4.5h4.5M20 20v-4.5h-4.5",
  download: "M12 4v11M7.5 10.5L12 15l4.5-4.5M5 20h14",
  upload: "M12 20V9M7.5 13.5L12 9l4.5 4.5M5 4h14",
  external: "M14 4h6v6M20 4l-8.5 8.5M18.5 14v4.5a1.5 1.5 0 0 1-1.5 1.5H5.5A1.5 1.5 0 0 1 4 18.5V7a1.5 1.5 0 0 1 1.5-1.5H10",
  check: "M5 12.5l4.5 4.5L19 7",
  alert: "M12 3.5l9.5 16.5h-19zM12 10v4.5M12 17.2h.01",
  menu: "M4 7h16M4 12h16M4 17h16",
  user: "M12 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM4.5 20.5a7.5 7.5 0 0 1 15 0",
  users: "M9 4.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7zM2.5 20a6.5 6.5 0 0 1 13 0M16 4.8a3.5 3.5 0 0 1 0 6.4M18.5 14a6.5 6.5 0 0 1 3 6",
  sparkle: "M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z",
  shield: "M12 3l7.5 3v5.5c0 4.6-3.2 8.2-7.5 9.5-4.3-1.3-7.5-4.9-7.5-9.5V6z",
  trash: "M4.5 7h15M10 11v6M14 11v6M6.5 7l.8 12.2a1.5 1.5 0 0 0 1.5 1.3h6.4a1.5 1.5 0 0 0 1.5-1.3L17.5 7M9.5 7V4.5h5V7",
  command: "M9 6.5A2.5 2.5 0 1 0 6.5 9H17.5A2.5 2.5 0 1 0 15 6.5v11A2.5 2.5 0 1 0 17.5 15H6.5A2.5 2.5 0 1 0 9 17.5z",
  mic: "M12 3.5a3 3 0 0 0-3 3v5a3 3 0 0 0 6 0v-5a3 3 0 0 0-3-3zM5.5 11a6.5 6.5 0 0 0 13 0M12 17.5v3",
  chat: "M4.5 5.5h15a1 1 0 0 1 1 1v9.5a1 1 0 0 1-1 1H10l-4.5 3.5V17h-1a1 1 0 0 1-1-1V6.5a1 1 0 0 1 1-1z",
  edit: "M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4",
  terminal: "M4.5 5h15a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1h-15a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM7.5 9.5l3 2.5-3 2.5M12.5 15h4",
  globe: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c2.5 2.7 3.5 5.7 3.5 9s-1 6.3-3.5 9c-2.5-2.7-3.5-5.7-3.5-9S9.5 5.7 12 3z",
  book: "M5 4.5h5.5A2.5 2.5 0 0 1 13 7v13a2 2 0 0 0-2-2H5zM19 4.5h-3.5A2.5 2.5 0 0 0 13 7",
  bolt: "M13 3L5 13.5h6L10 21l8-10.5h-6z",
  clock: "M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17zM12 7.5V12l3 2",
  stop: "M7 7h10v10H7z",
  phone: "M8 2.5h8A1.5 1.5 0 0 1 17.5 4v16a1.5 1.5 0 0 1-1.5 1.5H8A1.5 1.5 0 0 1 6.5 20V4A1.5 1.5 0 0 1 8 2.5zM11 18.5h2",
  history: "M3.5 12a8.5 8.5 0 1 0 2.5-6M3.5 4v4h4M12 7.5V12l3 2",
  copy: "M9 9h10.5v10.5H9zM15 9V4.5H4.5V15H9",
  branch: "M6 4v10M6 14a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM18 4a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM18 10c0 4-4 4-9 6",
  shieldok: "M12 3l7.5 3v5.5c0 4.6-3.2 8.2-7.5 9.5-4.3-1.3-7.5-4.9-7.5-9.5V6zM8.5 12l2.5 2.5 4.5-5",
};
function icon(name, size = 18, cls = "") {
  const d = ICON_PATHS[name] || ICON_PATHS.sparkle;
  const fill = name === "stop" ? "currentColor" : "none";
  return `<svg class="ic ${cls}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="${fill}" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`;
}

// ---------- 预设头像：16 个水果小人 ----------
const AVATAR_LIST = [
  ["orange", "橙子"], ["lemon", "柠檬"], ["lime", "青柠"], ["grapefruit", "西柚"],
  ["peach", "蜜桃"], ["cherry", "樱桃"], ["grape", "葡萄"], ["blueberry", "蓝莓"],
  ["kiwi", "猕猴桃"], ["avocado", "牛油果"], ["watermelon", "西瓜"], ["pineapple", "菠萝"],
  ["strawberry", "草莓"], ["coconut", "椰子"], ["pear", "雪梨"], ["plum", "李子"],
];
const AVATAR_NAMES = Object.fromEntries(AVATAR_LIST);

function _face(cx, cy, s = 1, ink = "#3b2a1f") {
  const ex = 6 * s, r = 2.1 * s;
  return `<g class="face">
    <ellipse class="eye" cx="${cx - ex}" cy="${cy}" rx="${r}" ry="${r * 1.15}" fill="${ink}"/>
    <ellipse class="eye" cx="${cx + ex}" cy="${cy}" rx="${r}" ry="${r * 1.15}" fill="${ink}"/>
    <circle cx="${cx - ex + 0.8 * s}" cy="${cy - 0.8 * s}" r="${0.7 * s}" fill="#fff" opacity=".9"/>
    <circle cx="${cx + ex + 0.8 * s}" cy="${cy - 0.8 * s}" r="${0.7 * s}" fill="#fff" opacity=".9"/>
    <ellipse cx="${cx - ex - 2.5 * s}" cy="${cy + 4 * s}" rx="${2.6 * s}" ry="${1.6 * s}" fill="#ff7f72" opacity=".5"/>
    <ellipse cx="${cx + ex + 2.5 * s}" cy="${cy + 4 * s}" rx="${2.6 * s}" ry="${1.6 * s}" fill="#ff7f72" opacity=".5"/>
    <path d="M${cx - 2.6 * s} ${cy + 3.2 * s} q${2.6 * s} ${2.8 * s} ${5.2 * s} 0" stroke="${ink}" stroke-width="${1.6 * s}" fill="none" stroke-linecap="round"/>
  </g>`;
}
const _leaf = (x, y, rot = -30, c = "#5fa052") =>
  `<path d="M0 0 q7 -7 13 -2 q-6 6 -13 2z" fill="${c}" transform="translate(${x} ${y}) rotate(${rot})"/>`;
const _stem = (x1, y1, x2, y2, c = "#6b4f2a") =>
  `<path d="M${x1} ${y1} Q${(x1 + x2) / 2 + 2} ${(y1 + y2) / 2} ${x2} ${y2}" stroke="${c}" stroke-width="2.2" fill="none" stroke-linecap="round"/>`;
const _shine = (x, y, r = 4) => `<ellipse cx="${x}" cy="${y}" rx="${r}" ry="${r * 0.65}" fill="#fff" opacity=".28" transform="rotate(-30 ${x} ${y})"/>`;

const AVATAR_ART = {
  orange: ["#ffe8d2", () => `${_stem(32, 18, 33, 13)}<circle cx="32" cy="37" r="19" fill="#f5892b"/>${_shine(24, 28)}
    <g fill="#e2741c" opacity=".35"><circle cx="40" cy="45" r=".9"/><circle cx="44" cy="40" r=".9"/><circle cx="21" cy="44" r=".9"/></g>${_leaf(33, 15)}${_face(32, 38)}`],
  lemon: ["#fff6cc", () => `<ellipse cx="32" cy="37" rx="21" ry="16" fill="#f7d84a"/><ellipse cx="11.5" cy="37" rx="3.2" ry="2.4" fill="#efcb2f"/>
    <ellipse cx="52.5" cy="37" rx="3.2" ry="2.4" fill="#efcb2f"/>${_shine(23, 30)}${_leaf(31, 21, -50, "#7bb661")}${_face(32, 38)}`],
  lime: ["#e6f4d6", () => `<circle cx="32" cy="37" r="18" fill="#8cc152"/><circle cx="32" cy="37" r="14.5" fill="#a8d470" opacity=".55"/>
    ${_shine(24, 29)}${_leaf(32, 19, -40, "#4e8a3a")}${_face(32, 38)}`],
  grapefruit: ["#ffe1d8", () => `<circle cx="32" cy="36" r="20" fill="#f7a072"/><circle cx="32" cy="36" r="16.5" fill="#f4675a"/>
    <g stroke="#ffd6cc" stroke-width="1.1" opacity=".55">${[0, 45, 90, 135].map(a => `<line x1="32" y1="20" x2="32" y2="52" transform="rotate(${a} 32 36)"/>`).join("")}</g>
    <circle cx="32" cy="36" r="9" fill="#f4675a"/>${_face(32, 37, 0.95, "#5a1d17")}`],
  peach: ["#ffe6dc", () => `<path d="M32 55C15 52 12 33 21 25c5-4 9-3 11 1 2-4 6-5 11-1 9 8 6 27-11 30z" fill="#f9a88b"/>
    <path d="M32 26q-4 13 0 28" stroke="#e8836a" stroke-width="1.6" fill="none" opacity=".55"/>${_shine(23, 33, 3.5)}${_leaf(33, 23, -35)}${_face(32, 40)}`],
  cherry: ["#ffe0e3", () => `<path d="M21 33Q26 18 38 12M41 32Q39 20 38 12" stroke="#5e7d34" stroke-width="2" fill="none" stroke-linecap="round"/>
    ${_leaf(38, 12, -10)}<circle cx="20" cy="40" r="11" fill="#b81d33"/><circle cx="38" cy="41" r="15" fill="#d7263d"/>${_shine(32, 34, 3.5)}${_face(38, 43, 0.85, "#3a0d12")}`],
  grape: ["#ece3fa", () => `${_stem(32, 17, 33, 12)}${_leaf(33, 14, -25)}<g fill="#8e6bd1">
    <circle cx="23" cy="25" r="7.5"/><circle cx="32" cy="23" r="7.5"/><circle cx="41" cy="25" r="7.5"/><circle cx="19" cy="35" r="7.5"/><circle cx="45" cy="35" r="7.5"/></g>
    <circle cx="32" cy="40" r="13.5" fill="#7e57c2"/>${_shine(27, 34, 3)}${_face(32, 41, 0.85, "#22133d")}`],
  blueberry: ["#e2e8fb", () => `<circle cx="32" cy="37" r="19" fill="#4c6edb"/>${_shine(24, 29)}
    <path d="M27 18.5l2.5 3 2.5-4 2.5 4 2.5-3-1 5h-8z" fill="#2c3f8c"/>${_face(32, 39, 1, "#111a3d")}`],
  kiwi: ["#efe9dd", () => `<circle cx="32" cy="36" r="20" fill="#8d6e4a"/><circle cx="32" cy="36" r="17" fill="#9ccc65"/>
    <g fill="#2e2a1f">${Array.from({ length: 12 }, (_, i) => { const a = i / 12 * Math.PI * 2; return `<ellipse cx="${32 + Math.cos(a) * 12}" cy="${36 + Math.sin(a) * 12}" rx="1" ry="1.7" transform="rotate(${a * 57.3 + 90} ${32 + Math.cos(a) * 12} ${36 + Math.sin(a) * 12})"/>`; }).join("")}</g>
    <circle cx="32" cy="36" r="8.5" fill="#eef7e0"/>${_face(32, 36, 0.62)}`],
  avocado: ["#eef3dc", () => `<path d="M32 12c7 0 9 8 10 13 1 4 9 9 9 19 0 9-8 13-19 13S13 53 13 44c0-10 8-15 9-19 1-5 3-13 10-13z" fill="#557a2e"/>
    <path d="M32 16c5 0 6.5 6 7.5 10 1 3.5 7.5 8 7.5 17 0 7-6.5 10.5-15 10.5S17 50 17 43c0-9 6.5-13.5 7.5-17 1-4 2.5-10 7.5-10z" fill="#d9e8a5"/>
    <circle cx="32" cy="43" r="7" fill="#8d5a3b"/>${_shine(29, 41, 2)}${_face(32, 29, 0.75)}`],
  watermelon: ["#e3f3e0", () => `<path d="M8 27a24 24 0 0 0 48 0z" fill="#3f9b4a"/><path d="M11 27a21 21 0 0 0 42 0z" fill="#eef7e4"/>
    <path d="M13.5 27a18.5 18.5 0 0 0 37 0z" fill="#ef5350"/><g fill="#3a1f1a"><ellipse cx="21" cy="35" rx="1.1" ry="1.8"/><ellipse cx="43" cy="35" rx="1.1" ry="1.8"/><ellipse cx="27" cy="41" rx="1.1" ry="1.8"/><ellipse cx="37" cy="41" rx="1.1" ry="1.8"/></g>
    ${_face(32, 32, 0.9, "#3a1212")}`],
  pineapple: ["#fff1cf", () => `<g fill="#5fa052"><path d="M32 6l4 14h-8z"/><path d="M22 10l9 11-6 2z"/><path d="M42 10l-9 11 6 2z"/></g>
    <ellipse cx="32" cy="40" rx="15.5" ry="18" fill="#f9c74f"/><g stroke="#df9f1f" stroke-width="1.2" opacity=".55">
    <path d="M20 32l24 18M20 44l18-14M26 25l20 15M18 39l10-8"/></g>${_face(32, 41, 0.95)}`],
  strawberry: ["#ffe1e4", () => `<path d="M32 56C18 50 11 35 16 26c4-6 28-6 32 0 5 9-2 24-16 30z" fill="#e8414f"/>
    <g fill="#ffe08a">${[[22, 34], [42, 34], [26, 46], [38, 46], [32, 52], [19, 41], [45, 41]].map(([x, y]) => `<ellipse cx="${x}" cy="${y}" rx=".9" ry="1.4"/>`).join("")}</g>
    <path d="M20 23l6 3 6-6 6 6 6-3-3 6H23z" fill="#4f9a45"/>${_face(32, 36, 0.95, "#3a0d12")}`],
  coconut: ["#efe4dc", () => `<circle cx="32" cy="37" r="19" fill="#7b5b45"/><g fill="#4a3326"><circle cx="27" cy="25" r="1.6"/><circle cx="37" cy="25" r="1.6"/><circle cx="32" cy="22" r="1.6"/></g>
    ${_shine(23, 31)}${_face(32, 39, 1, "#fff3e0")}`],
  pear: ["#f2f5d8", () => `${_stem(32, 15, 34, 9)}${_leaf(34, 11, -20)}<path d="M32 14c5 0 7 6 7 11 0 4 10 9 10 19 0 9-8 13-17 13s-17-4-17-13c0-10 10-15 10-19 0-5 2-11 7-11z" fill="#c9d86a"/>
    ${_shine(26, 38, 3.5)}${_face(32, 42)}`],
  plum: ["#f1e1f1", () => `<circle cx="32" cy="37" r="18" fill="#8e3b8e"/><path d="M32 20q-5 17 0 35" stroke="#6f2a6f" stroke-width="1.5" fill="none" opacity=".6"/>
    ${_shine(24, 29)}${_leaf(32, 20, -45, "#5f8f3a")}${_face(32, 39, 1, "#fff0fa")}`],
};

function avatarSVG(id, size = 32) {
  const art = AVATAR_ART[id] || AVATAR_ART.orange;
  return `<svg class="ava-svg" width="${size}" height="${size}" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="32" fill="${art[0]}"/>${art[1]()}</svg>`;
}
function avatarEl(id, size = 32, cls = "") {
  return `<span class="ava ${cls}" title="${AVATAR_NAMES[id] || ""}" style="width:${size}px;height:${size}px">${avatarSVG(id, size)}</span>`;
}

// ---------- 橙光球：Claude 的形象 ----------
// state：idle 呼吸 / busy 旋转流光 / wait 琥珀色脉动
function orb(size = 28, state = "idle") {
  return `<span class="orb orb-${state}" style="--s:${size}px"><i></i><b></b></span>`;
}

// ---------- 消息提示 ----------
function toast(text, kind = "info", ms = 3600) {
  let box = document.getElementById("toasts");
  if (!box) { box = document.createElement("div"); box.id = "toasts"; document.body.appendChild(box); }
  const el = document.createElement("div");
  el.className = "toast toast-" + kind;
  el.innerHTML = icon(kind === "ok" ? "check" : kind === "err" ? "alert" : "sparkle", 16) + `<span></span>`;
  el.querySelector("span").textContent = text;
  box.appendChild(el);
  requestAnimationFrame(() => el.classList.add("in"));
  setTimeout(() => { el.classList.remove("in"); el.classList.add("out"); setTimeout(() => el.remove(), 400); }, ms);
}

// ---------- 数字从 0 滚到目标值 ----------
function countUp(el, to, ms = 900) {
  const start = performance.now();
  const step = t => {
    const p = Math.min(1, (t - start) / ms);
    el.textContent = Math.round(to * (1 - Math.pow(1 - p, 3)));
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// ---------- 主题：浅色 / 深色 / 跟随系统 ----------
function applyTheme(mode) {
  const root = document.documentElement;
  if (mode === "light" || mode === "dark") root.dataset.theme = mode; else delete root.dataset.theme;
}
try { applyTheme(localStorage.getItem("theme") || "auto"); } catch {}
