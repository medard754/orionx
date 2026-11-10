"""H-3 : gel des données avec un faux courtier : empreintes stables, toute modification détectée, bougie en formation écartée."""

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from orionx.backtest.h3_freeze import Row, freeze, read_snapshot, snapshot_name, verify_manifest


def hour(year: int, k: int) -> int:
    return int(datetime(year, 1, 1, tzinfo=UTC).timestamp()) + k * 3600


def fake_fetch(symbol: str, tf: str, year: int) -> list[Row]:
    if year < 2024:
        return []  # le courtier n'a rien avant 2024
    return [(hour(year, k), 1.1 + k * 1e-5, 1.1002 + k * 1e-5, 1.0998 + k * 1e-5, 1.1001 + k * 1e-5, 100 + k, 2) for k in range(24)]


class FreezeTests(unittest.TestCase):
    def run_freeze(self, tmp: str) -> dict:
        return freeze(fake_fetch, Path(tmp), symbols=("GBPUSD",), timeframes=("H1",), first_year=2022, last_year=2025, server="Faux-Demo",
                      commission_per_lot_per_side=3.5, clock=lambda: datetime(2025, 6, 1, tzinfo=UTC), log=lambda _m: None)  # fmt: skip

    def test_freeze_writes_files_and_a_manifest_with_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            m = self.run_freeze(tmp)
            (entry,) = m["files"]
            self.assertEqual((m["server"], m["commission_usd_per_lot_per_side"], entry["symbol"]), ("Faux-Demo", 3.5, "GBPUSD"))
            self.assertEqual(entry["rows"], 2 * 24 - 1, "2024 et 2025 : 48 bougies, moins la bougie en formation")
            self.assertEqual(len(entry["sha256"]), 64)
            rows = read_snapshot(Path(tmp) / snapshot_name("GBPUSD", "H1"))
            self.assertEqual(len(rows), entry["rows"])
            self.assertEqual(rows, sorted(rows), "bougies triées par heure")
            self.assertEqual(rows[-1][0], hour(2025, 22), "la dernière bougie (la 24e de 2025) est écartée")
            json.dumps(m)  # le manifeste est sérialisable tel quel

    def test_the_same_data_gives_the_same_hash_and_verification_passes(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ma, mb = self.run_freeze(a), self.run_freeze(b)
            self.assertEqual(ma["files"][0]["sha256"], mb["files"][0]["sha256"])
            self.assertEqual(verify_manifest(ma, Path(a)), [])

    def test_any_change_missing_file_or_truncation_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            m = self.run_freeze(tmp)
            path = Path(tmp) / snapshot_name("GBPUSD", "H1")
            original = path.read_text(encoding="utf-8")
            path.write_text(original.replace("1.1001", "1.1002", 1), encoding="utf-8")
            self.assertIn("empreinte différente", verify_manifest(m, Path(tmp))[0])
            path.write_text("\n".join(original.splitlines()[:-1]) + "\n", encoding="utf-8")
            self.assertTrue(verify_manifest(m, Path(tmp)))
            path.unlink()
            self.assertIn("fichier absent", verify_manifest(m, Path(tmp))[0])

    def test_a_series_without_data_is_left_out_not_invented(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            m = freeze(lambda *_a: [], Path(tmp), symbols=("XXXYYY",), timeframes=("H1",), first_year=2024, last_year=2024,
                       clock=lambda: datetime(2025, 1, 1, tzinfo=UTC), log=lambda _m: None)  # fmt: skip
            self.assertEqual(m["files"], [])

    def test_a_missing_interior_year_is_flagged_as_suspect(self) -> None:
        def full(symbol: str, tf: str, year: int) -> list[Row]:
            return [(hour(year, k), 1.1, 1.1002, 1.0998, 1.1001, 1, 2) for k in range(24)] if year >= 2022 else []

        with tempfile.TemporaryDirectory() as tmp:
            ok = freeze(full, Path(tmp), symbols=("GBPUSD",), timeframes=("H1",), first_year=2022, last_year=2025,
                        clock=lambda: datetime(2025, 6, 1, tzinfo=UTC), log=lambda _m: None)  # fmt: skip
            self.assertEqual(ok["files"][0]["suspect_years"], [])
            broken = freeze(lambda s, t, y: [] if y == 2024 else full(s, t, y), Path(tmp), symbols=("GBPUSD",), timeframes=("H1",),
                            first_year=2022, last_year=2025, clock=lambda: datetime(2025, 6, 1, tzinfo=UTC),
                            log=lambda _m: None)  # fmt: skip
            self.assertEqual(broken["files"][0]["suspect_years"], [2024])

    def test_duplicate_bars_are_merged(self) -> None:
        def dup(symbol: str, tf: str, year: int) -> list[Row]:
            rows = fake_fetch(symbol, tf, year)
            return rows + rows[:5] if year == 2025 else rows

        with tempfile.TemporaryDirectory() as tmp:
            m = freeze(dup, Path(tmp), symbols=("GBPUSD",), timeframes=("H1",), first_year=2024, last_year=2025,
                       clock=lambda: datetime(2025, 6, 1, tzinfo=UTC), log=lambda _m: None)  # fmt: skip
            self.assertEqual(m["files"][0]["rows"], 47)


if __name__ == "__main__":
    unittest.main()
