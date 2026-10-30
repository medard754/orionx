// ORIONX : site vitrine. Aucune bibliothèque, aucun appel réseau, aucun traceur.
"use strict";

document.documentElement.classList.add("js"); // les animations d'apparition ne masquent le contenu que si ce script tourne

// Menu sur mobile
const nav = document.querySelector(".nav");
const burger = document.querySelector(".burger");
if (nav && burger) {
  burger.addEventListener("click", () => {
    const open = nav.classList.toggle("open");
    burger.setAttribute("aria-expanded", String(open));
  });
}

// Lien de la page courante
const page = location.pathname.split("/").pop() || "index.html";
for (const a of document.querySelectorAll(".nav ul a")) {
  if (a.getAttribute("href") === page) a.setAttribute("aria-current", "page");
}

// Apparition au défilement
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const reveals = document.querySelectorAll(".reveal");
if (reduced || !("IntersectionObserver" in window)) {
  reveals.forEach((el) => el.classList.add("in"));
} else {
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
  }, { threshold: 0.12 });
  reveals.forEach((el) => io.observe(el));
}

// Scène 3D : légère inclinaison qui suit la souris
const scene = document.querySelector(".scene");
const inner = document.querySelector(".scene-inner");
if (scene && inner && !reduced) {
  scene.addEventListener("pointermove", (e) => {
    const r = scene.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width - 0.5;
    const y = (e.clientY - r.top) / r.height - 0.5;
    inner.style.setProperty("--ry", (x * 14).toFixed(2));
    inner.style.setProperty("--rx", (-y * 10).toFixed(2));
  });
  scene.addEventListener("pointerleave", () => { inner.style.setProperty("--ry", "0"); inner.style.setProperty("--rx", "0"); });
}

// Année du pied de page
for (const el of document.querySelectorAll("[data-year]")) el.textContent = String(new Date().getFullYear());
