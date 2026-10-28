// ORIONX : changement de langue. Le français est la source (dans le HTML) ; en.js et es.js apportent les traductions.
// Pour ajouter une langue : un fichier assets/i18n/xx.js, un bouton data-lang="xx" dans l'en-tête, et la langue dans LANGS.
"use strict";

(function () {
  const LANGS = ["fr", "en", "es"];
  const STORE = "orionx-lang";
  const dict = window.ORIONX_I18N || {};

  function remembered() {
    try { return localStorage.getItem(STORE); } catch { return null; }
  }
  function remember(lang) {
    try { localStorage.setItem(STORE, lang); } catch { /* navigation privée : on s'en passe */ }
  }
  function initial() {
    const asked = new URLSearchParams(location.search).get("lang");
    if (LANGS.includes(asked)) return asked;
    const saved = remembered();
    if (LANGS.includes(saved)) return saved;
    const nav = (navigator.language || "fr").slice(0, 2).toLowerCase();
    return LANGS.includes(nav) ? nav : "fr";
  }

  const originals = new Map(); // élément -> { html, attrs }
  function original(el) {
    if (!originals.has(el)) {
      const attrs = {};
      for (const pair of (el.dataset.i18nAttr || "").split(";").filter(Boolean)) {
        const name = pair.split(":")[0];
        attrs[name] = el.getAttribute(name);
      }
      originals.set(el, { html: el.dataset.i18n ? (el.tagName === "TITLE" ? el.textContent : el.innerHTML) : null, attrs });
    }
    return originals.get(el);
  }

  function apply(lang) {
    const words = lang === "fr" ? null : dict[lang] || {};
    for (const el of document.querySelectorAll("[data-i18n], [data-i18n-attr]")) {
      const orig = original(el);
      if (el.dataset.i18n) {
        const text = words && words[el.dataset.i18n] !== undefined ? words[el.dataset.i18n] : orig.html;
        if (el.tagName === "TITLE") el.textContent = text;
        else el.innerHTML = text; // textes de nos propres fichiers, jamais saisis par un visiteur
      }
      for (const pair of (el.dataset.i18nAttr || "").split(";").filter(Boolean)) {
        const [name, key] = pair.split(":");
        el.setAttribute(name, words && words[key] !== undefined ? words[key] : orig.attrs[name]);
      }
    }
    document.documentElement.lang = lang;
    for (const b of document.querySelectorAll("[data-lang]")) b.setAttribute("aria-pressed", String(b.dataset.lang === lang));
    for (const y of document.querySelectorAll("[data-year]")) y.textContent = String(new Date().getFullYear());
    window.ORIONX_LANG = lang;
  }

  for (const b of document.querySelectorAll("[data-lang]")) {
    b.addEventListener("click", () => { remember(b.dataset.lang); apply(b.dataset.lang); });
  }
  const start = initial();
  if (start !== "fr") apply(start);
  else window.ORIONX_LANG = "fr";
})();
