// Dashboard ORIONX (M9). Aucune bibliothèque externe. Règle : tout texte venant de l'API passe par textContent,
// jamais par une fonction qui interprète du HTML (un texte malveillant ne peut pas devenir du code). Session par cookie
// HttpOnly : ce script ne voit jamais le jeton ; chaque action envoie l'en-tête anti-CSRF.
"use strict";

const $ = (id) => document.getElementById(id);
const CSRF = { "X-Requested-By": "orionx-dashboard" };
const SVG = "http://www.w3.org/2000/svg";
const CRITICAL = new Set(["AccountChanged", "KillSwitchActivated", "CircuitBreakerTripped", "OrderFailed"]);
const IMPORTANT = new Set(["OrderFilled", "PositionClosed", "PositionAdjusted", "GatewayReconnected", "GatewayRecovered",
  "GatewayReconnectFailed", "DataStale", "DataFrozen", "RiskConfigChanged", "SignalApproval", "SignalRejectedByUser", "TradeRejected"]);
let sessionLost = false;
const equityHistory = []; // équité vue à chaque rafraîchissement (en mémoire seulement, perdue au rechargement)
const last = {};

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = String(text);
  if (cls) e.className = cls;
  return e;
}

function banner(message) {
  const b = $("banner");
  b.textContent = message || "";
  b.hidden = !message;
}

async function api(path, options = {}) {
  const r = await fetch(path, { credentials: "same-origin", ...options });
  if (r.status === 401) {
    sessionLost = true;
    banner("Session expirée ou agent redémarré : relancez « python -m orionx.main dashboard » dans un terminal.");
    throw new Error("401");
  }
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

const money = (v) => (v === null || v === undefined ? "—" : Number(v).toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const signed = (v) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${money(v)}`);
// arrondi à 5 décimales : évite l'affichage d'artefacts de calcul flottant (1.1587100000000001)
const price = (v) => (v === null || v === undefined ? "—" : String(Math.round(Number(v) * 1e5) / 1e5));
const time = (iso) => new Date(iso).toLocaleTimeString("fr-FR");

function pill(id, text, kind) {
  const p = $(id);
  const label = p.querySelector(".label");
  (label || p).textContent = text;
  p.className = `pill ${kind || ""}${id === "live" ? " pill-sm" : ""}`;
}

// Met à jour une valeur et la fait briller brièvement si elle a changé (vert : hausse, rouge : baisse).
function setValue(id, text, numeric) {
  const node = $(id);
  if (node.textContent === text) return;
  node.textContent = text;
  if (numeric !== undefined && last[id] !== undefined && numeric !== last[id]) {
    node.classList.remove("flash-up", "flash-down");
    void node.offsetWidth; // relance l'animation
    node.classList.add(numeric > last[id] ? "flash-up" : "flash-down");
  }
  if (numeric !== undefined) last[id] = numeric;
}

function drawSpark(values) {
  const svg = $("spark");
  svg.replaceChildren();
  if (values.length < 2) return;
  const defs = document.createElementNS(SVG, "defs");
  const grad = document.createElementNS(SVG, "linearGradient");
  grad.setAttribute("id", "spark-fill");
  grad.setAttribute("x1", "0"); grad.setAttribute("x2", "0"); grad.setAttribute("y1", "0"); grad.setAttribute("y2", "1");
  for (const [offset, opacity] of [["0", "0.35"], ["1", "0"]]) {
    const stop = document.createElementNS(SVG, "stop");
    stop.setAttribute("offset", offset);
    stop.setAttribute("stop-color", "#00d9f8");
    stop.setAttribute("stop-opacity", opacity);
    grad.append(stop);
  }
  defs.append(grad);
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const pts = values.map((v, i) => [(i / (values.length - 1)) * 300, 54 - ((v - min) / span) * 46]);
  const line = pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = document.createElementNS(SVG, "polygon");
  area.setAttribute("points", `0,60 ${line} 300,60`);
  area.setAttribute("class", "area");
  const poly = document.createElementNS(SVG, "polyline");
  poly.setAttribute("points", line);
  poly.setAttribute("class", "line");
  svg.append(defs, area, poly);
}

function renderStatus(s) {
  $("sim").hidden = !s.simulated;
  document.title = s.simulated ? "ORIONX · DÉMO SIMULÉE" : "ORIONX Dashboard";
  pill("mode", s.autonomy === "auto" ? "AUTO" : s.autonomy === "semi_auto" ? "semi-auto" : "signaux seuls", s.autonomy === "auto" ? "mode-auto" : "mode");
  if (s.aborted) pill("health", "ARRÊTÉ", "bad");
  else if (s.halts.length) pill("health", "mode sûr", "warn");
  else pill("health", "en marche", "ok");

  setValue("equity", money(s.equity), s.equity ?? undefined);
  setValue("balance", money(s.balance), s.balance ?? undefined);
  const pnl = s.equity !== null && s.balance !== null ? s.equity - s.balance : null;
  setValue("pnl", signed(pnl), pnl ?? undefined);
  $("pnl").className = `value ${pnl > 0 ? "pos" : pnl < 0 ? "neg" : ""}`;
  setValue("npos", String(s.positions.length));
  $("nsig").textContent = `${s.pending_signals} signal${s.pending_signals > 1 ? "s" : ""} en attente`;
  $("account").textContent = s.is_demo === null ? "—" : s.is_demo ? "Démo" : "NON DÉMO";
  $("account").className = `value ${s.is_demo === false ? "neg" : ""}`;
  $("cycles").textContent = s.cycles.toLocaleString("fr-FR");
  const halts = $("halts");
  halts.textContent = s.aborted ? s.aborted : s.halts.length ? s.halts.join(", ") : "aucun";
  halts.className = s.aborted || s.halts.length ? "active" : "";

  if (s.equity !== null) {
    equityHistory.push(s.equity);
    if (equityHistory.length > 180) equityHistory.shift(); // 6 minutes à 2 s
    drawSpark(equityHistory);
    const delta = equityHistory[equityHistory.length - 1] - equityHistory[0];
    $("equity-delta").textContent = equityHistory.length > 1 ? `${signed(delta)} depuis l'ouverture du dashboard` : "historique de la session";
  }

  const body = $("positions");
  body.replaceChildren();
  if (!s.positions.length) {
    const tr = el("tr"); const td = el("td", "Aucune position.", "empty"); td.colSpan = 8; tr.append(td); body.append(tr);
  }
  for (const p of s.positions) {
    const tr = el("tr");
    const side = el("td"); side.append(el("span", p.side === "buy" ? "ACHAT" : "VENTE", `badge ${p.side}`));
    tr.append(el("td", p.ticket, "num"), el("td", p.symbol, "sym"), side, el("td", p.volume, "num"), el("td", price(p.price_open), "num"),
      el("td", p.sl === null ? "AUCUN" : price(p.sl), p.sl === null ? "num neg" : "num"), el("td", price(p.tp), "num"),
      el("td", signed(p.profit), `num ${p.profit >= 0 ? "pos" : "neg"}`));
    body.append(tr);
  }
  $("updated").textContent = `mis à jour ${new Date().toLocaleTimeString("fr-FR")}`;
}

async function decide(id, action, button) {
  const verb = action === "approve" ? "APPROUVER (un ordre démo sera envoyé si le risque l'accepte)" : "rejeter";
  if (!window.confirm(`Voulez-vous ${verb} ce signal ?`)) return;
  button.disabled = true;
  try {
    const r = await api(`/api/signals/${encodeURIComponent(id)}/${action}`, { method: "POST", headers: CSRF });
    window.alert(r.ok ? (action === "approve" ? `Ordre exécuté (#${r.ticket ?? "?"} à ${r.price ?? "?"}).` : "Signal rejeté.") : `Refusé : ${r.message}`);
  } catch (e) {
    window.alert(`Échec : ${e.message}`);
  }
  refresh();
}

function renderSignals(list) {
  const body = $("signals");
  body.replaceChildren();
  if (!list.length) {
    const tr = el("tr"); const td = el("td", "Aucun signal.", "empty"); td.colSpan = 7; tr.append(td); body.append(tr);
  }
  for (const s of list) {
    const tr = el("tr");
    const actions = el("td");
    const box = el("div", null, "actions");
    const ok = el("button", "Approuver", "btn btn-approve"); ok.type = "button"; ok.disabled = s.expired;
    const no = el("button", "Rejeter", "btn btn-ghost"); no.type = "button";
    ok.addEventListener("click", () => decide(s.id, "approve", ok));
    no.addEventListener("click", () => decide(s.id, "reject", no));
    box.append(ok, no);
    actions.append(box);
    const side = el("td"); side.append(el("span", s.side === "buy" ? "ACHAT" : "VENTE", `badge ${s.side}`));
    tr.append(el("td", s.symbol, "sym"), side, el("td", price(s.sl), "num"), el("td", price(s.tp), "num"),
      el("td", s.strategy), el("td", s.expired ? "expiré" : time(s.valid_until), s.expired ? "neg" : "num"), actions);
    tr.title = s.rationale.join(" · ");
    body.append(tr);
  }
}

async function refresh() {
  if (sessionLost) return;
  try {
    const [status, signals] = await Promise.all([api("/api/status"), api("/api/signals")]);
    renderStatus(status);
    renderSignals(signals);
    banner("");
  } catch (e) {
    if (!sessionLost) { pill("health", "agent injoignable", "bad"); banner("L'agent ne répond pas : est-il lancé avec --api ?"); }
  }
}

async function kill(level) {
  const what = { 1: "mettre en PAUSE les nouvelles entrées", 2: "PROTÉGER les positions (stops resserrés)", 3: "CLÔTURER TOUTES les positions" }[level];
  if (!window.confirm(`Arrêt d'urgence niveau ${level} : ${what}. Confirmer ?`)) return;
  try {
    const r = await api("/api/kill", { method: "POST", headers: { ...CSRF, "Content-Type": "application/json" }, body: JSON.stringify({ level, reason: "dashboard" }) });
    window.alert(r.message);
  } catch (e) {
    window.alert(`Échec : ${e.message}`);
  }
}

function addEvent(item) {
  const sev = CRITICAL.has(item.kind) ? "sev-critical" : IMPORTANT.has(item.kind) ? "sev-important" : "sev-info";
  const li = el("li", null, sev);
  li.append(el("time", time(item.at)), el("b", item.kind));
  const p = item.payload || {};
  const detail = p.reason || p.detail || p.message || p.error || (p.symbol ? `${p.symbol}` : "");
  if (detail) li.append(el("span", String(detail), "detail"));
  const list = $("events");
  list.prepend(li);
  while (list.children.length > 200) list.lastChild.remove();
}

function connectEvents(delay = 1000) {
  if (sessionLost) return;
  const ws = new WebSocket(`ws://${location.host}/api/events`);
  ws.onopen = () => { pill("live", "en direct", "ok"); delay = 1000; };
  ws.onmessage = (m) => { try { addEvent(JSON.parse(m.data)); } catch (_) { /* message illisible ignoré */ } };
  ws.onclose = () => { pill("live", "reconnexion…", "warn"); setTimeout(() => connectEvents(Math.min(delay * 2, 30000)), delay); };
}

// --- Analyse du marché (onglets) ----------------------------------------------------------------
const analysis = { data: [], symbol: null, tab: "trend" };
const fmt = (v, digits = 2, suffix = "") => (v === null || v === undefined ? "—" : `${Number(v).toLocaleString("fr-FR", { maximumFractionDigits: digits })}${suffix}`);
const px = price;

function metric(label, value, cls) {
  const box = el("div", null, "metric");
  box.append(el("span", label), el("strong", value, cls));
  return box;
}

function svgEl(name, attrs) {
  const e = document.createElementNS(SVG, name);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
  return e;
}

function drawChart(chart) {
  const svg = svgEl("svg", { viewBox: "0 0 600 220", preserveAspectRatio: "none", class: "chart", role: "img",
    "aria-label": "Cours de clôture H1 avec moyennes mobiles 20 et 50" });
  const values = [...chart.closes, ...chart.ema20, ...chart.ema50].filter((v) => v !== null);
  if (chart.closes.length < 2 || !values.length) return svg;
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const n = chart.closes.length;
  const pts = (series) => series.map((v, i) => [(i + (n - series.length)) / (n - 1) * 600, 206 - ((v - min) / span) * 192])
    .map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const defs = svgEl("defs", {});
  const grad = svgEl("linearGradient", { id: "chart-fill", x1: 0, x2: 0, y1: 0, y2: 1 });
  grad.append(svgEl("stop", { offset: 0, "stop-color": "#00d9f8", "stop-opacity": 0.18 }), svgEl("stop", { offset: 1, "stop-color": "#00d9f8", "stop-opacity": 0 }));
  defs.append(grad);
  svg.append(defs);
  for (const y of [40, 90, 140, 190]) svg.append(svgEl("line", { x1: 0, x2: 600, y1: y, y2: y, class: "grid" }));
  const closes = pts(chart.closes);
  svg.append(svgEl("polygon", { points: `0,220 ${closes} 600,220`, class: "area" }));
  if (chart.ema50.length > 1) svg.append(svgEl("polyline", { points: pts(chart.ema50), class: "ema50" }));
  if (chart.ema20.length > 1) svg.append(svgEl("polyline", { points: pts(chart.ema20), class: "ema20" }));
  svg.append(svgEl("polyline", { points: closes, class: "price" }));
  return svg;
}

function legend() {
  const box = el("div", null, "legend");
  for (const [cls, text] of [["l-price", "Clôture H1"], ["l-ema20", "EMA 20"], ["l-ema50", "EMA 50"]]) {
    const item = el("span"); item.append(el("i", null, cls), document.createTextNode(text)); box.append(item);
  }
  return box;
}

function gauge(valuePct, left, right) {
  const wrap = el("div");
  const g = el("div", null, "gauge");
  const mark = el("b");
  mark.style.left = `${Math.max(0, Math.min(100, valuePct))}%`; // CSSOM : autorisé par la politique de sécurité
  g.append(mark);
  const labels = el("div", null, "gauge-labels");
  labels.append(el("span", left), el("span", right));
  wrap.append(g, labels);
  return wrap;
}

const PANELS = {
  trend(s) {
    const t = s.trend;
    const up = t.regime === "trend_up", down = t.regime === "trend_down";
    const m = el("div", null, "metrics");
    m.append(metric("Régime", t.regime_label, up ? "up" : down ? "down" : t.regime === "high_vol" ? "warn" : ""),
      metric("Prix", px(s.price.bid)), metric("EMA 20", px(t.ema20)), metric("EMA 50", px(t.ema50)),
      metric("Pente EMA 20", fmt(t.slope_pips_per_bar, 2, " pip/bougie"), t.slope_pips_per_bar > 0 ? "up" : t.slope_pips_per_bar < 0 ? "down" : ""),
      metric("Force (ATR)", fmt(t.trend_strength, 2)), metric("Efficacité", fmt(t.efficiency, 2)));
    return [m, drawChart(s.chart), legend()];
  },
  momentum(s) {
    const mo = s.momentum;
    const m = el("div", null, "metrics");
    m.append(metric("RSI 14", fmt(mo.rsi14, 1), mo.rsi_zone === "suracheté" ? "down" : mo.rsi_zone === "survendu" ? "up" : ""),
      metric("Zone", mo.rsi_zone || "—"), metric("Variation 24 h", fmt(mo.change_24h_pips, 1, " pips"), mo.change_24h_pips > 0 ? "up" : mo.change_24h_pips < 0 ? "down" : ""),
      metric("Variation 24 h", fmt(mo.change_24h_pct, 3, " %")));
    return mo.rsi14 === null ? [m] : [m, gauge(mo.rsi14, "survendu (30)", "suracheté (70)")];
  },
  volatility(s) {
    const v = s.volatility;
    const m = el("div", null, "metrics");
    m.append(metric("ATR 14", fmt(v.atr_pips, 1, " pips")), metric("ATR / médiane", fmt(v.volatility_ratio, 2), v.volatility_ratio >= v.high_vol_ratio ? "warn" : ""),
      metric("Seuil de blocage", fmt(v.high_vol_ratio, 1)), metric("Largeur Bollinger", fmt(v.bb_width_pips, 1, " pips")), metric("%B Bollinger", fmt(v.percent_b, 2)));
    return v.percent_b === null ? [m] : [m, gauge(v.percent_b * 100, "bande basse", "bande haute")];
  },
  levels(s) {
    const l = s.levels;
    const m = el("div", null, "metrics");
    m.append(metric("Résistance 2", px(l.r2)), metric("Résistance 1", px(l.r1)), metric("Pivot", px(l.pivot)), metric("Support 1", px(l.s1)),
      metric("Support 2", px(l.s2)), metric("Plus haut 24 h", px(l.high_24h)), metric("Plus bas 24 h", px(l.low_24h)));
    return l.position_in_range_pct === null || l.position_in_range_pct === undefined ? [m] : [m, gauge(l.position_in_range_pct, "plus bas 24 h", "plus haut 24 h")];
  },
  session(s) {
    const se = s.session;
    const m = el("div", null, "metrics");
    m.append(metric("Séance", se.session_label, se.blocked ? "warn" : ""), metric("Spread", fmt(s.price.spread_pips, 1, " pip")),
      metric("Fenêtre d'annonce", se.blackout ? "EN COURS" : "non", se.blackout ? "warn" : ""), metric("Annonces 24 h", String(se.events.length)));
    const list = el("ul", null, "list");
    for (const e of se.events) {
      const li = el("li");
      li.append(el("span", time(e.time), "num"), el("strong", e.currency), el("span", e.title, "grow"), el("span", `dans ${e.in_minutes} min · ${e.impact}`, "hint"));
      list.append(li);
    }
    return se.events.length ? [m, list] : [m];
  },
  decision(s) {
    const d = s.decision;
    const list = el("ul", null, "list");
    for (const st of d.strategies) {
      const li = el("li");
      li.append(el("i", null, st.eligible ? "dot-ok" : "dot-no"), el("strong", st.name, "grow"), el("span", st.why, "hint"));
      list.append(li);
    }
    const out = [list];
    if (d.last_notes.length) {
      const notes = el("ul", null, "list");
      for (const n of d.last_notes) notes.append(el("li", `${d.notes_at ? time(d.notes_at) : ""} · ${n}`));
      out.push(el("p", "Dernières notes du moteur de décision", "hint"), notes);
    }
    for (const p of d.positions) out.push(el("p", `Position #${p.ticket} : ${p.thesis.length ? `thèse dégradée (${p.thesis.join(", ")})` : "la thèse tient"}`, "hint"));
    return out;
  },
};

const READINGS = { trend: "trend", momentum: "momentum", volatility: "volatility", levels: "levels", session: "session", decision: "decision" };

function renderAnalysis() {
  const panel = $("an-panel");
  const s = analysis.data.find((x) => x.symbol === analysis.symbol) || analysis.data[0];
  if (!s) { panel.replaceChildren(el("p", "En attente de la première analyse…", "empty-panel")); return; }
  if (!s.bars) { panel.replaceChildren(el("p", `Pas encore de bougies analysées pour ${s.symbol} (la première analyse suit le démarrage).`, "empty-panel")); return; }
  const part = s[READINGS[analysis.tab]];
  panel.replaceChildren(el("p", part.reading, "reading"), ...PANELS[analysis.tab](s));
  $("an-updated").textContent = `calculée par l'agent sur ${s.bars} bougies H1 · ${time(s.updated_at)}`;
}

function selectTab(tab, focus) {
  analysis.tab = tab;
  for (const t of document.querySelectorAll(".tab")) {
    const on = t.dataset.tab === tab;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;
    if (on) { $("an-panel").setAttribute("aria-labelledby", t.id); if (focus) t.focus(); }
  }
  renderAnalysis();
}

async function refreshAnalysis() {
  if (sessionLost) return;
  try {
    analysis.data = await api("/api/analysis");
    const select = $("an-symbol");
    const symbols = analysis.data.map((x) => x.symbol);
    if (select.options.length !== symbols.length) {
      select.replaceChildren(...symbols.map((sym) => { const o = el("option", sym); o.value = sym; return o; }));
    }
    analysis.symbol = analysis.symbol && symbols.includes(analysis.symbol) ? analysis.symbol : symbols[0];
    select.value = analysis.symbol;
    select.hidden = symbols.length < 2;
    renderAnalysis();
  } catch (_) { /* l'état général affiche déjà la panne */ }
}

const tabs = [...document.querySelectorAll(".tab")];
for (const t of tabs) {
  t.addEventListener("click", () => selectTab(t.dataset.tab, false));
  t.addEventListener("keydown", (e) => { // navigation au clavier entre onglets
    const i = tabs.indexOf(t);
    const next = e.key === "ArrowRight" ? tabs[(i + 1) % tabs.length] : e.key === "ArrowLeft" ? tabs[(i - 1 + tabs.length) % tabs.length] : null;
    if (next) { e.preventDefault(); selectTab(next.dataset.tab, true); }
  });
}
$("an-symbol").addEventListener("change", (e) => { analysis.symbol = e.target.value; renderAnalysis(); });

// Lien direct vers un onglet : #momentum, #decision... (le navigateur conserve le fragment à travers la redirection de connexion)
const tabFromHash = () => { const t = location.hash.slice(1); return tabs.some((x) => x.dataset.tab === t) ? t : null; };
window.addEventListener("hashchange", () => { const t = tabFromHash(); if (t) selectTab(t, false); });
for (const t of tabs) t.addEventListener("click", () => history.replaceState(null, "", `#${t.dataset.tab}`));
if (tabFromHash()) selectTab(tabFromHash(), false);
refreshAnalysis();
setInterval(refreshAnalysis, 5000);

for (const b of document.querySelectorAll(".kill button")) b.addEventListener("click", () => kill(Number(b.dataset.level)));
refresh();
setInterval(refresh, 2000);
connectEvents();
