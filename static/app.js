/* 闲鱼智能搜索助手 前端逻辑 */
const $ = (id) => document.getElementById(id);
let state = { items: [], source: null, sort: "value", keyword: "" };

/* ---------------- 登录状态 ---------------- */
async function refreshStatus() {
  try {
    const r = await fetch("/api/status");
    const s = await r.json();
    renderLogin(s);
    return s;
  } catch {
    renderLogin({ running: false, logged_in: false });
  }
}

function renderLogin(s) {
  const dot = $("login-dot"), text = $("login-text");
  if (s.logged_in) {
    dot.className = "status-dot ok";
    text.textContent = `已登录${s.nick ? " · " + s.nick : ""}，可以搜索了`;
    $("btn-stop").classList.remove("hidden");
  } else if (s.running) {
    dot.className = "status-dot warn";
    text.textContent = "浏览器已打开，等待登录…";
    $("btn-stop").classList.remove("hidden");
  } else {
    dot.className = "status-dot";
    text.textContent = "未启动";
    $("btn-stop").classList.add("hidden");
  }
}

let pollTimer = null;
function pollStatus() {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    const s = await refreshStatus();
    if (s && s.logged_in) clearInterval(pollTimer);
  }, 3000);
}

$("btn-login").onclick = async () => {
  $("btn-login").disabled = true;
  $("login-hint").textContent = "正在启动浏览器并打开闲鱼网页，请在弹出的窗口中完成登录…";
  try {
    const r = await fetch("/api/login/start", { method: "POST" });
    if (!r.ok) throw new Error((await r.json()).detail || "启动失败");
    pollStatus();
  } catch (e) {
    alert("打开闲鱼失败：" + e.message);
  } finally {
    $("btn-login").disabled = false;
  }
};

$("btn-refresh").onclick = refreshStatus;
$("btn-stop").onclick = async () => {
  await fetch("/api/login/stop", { method: "POST" });
  refreshStatus();
};

/* ---------------- 搜索 ---------------- */
$("btn-search").onclick = doSearch;
$("kw").addEventListener("keydown", (e) => e.key === "Enter" && doSearch());

async function doSearch() {
  const keyword = $("kw").value.trim();
  if (!keyword) { alert("请输入搜索关键词"); return; }
  state.keyword = keyword;
  const btn = $("btn-search");
  btn.disabled = true; btn.textContent = "搜索并分析中…（首次约 1-4 分钟）";
  $("results").classList.add("hidden");
  try {
    const r = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        keyword,
        city: $("city").value.trim(),
        district: $("district").value.trim(),
      }),
    });
    if (!r.ok) throw new Error((await r.json()).detail || "搜索失败");
    const data = await r.json();
    state.items = data.items;
    state.source = data.source;
    state.note = data.note;
    renderResults();
  } catch (e) {
    alert("搜索失败：" + e.message);
  } finally {
    btn.disabled = false; btn.textContent = "🔍 智能搜索";
  }
}

/* ---------------- 排序 ---------------- */
$("sort-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  document.querySelectorAll("#sort-tabs button").forEach((x) => x.classList.remove("active"));
  b.classList.add("active");
  state.sort = b.dataset.sort;
  renderCards();
});

function sortedItems() {
  const key = {
    value: (it) => -it.sort_value,
    price: (it) => it.sort_price,
    distance: (it) => it.sort_distance,
    condition: (it) => -it.sort_condition,
  }[state.sort];
  const active = state.items.filter((it) => !it.flags.stale);
  const stale = state.items.filter((it) => it.flags.stale);
  active.sort((a, b) => key(a) - key(b));
  stale.sort((a, b) => key(a) - key(b));
  return active.concat(stale);
}

/* ---------------- 渲染 ---------------- */
function renderResults() {
  $("results").classList.remove("hidden");
  $("result-count").textContent = `共 ${state.items.length} 件 · 「${state.keyword}」`;
  const badge = $("source-badge");
  if (state.source === "demo") {
    badge.textContent = "演示数据（真实抓取失败时自动降级）";
    badge.classList.add("demo");
  } else {
    badge.textContent = "闲鱼真实数据";
    badge.classList.remove("demo");
  }
  if (state.note) console.warn("降级原因：", state.note);
  renderCards();
  $("results").scrollIntoView({ behavior: "smooth" });
}

function starsText(n) {
  const full = Math.floor(n), half = n - full >= 0.5;
  return "★".repeat(full) + (half ? "⯨" : "") + "☆".repeat(5 - full - (half ? 1 : 0));
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function renderCards() {
  const list = $("card-list");
  list.innerHTML = "";
  for (const it of sortedItems()) {
    const f = it.flags || {};
    const seller = it.seller || {};
    const div = document.createElement("div");
    div.className = "card" + (f.stale ? " stale" : "");

    const img = it.image_url
      ? `<img class="thumb" src="${escapeHtml(it.image_url)}" alt="" onerror="this.outerHTML='<div class=thumb style=&quot;width:120px;height:120px;display:flex;align-items:center;justify-content:center;font-size:36px;background:#f5f5f5&quot;>🐟</div>'">`
      : `<div class="thumb" style="display:flex;align-items:center;justify-content:center;font-size:36px;background:#f5f5f5">🐟</div>`;

    const priceHtml = f.ask_price
      ? `<span class="price ask">私聊问价</span>`
      : `<span class="price">¥${it.price}</span>`;

    const badges = [];
    if (f.stale) badges.push(`<span class="tag stale-tag">超过 10 天未上线 · 不推荐</span>`);
    if (f.ask_price) badges.push(`<span class="tag warn">需私聊问价，未计入比价</span>`);
    if (f.merchant) badges.push(`<span class="tag warn">疑似商家卖家</span>`);
    if (f.maybe_merchant) badges.push(`<span class="tag warn">在售较多，可能半职业</span>`);
    if (f.personal) badges.push(`<span class="tag plus">个人卖家</span>`);
    if (f.condition) badges.push(`<span class="tag note">品相：${escapeHtml(f.condition)}</span>`);
    if (f.pickup && f.pickup.note) badges.push(`<span class="tag note">${escapeHtml(f.pickup.note)}</span>`);

    const reasons = (it.reasons || []).map((r) =>
      `<li class="${r.type}">${escapeHtml(r.text)}</li>`).join("");

    const meta = [];
    if (seller.nick) meta.push(`卖家：${escapeHtml(seller.nick)}`);
    if (seller.good_count != null && seller.bad_count != null)
      meta.push(`好评 ${seller.good_count} / 差评 ${seller.bad_count}`);
    if (seller.item_count != null) meta.push(`主页在售 ${seller.item_count} 件`);
    if (seller.last_online_days != null)
      meta.push(seller.last_online_days <= 0.5 ? "今天上线" : `${Math.round(seller.last_online_days)} 天前上线`);
    if (f.pickup && f.pickup.fee_note) meta.push(escapeHtml(f.pickup.fee_note));
    if (it.shipping_fee != null) meta.push(it.shipping_fee <= 0.5 ? "卖家包邮" : `运费约 ¥${it.shipping_fee}`);
    if (it.area) meta.push(`发布地：${escapeHtml(it.area)}`);

    div.innerHTML = `
      ${img}
      <div class="info">
        <div class="title">${escapeHtml(it.title)}</div>
        <div>${priceHtml}<span class="stars">${starsText(it.stars)}<span class="score">${it.stars.toFixed(1)}</span></span></div>
        <div class="badges">${badges.join("")}</div>
        <ul class="reasons">${reasons}</ul>
        <div class="meta">${meta.join("<span>·</span>")}</div>
      </div>
      <a class="go" href="${escapeHtml(it.url)}" target="_blank" rel="noopener">去闲鱼看看</a>`;
    list.appendChild(div);
  }
}

/* ---------------- 初始化 ---------------- */
refreshStatus();
