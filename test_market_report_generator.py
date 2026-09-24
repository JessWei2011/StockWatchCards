import unittest
from market_report_generator import rows_with_official_volume


class TestMarketReportGenerator(unittest.TestCase):
    def test_rows_with_official_volume_excludes_weekends_and_unverified_rows(self):
        rows = [
            {"date": "2026-09-18", "open": 1, "high": 2, "low": 1, "close": 2},
            {"date": "2026-09-20", "open": 2, "high": 3, "low": 2, "close": 3},
            {"date": "2026-09-21", "open": 3, "high": 4, "low": 3, "close": 4},
        ]

        actual = rows_with_official_volume(rows, {"2026-09-18": 100, "2026-09-20": 1})

        self.assertEqual(actual, [{**rows[0], "volume": 100.0}])


if __name__ == "__main__":
    unittest.main()
