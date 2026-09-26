import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import interface_service


class TestInterfaceService(unittest.TestCase):
    def test_resolve_stock_code_pure_numeric(self):
        code, name = interface_service.resolve_stock_code("2330")
        self.assertEqual(code, "2330")

    def test_resolve_stock_code_suffix_strip(self):
        code, name = interface_service.resolve_stock_code("2330.TW")
        self.assertEqual(code, "2330")
        code, name = interface_service.resolve_stock_code("6515.TWO")
        self.assertEqual(code, "6515")

    def test_find_latest_report_file_not_found(self):
        file = interface_service.find_latest_report_file("99999999")
        self.assertIsNone(file)

    @patch("interface_service.run_report_generator")
    def test_get_stock_report_and_tags_invalid(self, mock_run):
        mock_run.return_value = (False, "查無此代碼")
        res = interface_service.get_stock_report_and_tags("999999", force_update=True)
        self.assertFalse(res["ok"])
        self.assertIn("error", res)

    def test_get_broker_chip_tag_no_broker_details(self):
        import broker_chip_service
        data = {
            "concentration_1d": -26.5,
            "concentration_5d": 17.1,
            "matrix_status": "💎 波段吸籌·短線洗盤",
            "top_buyers_5d": ["摩根士丹利+7,764", "高盛+6,131"],
            "top_sellers": ["瑞銀-1,182", "摩根士丹利-1,141"],
            "has_day_trader": False,
        }
        tag = broker_chip_service.get_broker_chip_tag(data)
        self.assertEqual(tag, "💎 波段吸籌·短線洗盤 (5D +17.1% ｜ 1D -26.5%)")
        self.assertNotIn("5D買", tag)
        self.assertNotIn("1D賣", tag)


if __name__ == "__main__":
    unittest.main()
