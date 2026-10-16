/* ==========================================================================
   GameHub 前端逻辑
   ========================================================================== */

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = {
  games: [],
  platform: "all",
  sort: "name",
  query: "",
  scanning: false,
  labels: {},
  colors: {},
  current: null,     // 详情面板中的游戏
};

/* ---------------------------------------------------------------- 工具 */

const imgUrl = (u) => (u ? "/api/img?u=" + encodeURIComponent(u) : "");

/** 依次尝试多个图片地址，全部失败时调用 onFail。 */
function loadImage(imgEl, urls, onFail) {
  let i = 0;
  const tryNext = () => {
    if (i >= urls.length) { onFail && onFail(); return; }
    const u = urls[i++];
    if (!u) return tryNext();
    imgEl.onerror = tryNext;
    imgEl.onload = () => imgEl.classList.add("is-loaded");
    imgEl.src = imgUrl(u);
  };
  tryNext();
}

/** 从封面 URL 推导备用图（Steam CDN 命名规律）。 */
function coverCandidates(g) {
  const list = [];
  if (g.cover) list.push(g.cover);
  const base = g.cover || "";
  const dir = base.substring(0, base.lastIndexOf("/") + 1);
  if (dir) {
    list.push(dir + "library_600x900_2x.jpg");
    list.push(dir + "header.jpg");
    list.push(dir + "capsule_616x353.jpg");
  }
  return [...new Set(list.filter(Boolean))];
}

function fmtSize(bytes) {
  if (!bytes || bytes < 0) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, n = bytes;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(n >= 10 || i < 2 ? 0 : 1) + " " + u[i];
}

function fmtDate(ts) {
  if (!ts) return "从未";
  const d = new Date(ts * 1000);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
  if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
  if (diff < 86400 * 30) return Math.floor(diff / 86400) + " 天前";
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** 用游戏名派生一个稳定的色相，给无封面的卡片生成专属柔和渐变。 */
function nameHue(s) {
  let h = 0;
  const str = String(s || "?");
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) >>> 0;
  return h % 360;
}

function fallbackGradient(name) {
  const h = nameHue(name);
  const h2 = (h + 34) % 360;
  return `linear-gradient(155deg, hsl(${h} 54% 91%) 0%, hsl(${h2} 46% 79%) 100%)`;
}

/* ---------------------------------------------------------------- Toast */

let toastTimer = null;
function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.hidden = false;
  requestAnimationFrame(() => el.classList.add("is-show"));
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.classList.remove("is-show");
    setTimeout(() => { el.hidden = true; }, 300);
  }, 2400);
}

/* ---------------------------------------------------------------- 数据 */

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok && res.status !== 404) throw new Error("HTTP " + res.status);
  return res.json();
}

async function loadGames() {
  const data = await api("/api/games");
  state.games = data.games || [];
  renderAll();
}

async function loadMeta() {
  try {
    const p = await api("/api/platforms");
    state.labels = p.labels || {};
    state.colors = p.colors || {};
  } catch (e) { /* 忽略 */ }
}

/* ---------------------------------------------------------------- 渲染 */

function filtered() {
  let list = state.games.slice();

  if (state.platform === "favorite") {
    list = list.filter(g => g.favorite);
  } else if (state.platform !== "all") {
    list = list.filter(g => g.platform === state.platform);
  }

  const q = state.query.trim().toLowerCase();
  if (q) {
    list = list.filter(g =>
      (g.name || "").toLowerCase().includes(q) ||
      (state.labels[g.platform] || "").toLowerCase().includes(q));
  }

  const by = {
    name:   (a, b) => (a.name || "").localeCompare(b.name || "", "zh"),
    recent: (a, b) => (b.last_played || 0) - (a.last_played || 0) ||
                      (a.name || "").localeCompare(b.name || "", "zh"),
    size:   (a, b) => (b.size_bytes || 0) - (a.size_bytes || 0),
  }[state.sort] || ((a, b) => 0);

  // 收藏置顶
  list.sort((a, b) => (b.favorite ? 1 : 0) - (a.favorite ? 1 : 0) || by(a, b));
  return list;
}

function renderPlatformNav() {
  const counts = {};
  let fav = 0;
  state.games.forEach(g => {
    counts[g.platform] = (counts[g.platform] || 0) + 1;
    if (g.favorite) fav++;
  });

  $("#countAll").textContent = state.games.length;
  $("#countFav").textContent = fav;

  const order = Object.keys(state.labels);
  const host = $("#platformList");
  host.innerHTML = "";

  order.filter(k => counts[k]).forEach(key => {
    const b = document.createElement("button");
    b.className = "nav-item" + (state.platform === key ? " is-active" : "");
    b.dataset.platform = key;
    b.innerHTML = `
      <span class="nav-dot" style="background:${state.colors[key] || "#8e8e93"}"></span>
      <span class="nav-label">${esc(state.labels[key] || key)}</span>
      <span class="nav-count">${counts[key]}</span>`;
    host.appendChild(b);
  });

  $$("#platformNav > .nav-item").forEach(b => {
    b.classList.toggle("is-active", b.dataset.platform === state.platform);
  });
}

function cardHTML(g, idx) {
  const color = state.colors[g.platform] || "#8e8e93";
  const label = state.labels[g.platform] || g.platform;
  const sub = [label, fmtSize(g.size_bytes)].filter(Boolean).join(" · ");
  const letter = esc((g.name || "?").trim().charAt(0).toUpperCase());
  return `
  <div class="card" data-id="${g.id}" style="animation-delay:${Math.min(idx * 16, 320)}ms">
    <div class="card-art" style="background:${fallbackGradient(g.name)}">
      <div class="card-fallback">
        ${g.icon ? `<img class="fb-icon" src="${esc(g.icon)}" alt="">` : `<div class="fb-letter">${letter}</div>`}
        <div class="fb-name">${esc(g.name)}</div>
      </div>
      <img alt="" loading="lazy">
      <span class="card-badge" style="background:${color}">${esc(label)}</span>
      <button class="card-fav ${g.favorite ? "is-on" : ""}" title="收藏">
        <svg viewBox="0 0 24 24" width="13" height="13"
             fill="${g.favorite ? "currentColor" : "none"}"
             stroke="currentColor" stroke-width="2" stroke-linejoin="round">
          <path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8L3.5 9.7l5.9-.9z"/>
        </svg>
      </button>
      <div class="card-play"><i>
        <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
      </i></div>
    </div>
    <div class="card-info">
      <div class="card-name" title="${esc(g.name)}">${esc(g.name)}</div>
      <div class="card-sub">${esc(sub)}</div>
    </div>
  </div>`;
}

function renderGrid() {
  const list = filtered();
  const grid = $("#grid");
  const empty = $("#empty");

  if (!list.length) {
    grid.innerHTML = "";
    empty.hidden = false;
    const noneAtAll = state.games.length === 0;
    $("#emptyTitle").textContent = noneAtAll ? "还没有游戏" : "没有匹配的结果";
    $("#emptySub").textContent = noneAtAll
      ? "点击左侧「重新扫描」，自动识别本机已安装的游戏"
      : "换个关键词或平台试试";
    return;
  }

  empty.hidden = true;
  grid.innerHTML = list.map(cardHTML).join("");

  // 异步加载封面（只取封面主图，不碰兜底图标）
  $$(".card", grid).forEach(card => {
    const g = state.games.find(x => x.id === card.dataset.id);
    if (!g) return;
    const img = $(".card-art > img", card);
    if (!img) return;
    loadImage(img, coverCandidates(g), () => { img.remove(); });
  });
}

function renderTitles() {
  const t = $("#viewTitle"), s = $("#viewSubtitle");
  if (state.platform === "all") t.textContent = "全部游戏";
  else if (state.platform === "favorite") t.textContent = "我的收藏";
  else t.textContent = state.labels[state.platform] || state.platform;

  const n = filtered().length;
  s.textContent = `${n} 个游戏` + (state.scanning ? " · 扫描中…" : "");
}

function renderAll() {
  renderPlatformNav();
  renderTitles();
  renderGrid();
}

/* ---------------------------------------------------------------- 扫描 */

async function startScan() {
  const btn = $("#btnScan");
  btn.classList.add("is-busy");
  await api("/api/scan", { method: "POST", body: JSON.stringify({ full: true }) });
  pollScan();
}

let scanPoll = null;
async function pollScan() {
  clearTimeout(scanPoll);
  let st;
  try { st = await api("/api/scan/status"); } catch (e) { return; }

  const bar = $("#scanbar");
  state.scanning = !!st.scanning;

  if (st.scanning || st.phase === "art" || st.phase === "detect") {
    bar.hidden = false;
    const pct = st.total > 0 ? Math.round((st.index / st.total) * 100) : 0;
    $("#scanFill").style.width = Math.max(6, pct) + "%";
    $("#scanText").textContent = st.message || "正在扫描…";
  }

  if (!st.scanning) {
    $("#btnScan").classList.remove("is-busy");
    if (st.phase === "done" || st.phase === "error") {
      setTimeout(() => { bar.hidden = true; }, 700);
      if (st.phase === "done") {
        await loadGames();
        toast(`扫描完成，共 ${st.count} 个游戏`);
      } else {
        toast(st.message || "扫描失败");
      }
      return;
    }
  }
  scanPoll = setTimeout(pollScan, 600);
}

/* ---------------------------------------------------------------- 启动 */

async function launch(g) {
  // 启动动画
  const ov = $("#launchOverlay");
  const art = $("#launchArt");
  const urls = coverCandidates(g);
  art.style.backgroundImage = urls.length
    ? `url("${imgUrl(urls[0])}"), linear-gradient(155deg,#3a3a3c,#1c1c1e)`
    : "linear-gradient(155deg,#3a3a3c,#1c1c1e)";
  $("#launchName").textContent = g.name;
  $("#launchStatus").textContent = "正在启动…";
  ov.hidden = false;

  let res;
  try {
    res = await api(`/api/launch/${g.id}`, { method: "POST" });
  } catch (e) {
    res = { ok: false, message: "请求失败" };
  }

  $("#launchStatus").textContent = res.ok ? "已启动，祝游戏愉快" : (res.message || "启动失败");
  const sp = $(".launch-spinner", ov);
  if (!res.ok && sp) sp.style.display = "none";

  setTimeout(() => {
    ov.hidden = true;
    if (sp) sp.style.display = "";
    if (!res.ok) toast(res.message || "启动失败");
  }, res.ok ? 1500 : 2200);

  if (res.ok) {
    g.play_count = (g.play_count || 0) + 1;
    g.last_played = Math.floor(Date.now() / 1000);
    if (state.sort === "recent") renderGrid();
  }
}

/* ---------------------------------------------------------------- 详情 */

function openSheet(g) {
  state.current = g;
  $("#sheetArt").style.backgroundImage =
    coverCandidates(g).length
      ? `url("${imgUrl(coverCandidates(g)[0])}"), linear-gradient(155deg,#3a3a3c,#1c1c1e)`
      : "linear-gradient(155deg,#3a3a3c,#1c1c1e)";
  $("#sheetName").textContent = g.name;

  const rows = [
    ["平台", state.labels[g.platform] || g.platform],
    ["体积", fmtSize(g.size_bytes) || "未知"],
    ["最近游玩", fmtDate(g.last_played)],
    ["启动次数", g.play_count ? g.play_count + " 次" : "从未"],
    ["安装位置", g.install_dir || "—"],
  ];
  $("#sheetMeta").innerHTML = rows
    .map(([k, v]) => `<div><b>${esc(k)}</b>　${esc(v)}</div>`).join("");

  $("#sheetFav").classList.toggle("act-primary", g.favorite);
  $("#sheetMask").hidden = false;
  $("#sheet").hidden = false;
}

function closeSheet() {
  $("#sheet").hidden = true;
  $("#sheetMask").hidden = true;
  state.current = null;
}

/* ---------------------------------------------------------------- 事件 */

function bind() {
  // 平台导航
  $("#platformNav").addEventListener("click", e => {
    const item = e.target.closest(".nav-item");
    if (!item) return;
    state.platform = item.dataset.platform;
    renderAll();
  });

  // 搜索
  let searchTimer = null;
  $("#searchInput").addEventListener("input", e => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.query = e.target.value;
      renderTitles();
      renderGrid();
    }, 130);
  });

  // 排序
  $("#sortSeg").addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b) return;
    $$("#sortSeg button").forEach(x => x.classList.remove("is-active"));
    b.classList.add("is-active");
    state.sort = b.dataset.sort;
    renderGrid();
  });

  // 卡片交互
  $("#grid").addEventListener("click", e => {
    const card = e.target.closest(".card");
    if (!card) return;
    const g = state.games.find(x => x.id === card.dataset.id);
    if (!g) return;

    if (e.target.closest(".card-fav")) { toggleFav(g); return; }
    launch(g);
  });

  $("#grid").addEventListener("contextmenu", e => {
    const card = e.target.closest(".card");
    if (!card) return;
    e.preventDefault();
    const g = state.games.find(x => x.id === card.dataset.id);
    if (g) openSheet(g);
  });

  // 双击卡片 = 打开详情
  $("#grid").addEventListener("dblclick", e => {
    const card = e.target.closest(".card");
    if (!card) return;
    const g = state.games.find(x => x.id === card.dataset.id);
    if (g) openSheet(g);
  });

  // 详情面板
  $("#sheetMask").addEventListener("click", closeSheet);
  $("#sheetLaunch").addEventListener("click", () => {
    if (state.current) { launch(state.current); closeSheet(); }
  });
  $("#sheetFav").addEventListener("click", () => {
    if (state.current) { toggleFav(state.current); openSheet(state.current); }
  });
  $("#sheetFolder").addEventListener("click", async () => {
    if (!state.current) return;
    const r = await api(`/api/open-folder/${state.current.id}`, { method: "POST" });
    toast(r.message || (r.ok ? "已打开" : "目录不存在"));
  });
  $("#sheetRemove").addEventListener("click", async () => {
    if (!state.current) return;
    const g = state.current;
    await api(`/api/game/${g.id}`, { method: "DELETE" });
    state.games = state.games.filter(x => x.id !== g.id);
    closeSheet();
    renderAll();
    toast("已从库中移除");
  });

  // 扫描 / 设置
  $("#btnScan").addEventListener("click", startScan);
  $("#btnSettings").addEventListener("click", openSettings);
  $("#setCancel").addEventListener("click", closeSettings);
  $("#setMask").addEventListener("click", closeSettings);
  $("#setSave").addEventListener("click", saveSettings);
  $("#btnTestSgdb").addEventListener("click", testSgdb);
  $("#btnApplist").addEventListener("click", downloadApplist);
  $("#btnExit").addEventListener("click", exitApp);
  // 开机自启动开关：改动即生效，不必等「保存」
  $("#autostartChk").addEventListener("change", toggleAutostart);

  // 键盘
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") { closeSheet(); closeSettings(); }
    if ((e.ctrlKey || e.metaKey) && e.key === "f") {
      e.preventDefault(); $("#searchInput").focus();
    }
  });
}

async function toggleFav(g) {
  const r = await api(`/api/favorite/${g.id}`, { method: "POST" });
  if (r.ok) {
    g.favorite = r.favorite;
    renderAll();
    toast(r.favorite ? "已加入收藏" : "已取消收藏");
  }
}

async function openSettings() {
  const cfg = await api("/api/config");
  $("#sgdbKey").value = cfg.steamgriddb_key || "";
  $("#standaloneChk").checked = cfg.include_standalone !== false;
  $("#localChk").checked = cfg.include_local !== false;
  $("#localRoots").value = (cfg.local_roots || []).join("\n");
  resetSgdbHint();
  refreshApplistStatus();
  refreshAutostartStatus();
  $("#setMask").hidden = false;
  $("#settingsPanel").hidden = false;
}

/** 读取开机自启动当前状态并回填开关。失败不影响设置面板其它部分。 */
async function refreshAutostartStatus() {
  try {
    const r = await api("/api/autostart");
    if (r && r.ok !== false) $("#autostartChk").checked = !!r.enabled;
  } catch (e) {
    $("#autostartChk").checked = false;
  }
}

/** 切换开机自启动。立即生效，失败则把开关拨回去。 */
async function toggleAutostart() {
  const chk = $("#autostartChk");
  const on = chk.checked;
  const r = await api("/api/autostart", {
    method: "POST",
    body: JSON.stringify({ enabled: on }),
  });
  if (r && r.ok) {
    chk.checked = !!r.enabled;
    toast(r.message || (on ? "已开启开机自启动" : "已关闭开机自启动"));
  } else {
    chk.checked = !on;
    toast((r && r.message) || "设置失败");
  }
}

/** 完全退出：结束进程、移除托盘图标、停止服务。 */
async function exitApp() {
  if (!confirm("确定要完全退出 GameHub 吗？\n\n退出后将停止后台服务并移除托盘图标。")) return;
  const btn = $("#btnExit");
  btn.classList.add("is-busy");
  btn.textContent = "正在退出…";
  try {
    await api("/api/exit", { method: "POST" });
  } catch (e) { /* 服务已停，请求失败属预期 */ }
  // 服务停掉后页面必然失效，给出提示即可
  toast("GameHub 已退出");
}

function closeSettings() {
  $("#settingsPanel").hidden = true;
  $("#setMask").hidden = true;
}

function resetSgdbHint() {
  const h = $("#sgdbHint");
  h.className = "field-hint";
  h.innerHTML = `在 steamgriddb.com → 个人设置里免费申请。这是专门给「非 Steam 游戏」提供封面的社区图库，
    填上之后 Epic / 本地 / 独立游戏也能拿到正规海报，而且不依赖 Steam 商店接口。`;
}

function setHint(el, cls, text) {
  el.className = "field-hint " + cls;
  el.textContent = text;
}

/** 测试 SteamGridDB Key 是否可用 */
async function testSgdb() {
  const btn = $("#btnTestSgdb");
  const hint = $("#sgdbHint");
  const key = $("#sgdbKey").value.trim();
  if (!key) {
    setHint(hint, "is-bad", "请先填入 API Key");
    return;
  }
  btn.classList.add("is-busy");
  btn.textContent = "测试中…";
  let r;
  try {
    r = await api("/api/test-sgdb", {
      method: "POST",
      body: JSON.stringify({ key }),
    });
  } catch (e) {
    r = { ok: false, message: "请求失败" };
  }
  btn.classList.remove("is-busy");
  btn.textContent = "测试连接";
  btn.classList.toggle("is-ok", !!r.ok);
  btn.classList.toggle("is-bad", !r.ok);
  setHint(hint, r.ok ? "is-ok" : "is-bad", (r.ok ? "✓ " : "✕ ") + (r.message || ""));
}

/** 查询离线全量表状态 */
async function refreshApplistStatus() {
  const hint = $("#applistHint");
  let s;
  try {
    s = await api("/api/applist/status");
  } catch (e) {
    setHint(hint, "", "状态未知");
    return;
  }
  if (s.busy) {
    setHint(hint, "is-busy", s.message || "正在下载…");
    setTimeout(refreshApplistStatus, 1500);
    return;
  }
  $("#btnApplist").classList.remove("is-busy");
  if (!s.available) {
    setHint(hint, "", s.message || "尚未下载（离线匹配不可用）");
  } else {
    const n = s.count > 0 ? `${s.count.toLocaleString()} 条` : "已缓存";
    setHint(hint, "is-ok",
      `✓ 可用 · ${n} · ${s.size_mb} MB · ${s.age_days} 天前${s.message ? " · " + s.message : ""}`);
  }
}

async function downloadApplist() {
  const btn = $("#btnApplist");
  btn.classList.add("is-busy");
  btn.textContent = "下载中…";
  await api("/api/applist/download", { method: "POST" });
  setTimeout(refreshApplistStatus, 800);
}

async function saveSettings() {
  const roots = $("#localRoots").value
    .split("\n")
    .map(s => s.trim())
    .filter(Boolean);
  await api("/api/config", {
    method: "POST",
    body: JSON.stringify({
      steamgriddb_key: $("#sgdbKey").value.trim(),
      include_standalone: $("#standaloneChk").checked,
      include_local: $("#localChk").checked,
      local_roots: roots,
    }),
  });
  closeSettings();
  toast("设置已保存，重新扫描后生效");
}

/* ---------------------------------------------------------------- 启动 */

(async function init() {
  bind();
  await loadMeta();
  await loadGames();

  // 若库为空则触发一次扫描
  const st = await api("/api/scan/status");
  if (!state.games.length || st.scanning) pollScan();
})();
