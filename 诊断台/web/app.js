/* 诊断台 · 前端逻辑（原生 JS，无依赖） */
"use strict";

const state = {
  view: "overview",
  filters: { industry: "", status: "", source: "", q: "" },
  customers: [],
  sel: null,        // 选中的周度复盘客户
  report: null,
  weeks: [],
  compareSel: new Set(),
  reportWeek: null,
  mt: 0.10, st: 0.15,
};

const UP_BAD = new Set(["CPM", "CPC", "open_cost", "lead_cost"]); // 涨=不利
const UP_GOOD = new Set(["CTR", "button_rate", "open_rate", "lead_rate", "lead_cvr", "lead_cnt", "open_msg", "impressions", "note_clicks", "button_clicks"]);

/* ---------------- 基础工具 ---------------- */
async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}
function fmtMoney(x) { return x == null ? "—" : "¥" + Math.round(x).toLocaleString("zh-CN"); }
function fmtNum(x) { return x == null ? "—" : Math.round(x).toLocaleString("zh-CN"); }
function fmtPct(x, d = 1) { return x == null ? "—" : (x >= 0 ? "+" : "") + (x * 100).toFixed(d) + "%"; }
function dirClass(metric, change) {
  if (change == null) return "flat";
  if (Math.abs(change) < 0.005) return "flat";
  const good = UP_GOOD.has(metric), bad = UP_BAD.has(metric);
  if (good) return change > 0 ? "up" : "down";   // up=红(强调) down=绿
  if (bad) return change > 0 ? "up" : "down";
  return change > 0 ? "up" : "down";
}
function statusBadge(s) {
  const map = { "正常": "normal", "需关注": "watch", "需行动": "act" };
  const cls = map[s] || "normal";
  return `<span class="badge ${cls}">${s || "—"}</span>`;
}
function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.classList.add("show");
  clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove("show"), 2200);
}
function el(html) { const d = document.createElement("div"); d.innerHTML = html; return d.firstElementChild; }

/* ---------------- 路由 / 视图切换 ---------------- */
function setView(v) {
  state.view = v;
  document.querySelectorAll(".nav-item").forEach(n => n.classList.toggle("active", n.dataset.view === v));
  const titles = { overview: "数据总览", review: "周度复盘", ingest: "手动录入" };
  document.getElementById("crumbs").textContent = titles[v];
  if (v === "overview") renderOverview();
  else if (v === "review") renderReview();
  else renderIngest();
}

async function loadCustomers() {
  const f = state.filters;
  const qs = new URLSearchParams();
  if (f.industry) qs.set("industry", f.industry);
  if (f.status) qs.set("status", f.status);
  if (f.source) qs.set("source", f.source);
  if (f.q) qs.set("q", f.q);
  state.customers = await api("/api/customers?" + qs.toString());
}

function filterSubhead() {
  return `<div class="subhead">
    <select id="fIndustry"><option value="">全部行业</option></select>
    <select id="fStatus">
      <option value="">全部状态</option><option value="active">active</option><option value="paused">paused</option>
    </select>
    <select id="fSource">
      <option value="">全部来源</option><option value="sim">sim（模拟）</option><option value="upload">upload（上传）</option>
    </select>
    <input type="text" id="fQ" placeholder="搜索客户名…" value="${state.filters.q}">
    <span class="grow"></span>
    <button class="btn sm" id="fApply">筛选</button>
    <span class="num" style="color:var(--muted)">共 <b id="cCount">0</b> 个客户</span>
  </div>`;
}
function bindSubhead() {
  const ind = document.getElementById("fIndustry");
  // 行业下拉来自当前客户集合
  const inds = [...new Set(state.customers.map(c => c.industry))].sort();
  inds.forEach(i => { const o = document.createElement("option"); o.value = i; o.textContent = i; if (i === state.filters.industry) o.selected = true; ind.appendChild(o); });
  document.getElementById("fStatus").value = state.filters.status;
  document.getElementById("fSource").value = state.filters.source;
  document.getElementById("fApply").onclick = async () => {
    state.filters.industry = ind.value;
    state.filters.status = document.getElementById("fStatus").value;
    state.filters.source = document.getElementById("fSource").value;
    state.filters.q = document.getElementById("fQ").value.trim();
    await loadCustomers();
    if (state.view === "overview") renderOverviewBody();
    else renderReviewBody();
  };
}

/* ---------------- 视图：数据总览 ---------------- */
async function renderOverview() {
  const c = document.getElementById("content");
  c.innerHTML = filterSubhead() + `<div id="ovBody"><div class="loading">加载中…</div></div>`;
  await loadCustomers();
  bindSubhead();
  renderOverviewBody();
}
function renderOverviewBody() {
  const list = state.customers;
  document.getElementById("cCount").textContent = list.length;
  const totSpend = list.reduce((s, c) => s + (c.kpi?.spend || 0), 0);
  const totLeads = list.reduce((s, c) => s + (c.kpi?.lead_cnt || 0), 0);
  const lc = list.filter(c => c.kpi?.lead_cost != null).map(c => c.kpi.lead_cost);
  const avgLeadCost = lc.length ? lc.reduce((a, b) => a + b, 0) / lc.length : null;
  const oc = list.filter(c => c.kpi?.open_cost != null).map(c => c.kpi.open_cost);
  const avgOpenCost = oc.length ? oc.reduce((a, b) => a + b, 0) / oc.length : null;
  const sc = list.filter(c => c.kpi?.spend_change != null).map(c => c.kpi.spend_change);
  const avgSpendChg = sc.length ? sc.reduce((a, b) => a + b, 0) / sc.length : null;

  // 掉量 / 增量 归因
  const sorted = [...list].filter(c => c.kpi?.spend_change != null).sort((a, b) => a.kpi.spend_change - b.kpi.spend_change);
  const drops = sorted.slice(0, 5);
  const rises = sorted.slice(-5).reverse();

  const html = `
  <div class="grid kpi-row">
    <div class="card kpi"><div class="title">本周总消耗</div><div class="val">${fmtMoney(totSpend)}</div>
      <div class="sub ${dirClass('spend', avgSpendChg)}">组合环比 ${fmtPct(avgSpendChg)}</div></div>
    <div class="card kpi"><div class="title">本周总留资</div><div class="val">${fmtNum(totLeads)}</div><div class="sub">${list.length} 个客户</div></div>
    <div class="card kpi"><div class="title">平均留资成本</div><div class="val">${fmtMoney(avgLeadCost)}</div><div class="sub">加权口径均值</div></div>
    <div class="card kpi"><div class="title">平均开口成本</div><div class="val">${fmtMoney(avgOpenCost)}</div><div class="sub">加权口径均值</div></div>
  </div>

  <div class="section-title">掉量 / 增量归因（本周消耗环比）</div>
  <div class="grid" style="grid-template-columns:1fr 1fr">
    <div class="card"><h4 style="margin:0 0 10px;color:var(--bad)">↓ 消耗回落 Top5</h4>${attrList(drops)}</div>
    <div class="card"><h4 style="margin:0 0 10px;color:var(--good)">↑ 消耗增长 Top5</h4>${attrList(rises)}</div>
  </div>

  <div class="section-title">客户监控（点击行进入周度复盘）</div>
  <div class="card" style="padding:0;overflow:auto">
    <table>
      <thead><tr><th>客户</th><th>行业 / 赛道</th><th class="num">本周消耗</th><th class="num">消耗环比</th>
        <th class="num">留资成本</th><th class="num">留资成本环比</th><th>状态</th></tr></thead>
      <tbody id="monBody"></tbody>
    </table>
  </div>`;
  document.getElementById("ovBody").innerHTML = html;
  const tb = document.getElementById("monBody");
  list.forEach(c => {
    const k = c.kpi || {};
    const st = quickStatus(k);
    const tr = el(`<tr class="rowclick">
      <td><b>${c.name}</b></td><td style="color:var(--muted)">${c.industry} / ${c.sector}</td>
      <td class="num">${fmtMoney(k.spend)}</td>
      <td class="num ${dirClass('spend', k.spend_change)}">${fmtPct(k.spend_change)}</td>
      <td class="num">${fmtMoney(k.lead_cost)}</td>
      <td class="num ${dirClass('lead_cost', k.lead_cost_change)}">${fmtPct(k.lead_cost_change)}</td>
      <td>${statusBadge(st)}</td></tr>`);
    tr.onclick = () => { state.sel = c.id; state.reportWeek = null; setView("review"); };
    tb.appendChild(tr);
  });
}
function attrList(arr) {
  if (!arr.length) return `<div class="empty">无数据</div>`;
  return arr.map(c => {
    const k = c.kpi || {};
    return `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border)">
      <b>${c.name}</b>
      <span class="num ${dirClass('spend', k.spend_change)}">${fmtPct(k.spend_change)} · 留资成本 ${fmtPct(k.lead_cost_change)}</span>
    </div>`;
  }).join("");
}
function quickStatus(k) {
  if (k.spend_change != null && k.spend_change <= -0.15) return "需行动";
  if ((k.lead_cost_change != null && k.lead_cost_change >= 0.2) || (k.spend_change != null && k.spend_change <= -0.05)) return "需关注";
  return "正常";
}

/* ---------------- 视图：周度复盘 ---------------- */
async function renderReview() {
  const c = document.getElementById("content");
  c.innerHTML = filterSubhead() + `<div id="rvBody"><div class="loading">加载中…</div></div>`;
  await loadCustomers();
  bindSubhead();
  renderReviewBody();
}
function renderReviewBody() {
  const list = state.customers;
  document.getElementById("cCount").textContent = list.length;
  const cards = list.map(c => {
    const k = c.kpi || {};
    const st = quickStatus(k);
    return `<div class="cust-card" data-id="${c.id}">
      <div class="name">${c.name} ${statusBadge(st)}</div>
      <div class="meta">${c.industry} / ${c.sector} · ${c.categories.join("、") || "—"}</div>
      <div class="metrics">
        <div class="m"><div class="t">本周消耗</div><div class="v">${fmtMoney(k.spend)}</div></div>
        <div class="m"><div class="t">消耗环比</div><div class="v ${dirClass('spend', k.spend_change)}">${fmtPct(k.spend_change)}</div></div>
        <div class="m"><div class="t">留资成本</div><div class="v">${fmtMoney(k.lead_cost)}</div></div>
        <div class="m"><div class="t">留资成本环比</div><div class="v ${dirClass('lead_cost', k.lead_cost_change)}">${fmtPct(k.lead_cost_change)}</div></div>
      </div></div>`;
  }).join("");
  document.getElementById("rvBody").innerHTML = `
    <div class="section-title">客户卡片（点击查看本周诊断报告）</div>
    <div class="grid review-wrap" id="cardGrid" style="grid-template-columns:1fr">${cards}</div>
    <div id="reportZone"></div>`;
  document.querySelectorAll(".cust-card").forEach(card => {
    card.onclick = () => openReport(parseInt(card.dataset.id));
  });
  if (state.sel) openReport(state.sel);
}

async function openReport(cid) {
  state.sel = cid;
  const zone = document.getElementById("reportZone");
  zone.innerHTML = `<div class="report-panel"><div class="loading">生成报告中…（离线确定性，秒级）</div></div>`;
  try {
    const rep = await api(`/api/report?customer_id=${cid}&metric_threshold=${state.mt}&spend_threshold=${state.st}` + (state.reportWeek ? `&week=${state.reportWeek}` : ""));
    if (rep.error) { zone.innerHTML = `<div class="report-panel empty">${rep.error}</div>`; return; }
    state.report = rep;
    state.reportWeek = rep.params?.week || null;
    renderReportPanel(rep);
  } catch (e) { zone.innerHTML = `<div class="report-panel empty">报告生成失败: ${e.message}</div>`; }
}

function renderReportPanel(rep) {
  const ch = rep.chapters;
  const c1 = ch["1_封面"], c2 = ch["2_核心结论"];
  const zone = document.getElementById("reportZone");
  zone.innerHTML = `
  <div class="section-title">诊断报告 · ${c1.customer}</div>
  <div class="report-panel">
    <div class="controls">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <b>交互控制</b><span style="color:var(--muted);font-size:12px">当前周期 ${c1.period}</span>
      </div>
      <label>异常指标阈值（metric_threshold）：<b id="mtV">${state.mt}</b></label>
      <input type="range" id="mtSlider" min="0.05" max="0.5" step="0.01" value="${state.mt}">
      <label>异常消耗阈值（spend_threshold）：<b id="stV">${state.st}</b></label>
      <input type="range" id="stSlider" min="0.05" max="0.5" step="0.01" value="${state.st}">
      <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn sm" id="recalc">改参数重算</button>
        <button class="btn sm ghost" id="genLink">复制报告链接</button>
        <button class="btn sm ghost" id="genNew">新标签打开</button>
      </div>
      <div style="margin-top:10px"><b style="font-size:12px;color:var(--muted)">对比多周（勾选后点对比）：</b><div id="weekChips"></div>
        <button class="btn sm ghost" id="doCompare" style="margin-top:6px">对比选中周</button></div>
    </div>
    <div id="compareZone"></div>
    ${renderChapters(rep)}
  </div>`;
  // 滑块
  const mtS = document.getElementById("mtSlider"), stS = document.getElementById("stSlider");
  mtS.oninput = () => { state.mt = parseFloat(mtS.value); document.getElementById("mtV").textContent = state.mt.toFixed(2); };
  stS.oninput = () => { state.st = parseFloat(stS.value); document.getElementById("stV").textContent = state.st.toFixed(2); };
  document.getElementById("recalc").onclick = () => openReport(state.sel);
  document.getElementById("genLink").onclick = () => copyReportLink();
  document.getElementById("genNew").onclick = () => window.open(reportHash(), "_blank");
  document.getElementById("doCompare").onclick = () => doCompare();
  loadWeekChips();
}

function renderChapters(rep) {
  const ch = rep.chapters;
  const c1 = ch["1_封面"], c2 = ch["2_核心结论"], c3 = ch["3_指标与趋势"];
  const c4 = ch["4_分层诊断"], c5 = ch["5_异常与原因"], c6 = ch["6_案例参考"];
  const c7 = ch["7_优化建议"], c8 = ch["8_行动计划"];
  let out = "";

  // 1 封面
  out += `<div class="chapter"><h4>① 封面</h4>
    <div class="kv">
      <span class="k">客户</span><span>${c1.customer}</span>
      <span class="k">行业 / 赛道</span><span>${c1.industry} / ${c1.sector}</span>
      <span class="k">品类</span><span>${c1.categories?.join("、") || "—"}</span>
      <span class="k">周期</span><span>${c1.period}</span>
      <span class="k">生成时间</span><span>${c1.generated_at}</span>
    </div></div>`;

  // 2 核心结论
  out += `<div class="chapter"><h4>② 核心结论</h4>
    <div style="margin-bottom:8px">整体状态：${statusBadge(c2.overall_status)}　数据状态：${c2.data_status}</div>
    <div style="background:var(--sky-light);border-radius:10px;padding:12px">${c2.summary || "（无摘要）"}</div>
    <div style="margin-top:10px"><b>Top3 异动：</b>${c2.top3?.length ? c2.top3.map(t =>
      `<div class="watch-item">· ${t.location}（${fmtPct(t.change)}，权重 ${t.weight?.toFixed ? t.weight.toFixed(3) : t.weight}）<span class="badge ${t.direction === 'negative' ? 'act' : 'normal'}">${t.direction === 'negative' ? '不利' : '向好'}</span></div>`).join("") : "无"}</div>
  </div>`;

  // 3 指标与趋势
  const mc = c3.metrics_cur, mp = c3.metrics_prev, mch = c3.metrics_change;
  const rows = ["spend", "CPM", "CTR", "CPC", "button_rate", "open_rate", "lead_rate", "lead_cvr", "open_cost", "lead_cost"];
  const isCost = m => ["CPM", "CPC", "open_cost", "lead_cost"].includes(m);
  let trows = rows.map(m => {
    const isMoney = m === "spend";
    const cv = isMoney ? fmtMoney(mc?.[m]) : fmtNum(mc?.[m]);
    const pv = isMoney ? fmtMoney(mp?.[m]) : fmtNum(mp?.[m]);
    const cc = mch?.[m];
    return `<tr><td>${m}</td><td class="num">${cv}</td><td class="num">${pv}</td>
      <td class="num ${dirClass(m, cc)}">${fmtPct(cc)}</td></tr>`;
  }).join("");
  const spark = sparkline(c3.trend_14d?.daily || []);
  out += `<div class="chapter"><h4>③ 指标与趋势</h4>
    <table><thead><tr><th>指标</th><th class="num">本期</th><th class="num">上期</th><th class="num">环比</th></tr></thead>
      <tbody>${trows}</tbody></table>
    <div style="margin-top:10px"><b>14 日目标成本趋势</b><br>${spark}</div>
  </div>`;

  // 4 分层诊断
  out += `<div class="chapter"><h4>④ 分层诊断</h4>` + (c4 || []).map(l =>
    `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border)">
      <b>${l.layer}</b><span>${statusBadge(l.status)} <span style="color:var(--muted)">${l.judgement || ""}</span></span></div>`).join("") + `</div>`;

  // 5 异常与原因
  let top3d = c5.top3_detail;
  if (typeof top3d === "string") top3d = [{ summary: top3d }];
  const top3html = Array.isArray(top3d) ? top3d.map(e =>
    `<div class="sugg"><b>${e.location || ""}</b> ${e.reason || e.summary || ""}
      ${(e.evidence || []).map(x => `<div style="color:var(--muted);font-size:12px">↳ ${x}</div>`).join("")}
      ${e.confidence ? `<span class="badge ${e.confidence === '高' ? 'act' : 'watch'}">置信 ${e.confidence}</span>` : ""}</div>`).join("")
    : JSON.stringify(top3d);
  const watch = c5.watchlist || [];
  out += `<div class="chapter"><h4>⑤ 异常与原因</h4>
    ${top3html}
    <div style="margin-top:8px"><b>观察清单：</b>${watch.length ? watch.map(w => `<div class="watch-item">· ${w.location}（${typeof w.change === 'object' ? JSON.stringify(w.change) : w.change}）</div>`).join("") : "无"}</div>
  </div>`;

  // 6 案例参考
  const c6ref = c6.refs || c6.cases || [];
  out += `<div class="chapter"><h4>⑥ 案例参考（SQL 匹配 RAG）</h4>` +
    (c6ref.length ? c6ref.map(r => `<div class="sugg">案例 #${r.case_id || r.id} · ${r.industry || ""}/${r.sector || ""}
      <div style="font-size:12px;color:var(--muted)">${r.action_taken || ""} → ${r.result_after || ""}</div></div>`).join("")
      : `<div class="empty">案例库暂无匹配（案例由审核通过的报告沉淀，演示时可手动扩充）</div>`) + `</div>`;

  // 7 优化建议
  out += `<div class="chapter"><h4>⑦ 优化建议</h4>` + (c7 || []).map(s =>
    `<div class="sugg"><span class="${s.priority === 'P0' ? 'p0' : 'p1'}">[${s.priority}]</span> ${s.text || ""}
      ${s.basis ? `<div style="color:var(--muted);font-size:12px">依据：${s.basis}</div>` : ""}
      ${s.risk ? `<div style="color:var(--warn);font-size:12px">风险：${s.risk}</div>` : ""}</div>`).join("") + `</div>`;

  // 8 行动计划
  const c8html = (c8 && c8.length)
    ? c8.map(a => `<div class="sugg">▸ ${a.action || ""}
        <div style="color:var(--muted);font-size:12px">日期：${a.date || "—"}　预期：${a.expect_metric || "—"}</div></div>`).join("")
    : `<div class="empty">本期无行动项（正常周）</div>`;
  out += `<div class="chapter"><h4>⑧ 行动计划</h4>${c8html}</div>`;
  return out;
}

function sparkline(daily) {
  if (!daily || !daily.length) return `<span style="color:var(--muted)">无趋势数据</span>`;
  const vals = daily.map(d => d.value).filter(v => v != null);
  if (!vals.length) return `<span style="color:var(--muted)">无趋势数据</span>`;
  const w = 260, h = 46, max = Math.max(...vals), min = Math.min(...vals);
  const rng = max - min || 1;
  const pts = daily.map((d, i) => {
    const x = (i / (daily.length - 1)) * w;
    const y = h - (( (d.value ?? min) - min) / rng) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return `<svg width="${w}" height="${h}" style="background:var(--sky-light);border-radius:8px">
    <polyline points="${pts}" fill="none" stroke="var(--sky)" stroke-width="2"/></svg>`;
}

async function loadWeekChips() {
  const cid = state.sel;
  const weeks = await api(`/api/weeks?customer_id=${cid}`);
  state.weeks = weeks;
  const box = document.getElementById("weekChips");
  if (!box) return;
  box.innerHTML = weeks.map(w => `<span class="chip ${w === state.reportWeek ? "on" : ""}" data-w="${w}">${w.slice(5)}</span>`).join("");
  box.querySelectorAll(".chip").forEach(ch => {
    ch.onclick = () => ch.classList.toggle("on");
  });
}
async function doCompare() {
  const cid = state.sel;
  const sel = state.weeks.filter(w => {
    const chip = document.querySelector(`#weekChips .chip[data-w="${w}"]`);
    return chip && chip.classList.contains("on");
  });
  const zone = document.getElementById("compareZone");
  if (sel.length < 1) { toast("请至少勾选一周"); return; }
  zone.innerHTML = `<div class="loading">对比中…</div>`;
  const data = await api(`/api/compare?customer_id=${cid}&weeks=${sel.join(",")}`);
  const rows = data.rows || [];
  const metrics = [
    ["spend", "消耗", true], ["lead_cnt", "留资数", false], ["lead_cost", "留资成本", false],
    ["open_cost", "开口成本", false], ["ctr", "CTR", false], ["cpm", "CPM", false],
  ];
  let html = `<div class="chapter"><h4>多周对比</h4><div style="overflow:auto"><table><thead><tr><th>指标</th>` +
    rows.map(r => `<th class="num">${r.week_label}</th>`).join("") + `</tr></thead><tbody>`;
  metrics.forEach(([m, label, isMoney]) => {
    html += `<tr><td>${label}</td>` + rows.map(r => {
      const v = r[m], ch = r[m + "_change"];
      const cv = isMoney ? fmtMoney(v) : fmtNum(v);
      return `<td class="num"><div>${cv}</div><div class="${dirClass(m, ch)}" style="font-size:11px">${fmtPct(ch)}</div></td>`;
    }).join("") + `</tr>`;
  });
  html += `</tbody></table></div></div>`;
  zone.innerHTML = html;
}

function reportHash() {
  const w = state.reportWeek || "";
  return `${location.pathname}#/review?customer=${state.sel}&week=${w}`;
}
function copyReportLink() {
  const link = location.origin + reportHash();
  navigator.clipboard?.writeText(link);
  toast("报告链接已复制：" + link);
}

/* ---------------- 视图：手动录入 ---------------- */
async function renderIngest() {
  const c = document.getElementById("content");
  c.innerHTML = `<div class="card" style="max-width:880px">
    <h3 style="margin-top:0">手动录入客户（接 ingest 后端，字段不变、只增数据行）</h3>
    <p class="note">用途：把你自己真实的客户投放数据贴进来，跑出一份真实诊断报告（演示"真产品"）。
      字段结构与 <code>data/sample_customer_upload.json</code> 一致。录入后以 <code>source=upload</code> 落库，与模拟数据并存、互不影响。</p>
    <div class="form-grid">
      <div class="field"><label>客户名称</label><input id="in_name" placeholder="如：示例上传客户_美妆个护"></div>
      <div class="field"><label>行业 / 赛道</label><input id="in_ind" placeholder="到综服务 / 美妆个护"></div>
      <div class="field"><label>品类（逗号分隔）</label><input id="in_cat" placeholder="护肤,彩妆"></div>
      <div class="field"><label>优化目标</label><select id="in_target"><option value="lead">lead（留资）</option><option value="open">open（开口）</option></select></div>
      <div class="field"><label>目标成本（元）</label><input id="in_cost" type="number" placeholder="80"></div>
    </div>
    <div style="margin-top:14px" class="field">
      <label>完整录入 JSON（客户 / plans / notes / daily_metrics，可点"填入示例"后修改）</label>
      <textarea id="in_json"></textarea>
    </div>
    <div style="margin-top:12px;display:flex;gap:10px">
      <button class="btn" id="in_submit">提交录入</button>
      <button class="btn ghost" id="in_sample">填入示例</button>
    </div>
    <div id="in_msg" class="note" style="display:none"></div>
  </div>`;
  document.getElementById("in_sample").onclick = fillSample;
  document.getElementById("in_submit").onclick = submitIngest;
  fillSample();
}
function buildSamplePayload() {
  const name = document.getElementById("in_name").value.trim() || "示例上传客户_美妆个护";
  const ind = document.getElementById("in_ind").value.trim() || "到综服务";
  const [sector, ...rest] = (document.getElementById("in_ind").value.trim() || "美妆个护").split("/");
  const sec = rest.join("/").trim() || "美妆个护";
  const cats = document.getElementById("in_cat").value.split(/[,，]/).map(s => s.trim()).filter(Boolean);
  const target = document.getElementById("in_target").value;
  const cost = parseFloat(document.getElementById("in_cost").value) || 80;
  return {
    "customer": { name, industry: ind, sector: sec, categories: cats.length ? cats : ["护肤"], optimize_target: target, target_cost: cost },
    "plans": [
      { "key": 0, "name": "信息流_主推", "category": cats[0] || "护肤", "placement": "feed", "created_date": "2026-08-01", "status": "在投", "daily_budget": 500 },
      { "key": 1, "name": "搜索_词包", "category": cats[0] || "护肤", "placement": "search", "created_date": "2026-08-05", "status": "在投", "daily_budget": 300 }
    ],
    "notes": [
      { "key": 0, "plan_key": 0, "category": cats[0] || "护肤", "title": "图文A", "material_form": "图文", "created_date": "2026-08-01", "status": "在投" },
      { "key": 1, "plan_key": 1, "category": cats[0] || "护肤", "title": "视频B", "material_form": "视频", "created_date": "2026-08-05", "status": "在投" }
    ],
    "daily_metrics": [
      { "plan_key": 0, "note_key": 0, "category": cats[0] || "护肤", "placement": "feed", "date": "2026-08-11", "spend": 480, "impressions": 12000, "note_clicks": 360, "button_clicks": 90, "open_msg": 12, "lead_cnt": 6 },
      { "plan_key": 1, "note_key": 1, "category": cats[0] || "护肤", "placement": "search", "date": "2026-08-11", "spend": 290, "impressions": 8000, "note_clicks": 240, "button_clicks": 70, "open_msg": 9, "lead_cnt": 5 }
    ]
  };
}
function fillSample() {
  document.getElementById("in_json").value = JSON.stringify(buildSamplePayload(), null, 2);
}
async function submitIngest() {
  const ta = document.getElementById("in_json");
  let payload;
  try { payload = JSON.parse(ta.value); }
  catch (e) { return showIngestMsg("JSON 解析失败：" + e.message, true); }
  try {
    const r = await fetch("/api/ingest", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const res = await r.json();
    if (res.error) return showIngestMsg("录入失败：" + res.error, true);
    showIngestMsg(`录入成功 ✓ 客户ID=${res.customer_id}，计划 ${res.plans} 个、笔记 ${res.notes} 个、日明细 ${res.daily_rows} 行。可去「周度复盘」筛选 source=upload 查看。`, false);
    await loadCustomers();
  } catch (e) { showIngestMsg("请求失败：" + e.message, true); }
}
function showIngestMsg(msg, isErr) {
  const m = document.getElementById("in_msg");
  m.style.display = "block";
  m.style.color = isErr ? "var(--bad)" : "var(--good)";
  m.textContent = msg;
}

/* ---------------- 启动 / 哈希路由 ---------------- */
async function boot() {
  document.getElementById("toggleSidebar").onclick = () => document.getElementById("app").classList.toggle("collapsed");
  document.querySelectorAll(".nav-item").forEach(n => n.onclick = () => setView(n.dataset.view));

  // 哈希深链：#/review?customer=ID&week=W
  const h = location.hash;
  if (h.startsWith("#/review")) {
    const p = new URLSearchParams(h.slice("#/review?".length));
    const cid = parseInt(p.get("customer"));
    const wk = p.get("week");
    if (cid) {
      await loadCustomers();
      state.sel = cid; state.reportWeek = wk && wk !== "null" ? wk : null;
      setView("review");
      return;
    }
  }
  setView("overview");
}
boot();
