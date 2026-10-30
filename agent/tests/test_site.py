"""Garde-fous du site vitrine (dossier site/ à la racine du dépôt)."""

import re
import unittest
from collections import Counter
from pathlib import Path

SITE = Path(__file__).resolve().parents[2] / "site"
PAGES = sorted(SITE.glob("*.html"))
REF = re.compile(r'(?:href|src)="([^"]+)"')


@unittest.skipUnless(SITE.is_dir(), "dossier site/ absent")
class SiteTests(unittest.TestCase):
    def test_expected_pages_exist(self) -> None:
        names = {p.name for p in PAGES}
        for page in ("index.html", "abonnement.html", "a-propos.html", "contact.html", "connexion.html", "mentions-legales.html"):
            self.assertIn(page, names)

    def test_every_internal_link_and_image_exists(self) -> None:
        for page in PAGES:
            for ref in REF.findall(page.read_text(encoding="utf-8")):
                if ref.startswith(("mailto:", "#")):
                    continue
                target = ref.split("#")[0].split("?")[0]
                self.assertTrue((SITE / target).is_file(), f"{page.name} : lien cassé vers {ref}")

    def test_no_external_resources_or_trackers(self) -> None:
        for path in [*PAGES, SITE / "assets" / "site.css", *(SITE / "assets").rglob("*.js")]:
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"(?:src|href)=\"https?://|url\(\s*['\"]?https?://|@import", text),
                              f"{path.name} : ressource externe (vie privée, disponibilité)")  # fmt: skip

    def test_every_page_carries_the_risk_warning(self) -> None:
        for page in PAGES:
            self.assertIn("Avertissement sur les risques", page.read_text(encoding="utf-8"), page.name)

    def test_no_credential_collection(self) -> None:
        for page in PAGES:
            self.assertNotIn('type="password"', page.read_text(encoding="utf-8"), f"{page.name} : pas de faux formulaire de connexion")

    def test_no_performance_promise(self) -> None:
        for page in PAGES:
            text = page.read_text(encoding="utf-8").lower()
            for claim in ("gains garantis", "rendement garanti", "% par mois", "sans risque de perte"):
                self.assertNotIn(claim, text, f"{page.name} : promesse interdite « {claim} »")

    def test_images_stay_light(self) -> None:
        for img in (SITE / "assets").glob("*.png"):
            self.assertLess(img.stat().st_size, 400_000, f"{img.name} trop lourde")


LANGS = ("en", "es")
ELEMENT = re.compile(r'<([a-z0-9]+)\b[^>]*\bdata-i18n="([^"]+)"[^>]*>(.*?)</\1>', re.S)
ATTR_TAG = re.compile(r'<[a-z0-9]+\b[^>]*\bdata-i18n-attr="([^"]+)"[^>]*>')


def page_keys() -> dict[str, str]:
    """clé -> texte français source (contenu de l'élément, ou valeur de l'attribut), pour toutes les pages."""
    keys: dict[str, str] = {}
    for page in PAGES:
        html = page.read_text(encoding="utf-8")
        for m in ELEMENT.finditer(html):
            keys[m.group(2)] = re.sub(r"\s+", " ", m.group(3)).strip()
        for tag in ATTR_TAG.finditer(html):
            for pair in tag.group(1).split(";"):
                name, key = pair.split(":")
                value = re.search(rf'\s{name}="([^"]*)"', tag.group(0))
                keys.setdefault(key, value.group(1) if value else "")
    return keys


def translations(lang: str) -> dict[str, str]:
    text = (SITE / "assets" / "i18n" / f"{lang}.js").read_text(encoding="utf-8")
    return dict(re.findall(r'^ "([^"]+)": "((?:[^"\\]|\\.)*)",?$', text, re.M))


@unittest.skipUnless(SITE.is_dir(), "dossier site/ absent")
class SiteLanguageTests(unittest.TestCase):
    def test_every_page_has_the_language_switch_and_scripts(self) -> None:
        for page in PAGES:
            html = page.read_text(encoding="utf-8")
            for lang in ("fr", *LANGS):
                self.assertIn(f'data-lang="{lang}"', html, f"{page.name} : bouton {lang} absent")
            for script in ("assets/i18n/en.js", "assets/i18n/es.js", "assets/i18n.js"):
                self.assertIn(f'src="{script}"', html, f"{page.name} : {script} non chargé")
            self.assertIn('<html lang="fr">', html, "le français reste la langue source")

    def test_translations_cover_exactly_the_marked_texts(self) -> None:
        keys = set(page_keys())
        self.assertGreater(len(keys), 150)
        for lang in LANGS:
            tr = translations(lang)
            self.assertEqual(sorted(keys - set(tr)), [], f"{lang} : clés sans traduction")
            self.assertEqual(sorted(set(tr) - keys), [], f"{lang} : traductions orphelines")
            for key, value in tr.items():
                self.assertTrue(value.strip(), f"{lang}/{key} : traduction vide")

    def test_translations_keep_the_inline_markup_of_the_source(self) -> None:
        # une balise perdue ou en trop casserait la mise en page (ex. le dégradé des titres, le ✓ des listes)
        for lang in LANGS:
            tr = translations(lang)
            for key, fr in page_keys().items():
                tags = lambda t: Counter(re.findall(r"</?([a-z0-9]+)", t))  # noqa: E731
                self.assertEqual(tags(tr[key]), tags(fr), f"{lang}/{key} : balises différentes du français")
                self.assertEqual("data-year" in tr[key], "data-year" in fr, f"{lang}/{key} : année")

    def test_the_risk_warning_exists_in_every_language(self) -> None:
        for lang, phrase in (("en", "Risk warning"), ("es", "Advertencia de riesgo")):
            self.assertTrue(any(phrase in v for v in translations(lang).values()), f"{lang} : avertissement sur les risques absent")

    def test_translations_make_no_performance_promise(self) -> None:
        banned = ("guaranteed profit", "guaranteed return", "risk-free", "risk free", "ganancias garantizadas", "sin riesgo de pérdida")
        for lang in LANGS:
            for key, value in translations(lang).items():
                for claim in banned:
                    self.assertNotIn(claim, value.lower(), f"{lang}/{key} : promesse interdite « {claim} »")

    def test_the_3d_section_is_decorative_and_honest(self) -> None:
        index = (SITE / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="core3d"', index)
        self.assertIn("aucune donnée de marché réelle", index, "l'illustration doit se dire illustration")
        js = (SITE / "assets" / "core3d.js").read_text(encoding="utf-8")
        self.assertIn("prefers-reduced-motion", js, "animation arrêtée si le visiteur le demande")
        for forbidden in ("fetch(", "XMLHttpRequest", "WebSocket", "http://", "https://"):
            self.assertNotIn(forbidden, js, f"core3d.js : {forbidden}")

    def test_dashboard_previews_are_present_and_described(self) -> None:
        index = (SITE / "index.html").read_text(encoding="utf-8")
        for img in ("galerie-analyse", "galerie-ticket", "galerie-signal", "apercu-ordinateur", "apercu-mobile"):
            self.assertTrue((SITE / "assets" / f"{img}.png").is_file(), img)
        for m in re.finditer(r"<img\b[^>]*assets/galerie-[^>]*>", index):
            self.assertRegex(m.group(0), r'alt="[^"]{20,}"', "capture sans description")
        self.assertIn("disponible en français", index, "dire que le tableau de bord n'est pas traduit")


@unittest.skipUnless(SITE.is_dir(), "dossier site/ absent")
class LegalPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (SITE / "mentions-legales.html").read_text(encoding="utf-8")

    def test_every_page_links_to_the_legal_notice(self) -> None:
        for page in PAGES:
            self.assertIn('href="mentions-legales.html"', page.read_text(encoding="utf-8"), f"{page.name} : lien absent du pied de page")

    def test_the_mandatory_sections_exist(self) -> None:
        for needle in ("Éditeur du site", "Hébergeur", "GitHub", "Données personnelles", "aucun cookie", "RGPD", "CNIL",
                       "pas un conseil en investissement", "Droit applicable"):  # fmt: skip
            self.assertIn(needle, self.html, f"mention manquante : {needle}")

    def test_the_legal_text_stays_in_french_and_is_not_machine_translated(self) -> None:
        body = self.html.split("<!--i18n-off-->")[1].split("<!--/i18n-off-->")[0]
        self.assertNotIn("data-i18n", body, "texte juridique : une seule version, en français, qui fait foi")
        self.assertIn("seule version faisant foi", body)

    def test_unfilled_fields_are_marked_and_countable(self) -> None:
        # Avant publication publique, ces champs doivent être remplis : `grep "À COMPLÉTER" site/` doit ne rien renvoyer.
        todo = re.findall(r"\[À COMPLÉTER[^\]]*\]", self.html)
        self.assertEqual(self.html.count("[À COMPLÉTER"), len(todo), "marqueur mal formé")


if __name__ == "__main__":
    unittest.main()
