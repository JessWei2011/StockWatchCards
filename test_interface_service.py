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


if __name__ == "__main__":
    unittest.main()
