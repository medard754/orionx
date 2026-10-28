"""Garde-fous du site vitrine (dossier site/ à la racine du dépôt)."""

import re
import unittest
from pathlib import Path

SITE = Path(__file__).resolve().parents[2] / "site"
PAGES = sorted(SITE.glob("*.html"))
REF = re.compile(r'(?:href|src)="([^"]+)"')


@unittest.skipUnless(SITE.is_dir(), "dossier site/ absent")
class SiteTests(unittest.TestCase):
    def test_expected_pages_exist(self) -> None:
        names = {p.name for p in PAGES}
        for page in ("index.html", "abonnement.html", "a-propos.html", "contact.html", "connexion.html"):
            self.assertIn(page, names)

    def test_every_internal_link_and_image_exists(self) -> None:
        for page in PAGES:
            for ref in REF.findall(page.read_text(encoding="utf-8")):
                if ref.startswith(("mailto:", "#")):
                    continue
                target = ref.split("#")[0].split("?")[0]
                self.assertTrue((SITE / target).is_file(), f"{page.name} : lien cassé vers {ref}")

    def test_no_external_resources_or_trackers(self) -> None:
        for path in [*PAGES, SITE / "assets" / "site.css", SITE / "assets" / "site.js"]:
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


if __name__ == "__main__":
    unittest.main()
