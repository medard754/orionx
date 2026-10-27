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
const price = (v) => (v === null || v === undefined ? "—" : String(v));
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

for (const b of document.querySelectorAll(".kill button")) b.addEventListener("click", () => kill(Number(b.dataset.level)));
refresh();
setInterval(refresh, 2000);
connectEvents();
