// ORIONX : illustration 3D du « cœur » (réseau neuronal + périmètre de contrôle du risque).
// Canvas 2D avec projection en perspective écrite à la main : aucune bibliothèque, aucune donnée de marché, aucun appel réseau.
// Les impulsions (idées de l'IA) partent du réseau et rencontrent le périmètre : certaines passent, d'autres sont refusées.
"use strict";

(function () {
  const canvas = document.getElementById("core3d");
  const ctx = canvas && canvas.getContext("2d");
  if (!ctx) return;

  const css = getComputedStyle(document.documentElement);
  const token = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  const COLOR = {
    net: token("--blue-text", "#4d8dff"),
    idea: token("--cyan", "#00d9f8"),
    ok: "#2ee6a6",
    gate: token("--gold", "#fed90f"),
    no: token("--orange", "#ffa630"),
  };
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // --- géométrie : points sur une sphère (spirale de Fibonacci), reliés à leurs plus proches voisins -------------------
  const N = 84;
  const nodes = [];
  for (let i = 0; i < N; i++) {
    const y = 1 - (2 * (i + 0.5)) / N;
    const r = Math.sqrt(1 - y * y);
    const a = i * Math.PI * (3 - Math.sqrt(5));
    nodes.push({ x: Math.cos(a) * r, y, z: Math.sin(a) * r, phase: Math.random() * 6.28 });
  }
  const edges = [];
  for (let i = 0; i < N; i++) {
    const near = nodes
      .map((n, j) => ({ j, d: (n.x - nodes[i].x) ** 2 + (n.y - nodes[i].y) ** 2 + (n.z - nodes[i].z) ** 2 }))
      .filter((o) => o.j !== i)
      .sort((p, q) => p.d - q.d)
      .slice(0, 3);
    for (const { j } of near) if (!edges.some(([a, b]) => (a === i && b === j) || (a === j && b === i))) edges.push([i, j]);
  }
  const GATE_R = 1.5; // rayon du périmètre de contrôle
  const rings = [0, 1, 2].map((k) => ({ tilt: (k * Math.PI) / 3, speed: 0.12 + k * 0.05 }));

  // --- caméra --------------------------------------------------------------------------------------------------------
  let yaw = 0, pitch = 0.35, drift = 0, offset = 0, targetOffset = 0, targetPitch = 0.35, size = 560;
  function resize() {
    const box = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    size = Math.max(240, Math.round(box.width));
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function project(x, y, z) {
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
    const x1 = x * cy + z * sy, z1 = -x * sy + z * cy;
    const y1 = y * cp - z1 * sp, z2 = y * sp + z1 * cp;
    const f = 3.6 / (3.6 + z2);
    const s = size * 0.225;
    return { x: size / 2 + x1 * s * f, y: size / 2 + y1 * s * f, depth: (z2 + 2.2) / 4.4, f };
  }
  const rgba = (hex, a) => {
    const n = parseInt(hex.replace("#", "").slice(0, 6), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${Math.max(0, Math.min(1, a)).toFixed(3)})`;
  };

  // --- impulsions ----------------------------------------------------------------------------------------------------
  const pulses = [];
  const sparks = [];
  function spawn() {
    const n = nodes[Math.floor(Math.random() * N)];
    pulses.push({ dir: n, t: 0, pass: Math.random() < 0.55, speed: 0.55 + Math.random() * 0.3, hit: false });
  }
  const at = (dir, r) => project(dir.x * r, dir.y * r, dir.z * r);

  // --- rendu ---------------------------------------------------------------------------------------------------------
  function draw(time, dt) {
    ctx.clearRect(0, 0, size, size);

    // halo central
    const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size * 0.46);
    g.addColorStop(0, rgba(COLOR.net, 0.22));
    g.addColorStop(0.55, rgba(COLOR.net, 0.06));
    g.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, size, size);
    const core = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size * 0.11);
    core.addColorStop(0, rgba(COLOR.idea, 0.55 + 0.15 * Math.sin(time * 2)));
    core.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = core;
    ctx.fillRect(0, 0, size, size);

    // périmètre de contrôle : trois cercles inclinés qui tournent
    ctx.lineWidth = 1.2;
    for (const ring of rings) {
      const ang = time * ring.speed;
      ctx.beginPath();
      for (let k = 0; k <= 96; k++) {
        const a = (k / 96) * Math.PI * 2;
        const x = Math.cos(a) * GATE_R, z = Math.sin(a) * GATE_R;
        // cercle dans le plan XZ, incliné autour de X puis tourné autour de Y
        const y1 = -z * Math.sin(ring.tilt), z1 = z * Math.cos(ring.tilt);
        const p = project(x * Math.cos(ang) + z1 * Math.sin(ang), y1, -x * Math.sin(ang) + z1 * Math.cos(ang));
        if (k === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y);
      }
      ctx.strokeStyle = rgba(COLOR.gate, 0.28);
      ctx.stroke();
    }

    // arêtes puis nœuds du réseau (les plus proches de la caméra sont plus lumineux)
    const proj = nodes.map((n) => project(n.x, n.y, n.z));
    ctx.lineWidth = 1;
    for (const [a, b] of edges) {
      const pa = proj[a], pb = proj[b];
      ctx.strokeStyle = rgba(COLOR.net, 0.08 + 0.3 * ((pa.depth + pb.depth) / 2) ** 2);
      ctx.beginPath(); ctx.moveTo(pa.x, pa.y); ctx.lineTo(pb.x, pb.y); ctx.stroke();
    }
    nodes.forEach((n, i) => {
      const p = proj[i];
      const glow = 0.5 + 0.5 * Math.sin(time * 1.6 + n.phase);
      const rad = (1.4 + 2.2 * p.depth) * (0.8 + 0.25 * glow);
      ctx.fillStyle = rgba(COLOR.idea, 0.35 + 0.65 * p.depth);
      ctx.beginPath(); ctx.arc(p.x, p.y, rad, 0, Math.PI * 2); ctx.fill();
    });

    // impulsions
    for (const p of pulses) {
      p.t += dt * p.speed;
      const r = 1.02 + p.t * 1.23; // 1.02 (surface) -> 2.25
      const rGate = GATE_R;
      const blocked = !p.pass && r >= rGate;
      const rr = blocked ? rGate : r;
      if (blocked && !p.hit) {
        p.hit = true;
        sparks.push({ dir: p.dir, age: 0 });
      }
      const head = at(p.dir, rr);
      const tail = at(p.dir, Math.max(1.02, rr - 0.32));
      const color = blocked ? COLOR.no : r > rGate ? COLOR.ok : COLOR.idea;
      const fade = blocked ? Math.max(0, 1 - (r - rGate) * 3) : r > 1.95 ? Math.max(0, (2.25 - r) / 0.3) : 1;
      const lg = ctx.createLinearGradient(tail.x, tail.y, head.x, head.y);
      lg.addColorStop(0, rgba(color, 0));
      lg.addColorStop(1, rgba(color, 0.95 * fade));
      ctx.strokeStyle = lg;
      ctx.lineWidth = 2.4;
      ctx.beginPath(); ctx.moveTo(tail.x, tail.y); ctx.lineTo(head.x, head.y); ctx.stroke();
      ctx.fillStyle = rgba(color, fade);
      ctx.shadowColor = color;
      ctx.shadowBlur = 12;
      ctx.beginPath(); ctx.arc(head.x, head.y, 3.2 * head.f, 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0;
      p.done = blocked ? r > rGate + 0.34 : r >= 2.25;
    }
    for (let i = pulses.length - 1; i >= 0; i--) if (pulses[i].done) pulses.splice(i, 1);

    // éclats de refus sur le périmètre
    for (const s of sparks) {
      s.age += dt;
      const c = at(s.dir, GATE_R);
      const k = s.age / 0.7;
      ctx.strokeStyle = rgba(COLOR.no, 1 - k);
      ctx.lineWidth = 1.6;
      ctx.beginPath(); ctx.arc(c.x, c.y, 4 + k * 22 * c.f, 0, Math.PI * 2); ctx.stroke();
    }
    for (let i = sparks.length - 1; i >= 0; i--) if (sparks[i].age > 0.7) sparks.splice(i, 1);
  }

  // --- boucle (arrêtée hors écran / onglet masqué) -------------------------------------------------------------------
  let running = false, last = 0, clock = 0, since = 0;
  function frame(now) {
    if (!running) return;
    const dt = Math.min(0.05, (now - last) / 1000 || 0.016);
    last = now;
    clock += dt;
    since += dt;
    if (since > 0.42 && pulses.length < 9) { spawn(); since = 0; }
    drift += 0.18 * dt;
    offset += (targetOffset - offset) * 0.06;
    yaw = drift + offset;
    pitch += (targetPitch - pitch) * 0.05;
    draw(clock, dt);
    requestAnimationFrame(frame);
  }
  function start() { if (!running && !reduced) { running = true; last = performance.now(); requestAnimationFrame(frame); } }
  function stop() { running = false; }

  resize();
  // préchauffage : des impulsions déjà en vol dès le premier affichage
  for (let i = 0; i < 7; i++) { spawn(); pulses[i].t = 0.1 + i * 0.12; }
  window.addEventListener("resize", () => { resize(); if (!running) draw(clock, 0); });
  if (reduced) {
    drift = yaw = 0.6;
    draw(0, 0);
    return;
  }
  canvas.addEventListener("pointermove", (e) => {
    const r = canvas.getBoundingClientRect();
    targetOffset = ((e.clientX - r.left) / r.width - 0.5) * 1.1;
    targetPitch = 0.35 + ((e.clientY - r.top) / r.height - 0.5) * 0.5;
  });
  canvas.addEventListener("pointerleave", () => { targetPitch = 0.35; targetOffset = 0; });
  let onScreen = !("IntersectionObserver" in window);
  const sync = () => (onScreen && !document.hidden ? start() : stop());
  if (!onScreen) {
    new IntersectionObserver((entries) => { onScreen = entries[entries.length - 1].isIntersecting; sync(); }).observe(canvas);
  }
  document.addEventListener("visibilitychange", sync);
  sync();
})();
