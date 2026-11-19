"use strict";

const PIZZA_TX = "a1075db55d416d3ca199f55b6084e2115b9345e16c5cf302fc80e9d5fbf5d48d";
const EXPLORER = "https://mempool.space";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const btc = (v) => `${Number(v).toLocaleString(undefined, { maximumFractionDigits: 8 })} BTC`;
const short = (s) => (s.length > 20 ? `${s.slice(0, 10)}…${s.slice(-8)}` : s);

let cy = null;
let lastRequest = null;
let lastData = null;

if (window.cytoscapeDagre) cytoscape.use(window.cytoscapeDagre);

function riskColor(level) {
  return css(`--risk-${level || "low"}`);
}

function graphStyle() {
  return [
    { selector: "node", style: { "font-size": 9, color: css("--text"), "text-valign": "bottom", "text-margin-y": 4,
        "text-outline-width": 2, "text-outline-color": css("--bg"), "min-zoomed-font-size": 7 } },
    { selector: 'node[kind="address"]', style: {
        shape: "ellipse", width: 18, height: 18, label: "data(label)",
        "background-color": (n) => riskColor(n.data("risk_level")) } },
    { selector: 'node[kind="address"][?entity]', style: { width: 28, height: 28, "font-weight": 700, "font-size": 10 } },
    { selector: 'node[kind="tx"]', style: {
        shape: "round-rectangle", width: 30, height: 14, label: "data(label)", "font-size": 8,
        color: css("--muted"), "background-color": css("--tx") } },
    { selector: 'node[kind="tx"][?coinjoin]', style: { "background-color": css("--coinjoin") } },
    { selector: 'node[kind="tx"][?stopped]', style: { "border-width": 2, "border-style": "dashed", "border-color": css("--risk-high") } },
    { selector: 'node[kind="tx"][?peel]', style: { "border-width": 2, "border-color": css("--risk-medium") } },
    { selector: 'node[kind="overflow"]', style: { shape: "round-rectangle", width: 60, height: 16, label: "data(label)",
        "font-size": 8, "text-valign": "center", "text-margin-y": 0, "background-color": css("--panel"),
        "border-width": 1, "border-color": css("--border"), color: css("--muted") } },
    { selector: "node[?seed]", style: { "border-width": 4, "border-color": css("--accent") } },
    { selector: "edge", style: {
        width: (e) => Math.max(1, Math.min(8, 1 + Math.log10(1 + e.data("btc") * 10) * 2)),
        "line-color": css("--edge"), "target-arrow-color": css("--edge"), "target-arrow-shape": "triangle",
        "arrow-scale": 0.7, "curve-style": "bezier", opacity: 0.85 } },
    { selector: 'edge[kind="output"]', style: { "line-color": css("--edge-out"), "target-arrow-color": css("--edge-out") } },
    { selector: "edge[?change]", style: { "line-style": "dashed" } },
    { selector: ".faded", style: { opacity: 0.12 } },
    { selector: ":selected", style: { "overlay-color": css("--accent"), "overlay-opacity": 0.25, "overlay-padding": 6 } },
  ];
}

function render(data) {
  $("empty").hidden = true;
  $("legend").hidden = false;
  if (cy) cy.destroy();
  cy = cytoscape({
    container: $("cy"),
    elements: data.elements,
    style: graphStyle(),
    wheelSensitivity: 0.25,
    minZoom: 0.05,
    maxZoom: 4,
    layout: window.cytoscapeDagre
      ? { name: "dagre", rankDir: "LR", nodeSep: 10, rankSep: 90, padding: 20, animate: false }
      : { name: "breadthfirst", directed: true, spacingFactor: 0.9 },
  });
  cy.on("tap", "node", (evt) => select(evt.target));
  cy.on("tap", (evt) => { if (evt.target === cy) clearHighlight(); });
  const seed = cy.nodes("[?seed]");
  if (seed.nonempty()) select(seed[0], false);
}

function clearHighlight() {
  if (cy) cy.elements().removeClass("faded");
}

function highlight(collection) {
  cy.elements().addClass("faded");
  collection.removeClass("faded");
}

function select(node, center = true) {
  cy.elements().unselect();
  node.select();
  if (center) {
    cy.animate({ center: { eles: node }, duration: 250 });
    highlight(node.closedNeighborhood());
  }
  const d = node.data();
  $("details-card").hidden = false;
  if (d.kind === "address") showAddress(d);
  else if (d.kind === "tx") showTx(d);
  else { $("details-title").textContent = "Collapsed"; $("details").innerHTML = `<p>${esc(d.label)} totalling ${btc(d.btc)} not shown.</p>`; }
}

function showAddress(d) {
  $("details-title").textContent = d.entity ? d.entity.name : "Address";
  const reasons = d.risk_reasons.length
    ? `<ul class="reasons">${d.risk_reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`
    : `<p class="mono" style="color:var(--muted)">No risk indicators in this graph.</p>`;
  const members = d.cluster && lastData ? (lastData.clusters[d.cluster] || []).length : 0;
  $("details").innerHTML = `
    <dl class="kv">
      <dt>Address</dt><dd class="mono">${esc(d.address)}</dd>
      <dt>Risk</dt><dd><span class="badge ${esc(d.risk_level)}">${d.risk}</span> ${esc(d.risk_level)}</dd>
      ${d.entity ? `<dt>Entity</dt><dd>${esc(d.entity.name)} <span class="tag warn">${esc(d.entity.category)}</span><br><small>${esc(d.entity.source)}</small></dd>` : ""}
      ${d.cluster ? `<dt>Cluster</dt><dd>${esc(d.cluster)} · ${members} addresses (common-input ownership)</dd>` : ""}
      ${d.terminal ? `<dt>Trace</dt><dd>Stopped here: known entity</dd>` : ""}
    </dl>
    <h2>Why this score</h2>${reasons}
    <div class="actions">
      ${d.cluster ? `<button type="button" id="hl-cluster">Highlight cluster</button>` : ""}
      <button type="button" id="retrace">Trace from here</button>
      <a href="${EXPLORER}/address/${encodeURIComponent(d.address)}" target="_blank" rel="noopener">View on explorer ↗</a>
    </div>`;
  if (d.cluster) $("hl-cluster").onclick = () => {
    const ids = new Set(lastData.clusters[d.cluster]);
    highlight(cy.nodes().filter((n) => ids.has(n.id())));
  };
  $("retrace").onclick = () => { $("seed").value = d.address; runTrace(); };
}

function showTx(d) {
  $("details-title").textContent = "Transaction";
  const when = d.block_time ? new Date(d.block_time * 1000).toISOString().replace("T", " ").slice(0, 19) + " UTC" : "unconfirmed";
  const tags = [
    d.seed && "seed", d.coinjoin && "CoinJoin", d.peel && "peel chain",
    d.hop > 0 && `forward hop ${d.hop}`, d.hop < 0 && `backward hop ${-d.hop}`,
  ].filter(Boolean).map((t) => `<span class="tag">${esc(t)}</span>`).join("");
  const change = d.change
    ? `<h2>Probable change</h2><p class="mono">${esc(d.change.address || "")} · confidence ${d.change.confidence}</p>
       <ul class="reasons">${d.change.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : "";
  $("details").innerHTML = `
    <div>${tags}</div>
    <dl class="kv">
      <dt>Txid</dt><dd class="mono">${esc(d.txid)}</dd>
      <dt>Time</dt><dd>${esc(when)}${d.block_height ? ` · block ${d.block_height}` : ""}</dd>
      <dt>Structure</dt><dd>${d.n_in} inputs → ${d.n_out} outputs</dd>
      <dt>Total out</dt><dd>${btc(d.btc_out)}</dd>
      <dt>Fee</dt><dd>${Number(d.fee).toLocaleString()} sats</dd>
      ${d.stopped ? `<dt>Not expanded</dt><dd>${esc(d.stopped)}</dd>` : ""}
    </dl>
    ${change}
    <div class="actions">
      <button type="button" id="retrace">Trace from here</button>
      <a href="${EXPLORER}/tx/${encodeURIComponent(d.txid)}" target="_blank" rel="noopener">View on explorer ↗</a>
    </div>`;
  $("retrace").onclick = () => { $("seed").value = d.txid; runTrace(); };
}

function showSummary(data) {
  $("summary-card").hidden = false;
  const s = data.stats;
  $("stats").innerHTML = [["Txs", s.transactions], ["Addresses", s.addresses], ["Clusters", s.clusters], ["CoinJoins", s.coinjoins]]
    .map(([k, v]) => `<div class="stat"><b>${v}</b><span>${k}</span></div>`).join("");
  $("findings").innerHTML = data.findings.map((f) => `<li>${esc(f)}</li>`).join("");
  $("warnings").innerHTML = data.warnings.map((w) => `<li>${esc(w)}</li>`).join("");

  $("risk-card").hidden = data.top_risk.length === 0;
  $("risk-list").innerHTML = data.top_risk.map((r) =>
    `<li data-id="${esc(r.address)}"><span class="badge ${esc(r.level)}">${r.score}</span><span class="mono">${esc(short(r.address))}</span></li>`).join("");
  $("risk-list").querySelectorAll("li").forEach((li) => {
    li.onclick = () => { const n = cy.getElementById(li.dataset.id); if (n.nonempty()) select(n); };
  });
}

function showError(msg) {
  const el = $("error");
  el.hidden = !msg;
  el.textContent = msg || "";
}

function currentRequest() {
  return {
    seed: $("seed").value.trim(),
    direction: $("direction").value,
    max_hops: Number($("hops").value),
    max_txs: Number($("maxtxs").value),
  };
}

async function post(path, body) {
  const resp = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const json = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = Array.isArray(json.detail) ? json.detail.map((d) => d.msg).join("; ") : json.detail;
    throw new Error(detail || `Request failed (HTTP ${resp.status})`);
  }
  return json;
}

async function runTrace() {
  const req = currentRequest();
  if (!req.seed) return;
  showError(null);
  $("loading").hidden = false;
  $("trace-btn").disabled = true;
  try {
    const data = await post("/api/trace", req);
    lastRequest = req;
    lastData = data;
    render(data);
    showSummary(data);
    $("report-btn").disabled = false;
    history.replaceState(null, "", `#${req.direction}/${req.max_hops}/${req.seed}`);
  } catch (e) {
    showError(e.message);
  } finally {
    $("loading").hidden = true;
    $("trace-btn").disabled = false;
  }
}

async function exportReport() {
  if (!lastRequest) return;
  const caseName = prompt("Case name for the report (optional):", "") ?? "";
  $("report-btn").disabled = true;
  try {
    const rep = await post("/api/report", { ...lastRequest, case_name: caseName });
    const blob = new Blob([JSON.stringify(rep, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `chaintrace-${lastRequest.seed.slice(0, 16)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) {
    showError(e.message);
  } finally {
    $("report-btn").disabled = false;
  }
}

$("trace-form").addEventListener("submit", (e) => { e.preventDefault(); runTrace(); });
$("report-btn").addEventListener("click", exportReport);
$("demo-btn").addEventListener("click", () => {
  $("seed").value = PIZZA_TX; $("direction").value = "forward"; $("hops").value = 3; runTrace();
});
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (cy) cy.style(graphStyle()); });

// Deep links: #direction/hops/seed
const m = location.hash.match(/^#(forward|backward|both)\/(\d)\/([A-Za-z0-9]{26,100})$/);
if (m) { $("direction").value = m[1]; $("hops").value = m[2]; $("seed").value = m[3]; runTrace(); }
