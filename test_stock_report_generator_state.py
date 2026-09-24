import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import stock_report_generator as gen
import reports_state


class TestStockReportGeneratorStateIntegration(unittest.TestCase):
    """
    測試 stock_report_generator 在各種狀態檔情境下的輸出目標與安全阻斷行為。
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)
        self.temp_reports_dir = self.temp_root / "reports"
        self.temp_reports_dir.mkdir(parents=True, exist_ok=True)
        self.temp_state_file = self.temp_root / "reports_state.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_scenario_1_no_state_file_preserves_existing_behavior(self):
        """情境 1：無狀態檔時，保持既有輸出行為（子資料夾舊檔優先）。"""
        sub = self.temp_reports_dir / "散熱"
        sub.mkdir(parents=True, exist_ok=True)
        old_html = sub / "3324_雙鴻(TWO).html"
        old_html.write_text("old html", encoding="utf-8")

        with patch.object(gen, "REPORTS_DIR", str(self.temp_reports_dir)), \
             patch.object(gen, "STATE_FILE", str(self.temp_state_file)):
            target = gen.find_existing_report_dir("3324", "雙鴻")
            self.assertEqual(os.path.abspath(target), os.path.abspath(sub))

    def test_scenario_2_active_state_routes_to_state_folder_ignoring_mtime(self):
        """情境 2：有合法 active 狀態時，強制輸出至 state.folder，無視舊檔 mtime。"""
        # 在「舊資料夾」放一個時間更新的舊報表
        old_dir = self.temp_reports_dir / "舊資料夾"
        old_dir.mkdir(parents=True, exist_ok=True)
        old_file = old_dir / "3324_雙鴻(TWO).html"
        old_file.write_text("old", encoding="utf-8")
        os.utime(old_file, (2000000000, 2000000000))

        # 狀態檔指定 folder 為「散熱」
        state_data = {
            "version": 1,
            "updatedAt": "2026-09-21T08:00:00.000000Z",
            "stocks": {
                "3324": {
                    "name": "雙鴻",
                    "folder": "散熱",
                    "updatedAt": "2026-09-21T08:00:00.000000Z",
                    "deletedAt": None,
                }
            }
        }
        self.temp_state_file.write_text(json.dumps(state_data, ensure_ascii=False), encoding="utf-8")

        with patch.object(gen, "REPORTS_DIR", str(self.temp_reports_dir)), \
             patch.object(gen, "STATE_FILE", str(self.temp_state_file)):
            target = gen.find_existing_report_dir("3324", "雙鴻")
            expected = self.temp_reports_dir / "散熱"
            self.assertEqual(os.path.abspath(target), os.path.abspath(expected))

    def test_scenario_3_tombstone_safely_refuses_generation(self):
        """情境 3：有合法 tombstone 時，安全拒絕生成，不能以新檔讓已刪除個股復活。"""
        state_data = {
            "version": 1,
            "updatedAt": "2026-09-21T08:00:00.000000Z",
            "stocks": {
                "8996": {
                    "name": "高力",
                    "folder": "散熱",
                    "updatedAt": "2026-09-21T08:00:00.000000Z",
                    "deletedAt": "2026-09-21T08:30:00.000000Z",
                }
            }
        }
        self.temp_state_file.write_text(json.dumps(state_data, ensure_ascii=False), encoding="utf-8")

        with patch.object(gen, "REPORTS_DIR", str(self.temp_reports_dir)), \
             patch.object(gen, "STATE_FILE", str(self.temp_state_file)):
            with self.assertRaises(ValueError) as cm:
                gen.find_existing_report_dir("8996", "高力")
            self.assertIn("Tombstone", str(cm.exception))

            # 測試 run(sid) 入口亦立即安全終止回傳 False
            ok = gen.run("8996")
            self.assertFalse(ok)

    def test_scenario_4_corrupted_state_file_safely_fails(self):
        """情境 4：狀態檔損壞時安全失敗，不得退回 mtime 推測。"""
        self.temp_state_file.write_text("{ corrupted json ...", encoding="utf-8")

        # 即使某目錄下有舊檔，也不能退回 mtime 推測
        sub = self.temp_reports_dir / "半導體"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "2330_台積電(TW).html").write_text("old", encoding="utf-8")

        with patch.object(gen, "REPORTS_DIR", str(self.temp_reports_dir)), \
             patch.object(gen, "STATE_FILE", str(self.temp_state_file)):
            with self.assertRaises(ValueError) as cm:
                gen.find_existing_report_dir("2330", "台積電")
            self.assertIn("狀態檔損壞或結構不合法", str(cm.exception))

            ok = gen.run("2330")
            self.assertFalse(ok)

    def test_scenario_5_directory_or_symlink_state_file_safely_fails(self):
        """情境 5：狀態檔為資料夾或符號連結時安全失敗。"""
        self.temp_state_file.mkdir()

        with patch.object(gen, "REPORTS_DIR", str(self.temp_reports_dir)), \
             patch.object(gen, "STATE_FILE", str(self.temp_state_file)):
            with self.assertRaises(ValueError) as cm:
                gen.find_existing_report_dir("2330", "台積電")
            self.assertIn("不可為資料夾", str(cm.exception))

    def test_scenario_6_unmanaged_stock_fixed_to_root_and_prevents_auto_organize(self):
        """情境 6：未受管理個股固定輸出至 reports/ 根目錄，且存在狀態檔時 auto_organize 禁止自動搬移。"""
        state_data = {
            "version": 1,
            "updatedAt": "2026-09-21T08:00:00.000000Z",
            "stocks": {
                "2330": {
                    "name": "台積電",
                    "folder": "半導體",
                    "updatedAt": "2026-09-21T08:00:00.000000Z",
                    "deletedAt": None,
                }
            }
        }
        self.temp_state_file.write_text(json.dumps(state_data, ensure_ascii=False), encoding="utf-8")

        with patch.object(gen, "REPORTS_DIR", str(self.temp_reports_dir)), \
             patch.object(gen, "STATE_FILE", str(self.temp_state_file)):
            target = gen.find_existing_report_dir("9999", "未知新股")
            # 固定為 reports/ 根目錄
            self.assertEqual(os.path.abspath(target), os.path.abspath(self.temp_reports_dir))

            # 驗證 auto_organize_unfiled_reports 不會將根目錄未受管理檔案搬移
            unmanaged_file = self.temp_reports_dir / "9999_未知新股(TW).html"
            unmanaged_file.write_text("html", encoding="utf-8")
            moved = gen.auto_organize_unfiled_reports()
            self.assertEqual(moved, 0)
            self.assertTrue(unmanaged_file.exists())

    def test_managed_state_preserves_existing_report_variants(self):
        """受狀態檔管理時，產生器不得清掉其他資料夾的同代號歷史版本。"""
        old_dir = self.temp_reports_dir / "舊資料夾"
        new_dir = self.temp_reports_dir / "散熱"
        old_dir.mkdir(parents=True, exist_ok=True)
        new_dir.mkdir(parents=True, exist_ok=True)
        old_html = old_dir / "3324_雙鴻(TWO)_處置股.html"
        old_chart = old_dir / "3324_雙鴻(TWO)_處置股_chart.png"
        output_html = new_dir / "3324_雙鴻(TWO).html"
        old_html.write_text("old", encoding="utf-8")
        old_chart.write_bytes(b"chart")
        output_html.write_text("new", encoding="utf-8")

        with patch.object(gen, "REPORTS_DIR", str(self.temp_reports_dir)):
            gen.cleanup_stale_report_variants("3324", str(output_html), preserve_existing=True)

        self.assertTrue(old_html.exists())
        self.assertTrue(old_chart.exists())


if __name__ == "__main__":
    unittest.main()
