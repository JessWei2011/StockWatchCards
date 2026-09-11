import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import reports_manager_server as server


class TestReportFileManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.reports_dir = Path(self.temp_dir.name) / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.patcher = patch.object(server, "REPORTS_DIR", self.reports_dir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.temp_dir.cleanup()

    def _make_handler(self, path, body=None):
        handler = server.Handler.__new__(server.Handler)
        handler.path = path
        handler._read_json_body = MagicMock(return_value=body or {})
        handler._json = MagicMock()
        return handler

    def test_create_and_delete_folder(self):
        # 1. 新增資料夾
        h_create = self._make_handler("/api/folder/create", {"parentPath": "", "name": "測試分類"})
        h_create.do_POST()
        code, resp = h_create._json.call_args[0]
        self.assertEqual(code, 200)
        self.assertTrue(resp.get("ok"))
        self.assertTrue((self.reports_dir / "測試分類").is_dir())

        # 2. 刪除空資料夾
        h_del = self._make_handler("/api/folder/delete", {"path": "測試分類"})
        h_del.do_POST()
        code, resp = h_del._json.call_args[0]
        self.assertEqual(code, 200)
        self.assertTrue(resp.get("ok"))
        self.assertFalse((self.reports_dir / "測試分類").exists())

    def test_move_html_and_md_together(self):
        # 建立來源與目標資料夾
        src_folder = self.reports_dir / "來源"
        dest_folder = self.reports_dir / "目標"
        src_folder.mkdir()
        dest_folder.mkdir()

        # 建立個股 2455 的 .html 與 .md 及 _chart.png
        html_file = src_folder / "2455_全新(TW).html"
        md_file = src_folder / "2455_全新_4階段技術分析報告.md"
        chart_file = src_folder / "2455_全新(TW)_chart.png"
        html_file.write_text("<html>全新</html>", encoding="utf-8")
        md_file.write_text("# 2455 全新技術分析", encoding="utf-8")
        chart_file.write_bytes(b"PNG_FAKE_DATA")

        # 移動個股
        h_move = self._make_handler("/api/report/move", {
            "from": "來源",
            "to": "目標",
            "base": "2455_全新(TW)",
            "code": "2455"
        })
        h_move.do_POST()
        code, resp = h_move._json.call_args[0]
        self.assertEqual(code, 200)
        self.assertTrue(resp.get("ok"))

        # 驗證來源檔案均已消失
        self.assertFalse(html_file.exists())
        self.assertFalse(md_file.exists())
        self.assertFalse(chart_file.exists())

        # 驗證目標資料夾已完整收到 .html、.md 與 _chart.png
        self.assertTrue((dest_folder / "2455_全新(TW).html").exists())
        self.assertTrue((dest_folder / "2455_全新_4階段技術分析報告.md").exists())
        self.assertTrue((dest_folder / "2455_全新(TW)_chart.png").exists())
        self.assertEqual((dest_folder / "2455_全新_4階段技術分析報告.md").read_text(encoding="utf-8"), "# 2455 全新技術分析")

    def test_delete_html_and_md_together(self):
        folder = self.reports_dir / "測試分類"
        folder.mkdir()

        html_file = folder / "3081_聯亞(TWO).html"
        md_file = folder / "3081_聯亞_4階段技術分析報告.md"
        chart_file = folder / "3081_聯亞(TWO)_chart.png"
        html_file.write_text("<html>聯亞</html>", encoding="utf-8")
        md_file.write_text("# 3081 聯亞分析報告", encoding="utf-8")
        chart_file.write_bytes(b"PNG")

        # 刪除特定資料夾中的報表
        h_del = self._make_handler("/api/report/delete", {
            "folder": "測試分類",
            "code": "3081",
            "base": "3081_聯亞(TWO)"
        })
        h_del.do_POST()
        code, resp = h_del._json.call_args[0]
        self.assertEqual(code, 200)
        self.assertTrue(resp.get("ok"))
        self.assertGreaterEqual(resp.get("deletedFiles"), 2)

        # 驗證 .html 與 .md 一併被刪除
        self.assertFalse(html_file.exists())
        self.assertFalse(md_file.exists())
        self.assertFalse(chart_file.exists())

    def test_move_keeps_source_when_destination_has_same_file(self):
        src_folder = self.reports_dir / "來源"
        dest_folder = self.reports_dir / "目標"
        src_folder.mkdir()
        dest_folder.mkdir()
        html_file = src_folder / "2330_台積電(TW).html"
        html_file.write_text("來源版本", encoding="utf-8")
        (src_folder / "2330_台積電_4階段技術分析報告.md").write_text("來源 md", encoding="utf-8")
        existing = dest_folder / "2330_台積電(TW).html"
        existing.write_text("既有版本", encoding="utf-8")

        h_move = self._make_handler("/api/report/move", {
            "from": "來源", "to": "目標", "base": "2330_台積電(TW)", "code": "2330"
        })
        h_move.do_POST()
        code, resp = h_move._json.call_args[0]
        self.assertEqual(code, 409)
        self.assertFalse(resp["ok"])
        self.assertTrue(html_file.exists())
        self.assertEqual(existing.read_text(encoding="utf-8"), "既有版本")

    def test_list_folder_returns_md_info(self):
        folder = self.reports_dir / "CPO"
        folder.mkdir()
        (folder / "2455_全新(TW).html").write_text("html", encoding="utf-8")
        (folder / "2455_全新_4階段技術分析報告.md").write_text("md", encoding="utf-8")

        folders, reports = server.list_folder(folder)
        self.assertEqual(len(reports), 1)
        r = reports[0]
        self.assertEqual(r["code"], "2455")
        self.assertTrue(r["hasMd"])
        self.assertEqual(r["md"], "2455_全新_4階段技術分析報告.md")

    def test_batch_move_and_batch_delete(self):
        src_folder = self.reports_dir / "分類A"
        dest_folder = self.reports_dir / "分類B"
        src_folder.mkdir()
        dest_folder.mkdir()

        # 建立兩檔股票
        (src_folder / "2330_台積電(TW).html").write_text("2330", encoding="utf-8")
        (src_folder / "2330_台積電_4階段技術分析報告.md").write_text("2330 md", encoding="utf-8")
        (src_folder / "2454_聯發科(TW).html").write_text("2454", encoding="utf-8")
        (src_folder / "2454_聯發科_4階段技術分析報告.md").write_text("2454 md", encoding="utf-8")

        # 批次移動至分類B
        h_bmove = self._make_handler("/api/report/batch-move", {
            "to": "分類B",
            "items": [
                {"from": "分類A", "code": "2330", "base": "2330_台積電(TW)"},
                {"from": "分類A", "code": "2454", "base": "2454_聯發科(TW)"}
            ]
        })
        h_bmove.do_POST()
        code, resp = h_bmove._json.call_args[0]
        self.assertEqual(code, 200)
        self.assertTrue(resp.get("ok"))

        # 驗證全數移動到 分類B
        self.assertTrue((dest_folder / "2330_台積電(TW).html").exists())
        self.assertTrue((dest_folder / "2330_台積電_4階段技術分析報告.md").exists())
        self.assertTrue((dest_folder / "2454_聯發科(TW).html").exists())
        self.assertTrue((dest_folder / "2454_聯發科_4階段技術分析報告.md").exists())

        # 批次刪除
        h_bdel = self._make_handler("/api/report/batch-delete", {
            "items": [
                {"folder": "分類B", "code": "2330", "base": "2330_台積電(TW)"},
                {"folder": "分類B", "code": "2454", "base": "2454_聯發科(TW)"}
            ]
        })
        h_bdel.do_POST()
        code, resp = h_bdel._json.call_args[0]
        self.assertEqual(code, 200)
        self.assertTrue(resp.get("ok"))
        self.assertEqual(resp.get("deletedFiles"), 4)

        # 驗證兩檔股票的 .html 與 .md 均已被完全刪除
        self.assertFalse((dest_folder / "2330_台積電(TW).html").exists())
        self.assertFalse((dest_folder / "2330_台積電_4階段技術分析報告.md").exists())
        self.assertFalse((dest_folder / "2454_聯發科(TW).html").exists())
        self.assertFalse((dest_folder / "2454_聯發科_4階段技術分析報告.md").exists())


if __name__ == "__main__":
    unittest.main()
