// Dashboard ORIONX (M9). Aucune bibliothèque externe. Règle : tout texte venant de l'API passe par textContent,
// jamais par une fonction qui interprète du HTML (un texte malveillant ne peut pas devenir du code). Session par cookie HttpOnly : ce script
// ne voit jamais le jeton ; chaque action envoie l'en-tête anti-CSRF.
"use strict";

const $ = (id) => document.getElementById(id);
const CSRF = { "X-Requested-By": "orionx-dashboard" };
let sessionLost = false;

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

const num = (v, digits = 2) => (v === null || v === undefined ? "—" : Number(v).toLocaleString("fr-FR", { minimumFractionDigits: digits, maximumFractionDigits: digits }));
const price = (v) => (v === null || v === undefined ? "—" : String(v));
const time = (iso) => new Date(iso).toLocaleTimeString("fr-FR");

function pill(id, text, kind) {
  const p = $(id);
  p.textContent = text;
  p.className = `pill ${kind || ""}`;
}

function renderStatus(s) {
  pill("mode", s.autonomy === "auto" ? "auto" : s.autonomy === "semi_auto" ? "semi-auto" : "signaux seuls", s.autonomy === "auto" ? "warn" : "");
  if (s.aborted) pill("health", "ARRÊTÉ", "bad");
  else if (s.halts.length) pill("health", "mode sûr", "warn");
  else pill("health", "en marche", "ok");
  $("account").textContent = s.is_demo === null ? "—" : s.is_demo ? "démo" : "NON DÉMO";
  $("equity").textContent = num(s.equity);
  $("balance").textContent = num(s.balance);
  $("cycles").textContent = s.cycles;
  $("halts").textContent = s.aborted ? s.aborted : s.halts.length ? s.halts.join(", ") : "aucun";

  const body = $("positions");
  body.replaceChildren();
  if (!s.positions.length) {
    const tr = el("tr"); const td = el("td", "Aucune position.", "muted"); td.colSpan = 8; tr.append(td); body.append(tr);
  }
  for (const p of s.positions) {
    const tr = el("tr");
    tr.append(el("td", p.ticket), el("td", p.symbol), el("td", p.side === "buy" ? "achat" : "vente"), el("td", p.volume),
      el("td", price(p.price_open)), el("td", p.sl === null ? "AUCUN" : price(p.sl), p.sl === null ? "neg" : ""),
      el("td", price(p.tp)), el("td", num(p.profit), p.profit >= 0 ? "pos" : "neg"));
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
    const tr = el("tr"); const td = el("td", "Aucun signal.", "muted"); td.colSpan = 7; tr.append(td); body.append(tr);
  }
  for (const s of list) {
    const tr = el("tr");
    const actions = el("td");
    const ok = el("button", "Approuver", "approve"); ok.type = "button"; ok.disabled = s.expired;
    const no = el("button", "Rejeter"); no.type = "button";
    ok.addEventListener("click", () => decide(s.id, "approve", ok));
    no.addEventListener("click", () => decide(s.id, "reject", no));
    actions.append(ok, document.createTextNode(" "), no);
    tr.append(el("td", s.symbol), el("td", s.side === "buy" ? "achat" : "vente"), el("td", price(s.sl)), el("td", price(s.tp)),
      el("td", s.strategy), el("td", s.expired ? "expiré" : time(s.valid_until), s.expired ? "muted" : ""), actions);
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
  const li = el("li");
  li.append(el("time", time(item.at)), el("b", item.kind));
  const p = item.payload || {};
  const detail = p.reason || p.detail || p.message || p.error || (p.symbol ? `${p.symbol}` : "");
  if (detail) li.append(document.createTextNode(String(detail)));
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
