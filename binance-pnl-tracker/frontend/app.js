const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); }

// Same-origin API since FastAPI serves this frontend directly.
const API_BASE = "";

function fmt(n) {
  const sign = n > 0 ? "+" : "";
  return sign + n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function pnlClass(n) {
  return n > 0 ? "gain" : n < 0 ? "loss" : "";
}

async function api(path, opts) {
  const headers = { "Content-Type": "application/json" };
  if (tg?.initData) headers["X-Telegram-Init-Data"] = tg.initData;
  const res = await fetch(API_BASE + path, { ...opts, headers });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function drawSparkline(canvas, series) {
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = w * dpr; canvas.height = h * dpr;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  if (series.length < 2) return;

  let running = 0;
  const cumulative = series.map(p => (running += p.pnl));
  const min = Math.min(...cumulative, 0);
  const max = Math.max(...cumulative, 0);
  const range = (max - min) || 1;

  ctx.beginPath();
  cumulative.forEach((v, i) => {
    const x = (i / (cumulative.length - 1)) * w;
    const y = h - ((v - min) / range) * h;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.strokeStyle = cumulative.at(-1) >= 0 ? "#4FA490" : "#C1595A";
  ctx.lineWidth = 2;
  ctx.stroke();
}

function renderCostRows(container, spot, fut) {
  const spotFees = Object.values(spot).reduce((a, v) => a + v.fees, 0);
  const futCommission = Object.values(fut).reduce((a, v) => a - v.commission, 0); // stored negative
  const futFunding = Object.values(fut).reduce((a, v) => a + v.funding, 0);
  const rows = [
    ["Spot trading fees", -spotFees],
    ["Futures commissions", -futCommission],
    ["Futures funding paid/earned", futFunding],
  ];
  container.innerHTML = rows.map(([label, val]) => `
    <div class="cost-row">
      <span class="label">${label}</span>
      <span class="value ${pnlClass(val)} mono">${fmt(val)}</span>
    </div>
  `).join("");
}

function renderAssets(container, spot, fut) {
  const rows = [];
  for (const [asset, v] of Object.entries(spot)) {
    const net = v.realized - v.fees;
    if (v.trades === 0) continue;
    rows.push({ symbol: asset + " (spot)", net, meta: `${v.trades} trades · open ${v.open_qty.toFixed(4)}` });
  }
  for (const [symbol, v] of Object.entries(fut)) {
    const net = v.realized_pnl + v.commission + v.funding;
    rows.push({ symbol: symbol + " (fut)", net, meta: `funding ${fmt(v.funding)}` });
  }
  rows.sort((a, b) => Math.abs(b.net) - Math.abs(a.net));

  if (!rows.length) {
    container.innerHTML = `<div class="empty-state">No trades synced yet. Hit "Sync latest trades" on Overview.</div>`;
    return;
  }
  container.innerHTML = rows.map(r => `
    <div class="asset-row">
      <div>
        <div class="symbol">${r.symbol}</div>
        <div class="meta">${r.meta}</div>
      </div>
      <div class="right">
        <div class="pnl ${pnlClass(r.net)}">${fmt(r.net)}</div>
      </div>
    </div>
  `).join("");
}

function renderHistory(container, series) {
  if (!series.length) {
    container.innerHTML = `<div class="empty-state">No history yet.</div>`;
    return;
  }
  container.innerHTML = series.slice().reverse().map(d => `
    <div class="history-row">
      <span class="date">${d.date}</span>
      <span class="pnl ${pnlClass(d.pnl)}">${fmt(d.pnl)}</span>
    </div>
  `).join("");
}

async function loadAll() {
  try {
    const [summary, history] = await Promise.all([
      api("/api/pnl/summary"),
      api("/api/pnl/history"),
    ]);

    const totalEl = document.getElementById("totalPnl");
    totalEl.textContent = fmt(summary.total_net);
    totalEl.className = "total-pnl " + pnlClass(summary.total_net);

    const spotEl = document.getElementById("spotNet");
    spotEl.textContent = fmt(summary.spot_realized_net);
    spotEl.className = "stat-value " + pnlClass(summary.spot_realized_net);

    const futEl = document.getElementById("futNet");
    futEl.textContent = fmt(summary.futures_realized_net);
    futEl.className = "stat-value " + pnlClass(summary.futures_realized_net);

    renderCostRows(document.getElementById("costRows"), summary.spot_by_asset, summary.futures_by_symbol);
    renderAssets(document.getElementById("assetList"), summary.spot_by_asset, summary.futures_by_symbol);
    renderHistory(document.getElementById("historyList"), history);
    drawSparkline(document.getElementById("sparkline"), history);
  } catch (e) {
    document.getElementById("syncStatus").textContent = "Couldn't load data — try syncing.";
    console.error(e);
  }
}

document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
  });
});

document.getElementById("syncBtn").addEventListener("click", async () => {
  const status = document.getElementById("syncStatus");
  status.textContent = "Syncing…";
  try {
    const [s, f] = await Promise.all([
      api("/api/sync/spot", { method: "POST" }),
      api("/api/sync/futures", { method: "POST" }),
    ]);
    status.textContent = `Synced. +${s.added} spot, +${f.added} futures trades.`;
    await loadAll();
  } catch (e) {
    status.textContent = "Sync failed — try 'Discover all coins' first if this is your first sync.";
    console.error(e);
  }
});

let discoverPoll = null;

document.getElementById("discoverBtn").addEventListener("click", async () => {
  const status = document.getElementById("syncStatus");
  try {
    const res = await api("/api/sync/discover", { method: "POST" });
    if (!res.started) {
      status.textContent = res.message;
      return;
    }
    status.textContent = "Scanning the whole exchange for coins you've traded — this can take a few minutes…";
    if (discoverPoll) clearInterval(discoverPoll);
    discoverPoll = setInterval(pollDiscoverStatus, 2000);
  } catch (e) {
    status.textContent = "Couldn't start discovery — check server logs.";
    console.error(e);
  }
});

async function pollDiscoverStatus() {
  const status = document.getElementById("syncStatus");
  try {
    const s = await api("/api/sync/discover/status");
    if (s.total) {
      status.textContent = `Scanning… ${s.scanned}/${s.total} symbols checked, ${s.found} with trades found.`;
    } else {
      status.textContent = s.message;
    }
    if (!s.running) {
      clearInterval(discoverPoll);
      discoverPoll = null;
      status.textContent = s.message;
      await loadAll();
    }
  } catch (e) {
    console.error(e);
  }
}

loadAll();
