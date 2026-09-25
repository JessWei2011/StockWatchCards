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
        self.reports_temp_dir = Path(self.temp_dir.name) / ".reports_temp"
        self.reports_temp_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = Path(self.temp_dir.name) / "reports_state.json"
        self.patcher = patch.object(server, "REPORTS_DIR", self.reports_dir)
        self.patcher_state = patch.object(server, "REPORTS_STATE_FILE", self.state_file)
        self.patcher_temp = patch.object(server, "REPORTS_TEMP_DIR", self.reports_temp_dir)
        self.patcher.start()
        self.patcher_state.start()
        self.patcher_temp.start()

    def tearDown(self):
        self.patcher_temp.stop()
        self.patcher_state.stop()
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

    def test_delete_by_code_searches_nested_folders(self):
        folder = self.reports_dir / "IC設計"
        folder.mkdir()
        html_file = folder / "2324_仁寶(TW).html"
        md_file = folder / "2324_仁寶_4階段技術分析報告.md"
        html_file.write_text("<html>仁寶</html>", encoding="utf-8")
        md_file.write_text("# 2324 仁寶技術分析", encoding="utf-8")

        h_del = self._make_handler("/api/report/delete", {"code": "2324"})
        h_del.do_POST()
        status, payload = h_del._json.call_args[0]

        self.assertEqual(status, 200)
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("deletedFiles"), 2)
        self.assertFalse(html_file.exists())
        self.assertFalse(md_file.exists())

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



    # ─────────────────────────────────────────────────────────────────────────
    # 階段 2 規格測試：狀態同步、交易保證、Tombstone 與邊界防護
    # ─────────────────────────────────────────────────────────────────────────

    def test_no_state_file_maintains_legacy_behavior_and_does_not_create_state(self):
        """無狀態檔時 API 維持既有行為，且絕不偷偷建立狀態檔。"""
        src = self.reports_dir / "來源"
        dest = self.reports_dir / "目標"
        src.mkdir()
        dest.mkdir()
        (src / "2330_台積電(TW).html").write_text("html", encoding="utf-8")

        h_move = self._make_handler("/api/report/move", {
            "from": "來源", "to": "目標", "code": "2330", "base": "2330_台積電(TW)"
        })
        h_move.do_POST()
        code, resp = h_move._json.call_args[0]
        self.assertEqual(code, 200)
        self.assertTrue(resp["ok"])
        self.assertTrue((dest / "2330_台積電(TW).html").exists())
        self.assertFalse(self.state_file.exists(), "不得自動建立正式狀態檔")

    def test_corrupted_state_file_rejects_operations_and_preserves_files(self):
        """損壞狀態檔時 API 拒絕操作，原檔與實體檔均不變動。"""
        corrupt_data = "{ bad json"
        self.state_file.write_text(corrupt_data, encoding="utf-8")

        src = self.reports_dir / "分類"
        src.mkdir()
        html = src / "2330_台積電(TW).html"
        html.write_text("html", encoding="utf-8")

        h_del = self._make_handler("/api/report/delete", {
            "folder": "分類", "code": "2330", "base": "2330_台積電(TW)"
        })
        h_del.do_POST()
        code, resp = h_del._json.call_args[0]
        self.assertIn(code, (400, 500))
        self.assertFalse(resp["ok"])
        # 確認實體檔案未被刪除
        self.assertTrue(html.exists())
        # 確認狀態檔未被覆寫
        self.assertEqual(self.state_file.read_text(encoding="utf-8"), corrupt_data)

    def test_move_full_group_including_variants_and_updates_state(self):
        """同代號多 HTML 變體、MD、PNG 完整搬移，目的地衝突時零搬移，成功時同步更新狀態。"""
        import reports_state
        state = {
            "version": 1,
            "updatedAt": "2026-09-21T12:00:00Z",
            "stocks": {
                "3324": {
                    "name": "雙鴻",
                    "folder": "來源",
                    "updatedAt": "2026-09-21T12:00:00Z",
                    "deletedAt": None,
                }
            }
        }
        reports_state.save_state(state, self.state_file)

        src = self.reports_dir / "來源"
        dest = self.reports_dir / "目標"
        src.mkdir()
        dest.mkdir()

        (src / "3324_雙鴻(TWO).html").write_text("一般版", encoding="utf-8")
        (src / "3324_雙鴻(TWO)_處置股.html").write_text("處置版", encoding="utf-8")
        (src / "3324_雙鴻_4階段技術分析報告.md").write_text("md", encoding="utf-8")
        (src / "3324_雙鴻(TWO)_chart.png").write_bytes(b"chart")

        # 1. 搬移成功
        h_move = self._make_handler("/api/report/move", {
            "from": "來源", "to": "目標", "code": "3324", "base": "3324_雙鴻(TWO)"
        })
        h_move.do_POST()
        code, resp = h_move._json.call_args[0]
        self.assertEqual(code, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual(len(resp["moved"]), 4)

        # 驗證來源均不存在、目標全數存在
        for fn in ("3324_雙鴻(TWO).html", "3324_雙鴻(TWO)_處置股.html", "3324_雙鴻_4階段技術分析報告.md", "3324_雙鴻(TWO)_chart.png"):
            self.assertFalse((src / fn).exists())
            self.assertTrue((dest / fn).exists())

        # 驗證狀態檔已被同步更新
        updated_state = reports_state.load_state(self.state_file)
        self.assertEqual(updated_state["stocks"]["3324"]["folder"], "目標")
        self.assertIsNone(updated_state["stocks"]["3324"]["deletedAt"])

        # 2. 目的地衝突時零搬移
        src2 = self.reports_dir / "來源2"
        src2.mkdir()
        (src2 / "3324_雙鴻(TWO).html").write_text("新版", encoding="utf-8")
        (src2 / "3324_雙鴻_4階段技術分析報告.md").write_text("新 md", encoding="utf-8")

        h_move_conflict = self._make_handler("/api/report/move", {
            "from": "來源2", "to": "目標", "code": "3324", "base": "3324_雙鴻(TWO)"
        })
        h_move_conflict.do_POST()
        c_code, c_resp = h_move_conflict._json.call_args[0]
        self.assertEqual(c_code, 409)
        self.assertFalse(c_resp["ok"])
        # 驗證來源2之檔案完全未被搬走
        self.assertTrue((src2 / "3324_雙鴻(TWO).html").exists())
        self.assertTrue((src2 / "3324_雙鴻_4階段技術分析報告.md").exists())

    def test_move_rollback_on_state_save_failure(self):
        """搬移時若狀態寫入失敗，檔案完整 rollback 回來源目錄。"""
        import reports_state
        state = {
            "version": 1,
            "updatedAt": "2026-09-21T12:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "來源", "updatedAt": "2026-09-21T12:00:00Z", "deletedAt": None}
            }
        }
        reports_state.save_state(state, self.state_file)

        src = self.reports_dir / "來源"
        dest = self.reports_dir / "目標"
        src.mkdir()
        dest.mkdir()
        html = src / "2330_台積電(TW).html"
        md = src / "2330_台積電_4階段技術分析報告.md"
        html.write_text("html", encoding="utf-8")
        md.write_text("md", encoding="utf-8")

        with patch.object(server, "save_reports_state_atomic", side_effect=IOError("磁碟寫入失敗")):
            h_move = self._make_handler("/api/report/move", {
                "from": "來源", "to": "目標", "code": "2330", "base": "2330_台積電(TW)"
            })
            h_move.do_POST()
            code, resp = h_move._json.call_args[0]
            self.assertIn(code, (400, 500))
            self.assertFalse(resp["ok"])

        # 驗證檔案全部完整回滾回來源目錄
        self.assertTrue(html.exists())
        self.assertTrue(md.exists())
        self.assertFalse((dest / "2330_台積電(TW).html").exists())

    def test_delete_writes_tombstone_and_rollback_on_failure(self):
        """刪除寫入 tombstone；狀態寫入失敗時檔案完整還原且無 tombstone。"""
        import reports_state
        state = {
            "version": 1,
            "updatedAt": "2026-09-21T12:00:00Z",
            "stocks": {
                "2454": {"name": "聯發科", "folder": "IC設計", "updatedAt": "2026-09-21T12:00:00Z", "deletedAt": None}
            }
        }
        reports_state.save_state(state, self.state_file)

        folder = self.reports_dir / "IC設計"
        folder.mkdir()
        html = folder / "2454_聯發科(TW).html"
        md = folder / "2454_聯發科_4階段技術分析報告.md"
        html.write_text("html", encoding="utf-8")
        md.write_text("md", encoding="utf-8")

        # 1. 寫入失敗 rollback 測試
        with patch.object(server, "save_reports_state_atomic", side_effect=IOError("寫入失敗")):
            h_del_fail = self._make_handler("/api/report/delete", {
                "folder": "IC設計", "code": "2454", "base": "2454_聯發科(TW)"
            })
            h_del_fail.do_POST()
            code, resp = h_del_fail._json.call_args[0]
            self.assertIn(code, (400, 500))
            self.assertFalse(resp["ok"])

        # 檔案完整存在，且狀態檔未被加上 tombstone
        self.assertTrue(html.exists())
        self.assertTrue(md.exists())
        st = reports_state.load_state(self.state_file)
        self.assertIsNone(st["stocks"]["2454"]["deletedAt"])

        # 2. 正常刪除成功並寫入 tombstone
        h_del_ok = self._make_handler("/api/report/delete", {
            "folder": "IC設計", "code": "2454", "base": "2454_聯發科(TW)"
        })
        h_del_ok.do_POST()
        c_code, c_resp = h_del_ok._json.call_args[0]
        self.assertEqual(c_code, 200)
        self.assertTrue(c_resp["ok"])
        self.assertFalse(html.exists())
        self.assertFalse(md.exists())

        # 驗證墓碑紀錄已寫入
        st_del = reports_state.load_state(self.state_file)
        self.assertIsNotNone(st_del["stocks"]["2454"]["deletedAt"])
        self.assertEqual(st_del["stocks"]["2454"]["folder"], "IC設計")

    def test_folder_rename_syncs_nested_state_and_rolls_back_on_failure(self):
        """巢狀資料夾改名同步更新所有受影響狀態；狀態寫入失敗時資料夾改回原名。"""
        import reports_state
        state = {
            "version": 1,
            "updatedAt": "2026-09-21T12:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "半導體", "updatedAt": "2026-09-21T12:00:00Z", "deletedAt": None},
                "2454": {"name": "聯發科", "folder": "半導體/IC", "updatedAt": "2026-09-21T12:00:00Z", "deletedAt": None},
            }
        }
        reports_state.save_state(state, self.state_file)

        old_dir = self.reports_dir / "半導體"
        old_sub = old_dir / "IC"
        old_sub.mkdir(parents=True)

        # 1. 改名成功
        h_rename = self._make_handler("/api/folder/rename", {
            "path": "半導體", "newName": "晶圓半導體"
        })
        h_rename.do_POST()
        code, resp = h_rename._json.call_args[0]
        self.assertEqual(code, 200)
        self.assertTrue(resp["ok"])
        self.assertFalse(old_dir.exists())
        new_dir = self.reports_dir / "晶圓半導體"
        self.assertTrue(new_dir.exists())
        self.assertTrue((new_dir / "IC").exists())

        # 驗證狀態檔巢狀目錄一併更新
        st = reports_state.load_state(self.state_file)
        self.assertEqual(st["stocks"]["2330"]["folder"], "晶圓半導體")
        self.assertEqual(st["stocks"]["2454"]["folder"], "晶圓半導體/IC")

        # 2. 狀態寫入失敗 rollback 資料夾名稱
        with patch.object(server, "save_reports_state_atomic", side_effect=IOError("寫入失敗")):
            h_rename_fail = self._make_handler("/api/folder/rename", {
                "path": "晶圓半導體", "newName": "失敗測試"
            })
            h_rename_fail.do_POST()
            f_code, f_resp = h_rename_fail._json.call_args[0]
            self.assertIn(f_code, (400, 500))
            self.assertFalse(f_resp["ok"])

        # 資料夾名稱應維持 晶圓半導體
        self.assertTrue((self.reports_dir / "晶圓半導體").exists())
        self.assertFalse((self.reports_dir / "失敗測試").exists())

    def test_force_delete_folder_writes_all_tombstones_and_rolls_back_on_failure(self):
        """強制刪除含多代號之資料夾時為每個個股寫入 tombstone；狀態失敗時保留資料夾。"""
        import reports_state
        state = {
            "version": 1,
            "updatedAt": "2026-09-21T12:00:00Z",
            "stocks": {
                "1101": {"name": "台泥", "folder": "傳產", "updatedAt": "2026-09-21T12:00:00Z", "deletedAt": None},
                "1102": {"name": "亞泥", "folder": "傳產", "updatedAt": "2026-09-21T12:00:00Z", "deletedAt": None},
            }
        }
        reports_state.save_state(state, self.state_file)

        folder = self.reports_dir / "傳產"
        folder.mkdir()
        (folder / "1101_台泥(TW).html").write_text("1101", encoding="utf-8")
        (folder / "1102_亞泥(TW).html").write_text("1102", encoding="utf-8")

        # 1. 失敗 rollback
        with patch.object(server, "save_reports_state_atomic", side_effect=IOError("寫入失敗")):
            h_del_fail = self._make_handler("/api/folder/delete", {
                "path": "傳產", "force": True
            })
            h_del_fail.do_POST()
            code, resp = h_del_fail._json.call_args[0]
            self.assertIn(code, (400, 500))
            self.assertFalse(resp["ok"])

        # 資料夾依然存在且內容完整
        self.assertTrue(folder.is_dir())
        self.assertTrue((folder / "1101_台泥(TW).html").exists())
        st_fail = reports_state.load_state(self.state_file)
        self.assertIsNone(st_fail["stocks"]["1101"]["deletedAt"])

        # 2. 正常成功刪除
        h_del_ok = self._make_handler("/api/folder/delete", {
            "path": "傳產", "force": True
        })
        h_del_ok.do_POST()
        s_code, s_resp = h_del_ok._json.call_args[0]
        self.assertEqual(s_code, 200)
        self.assertTrue(s_resp["ok"])
        self.assertFalse(folder.exists())

        # 驗證兩檔個股皆已加上 tombstone
        st_ok = reports_state.load_state(self.state_file)
        self.assertIsNotNone(st_ok["stocks"]["1101"]["deletedAt"])
        self.assertIsNotNone(st_ok["stocks"]["1102"]["deletedAt"])

    def test_force_delete_nested_folder_records_full_reports_relative_path(self):
        """強制刪除巢狀資料夾時，tombstone 必須保存相對 reports/ 的完整來源路徑。"""
        import reports_state
        state = {
            "version": 1,
            "updatedAt": "2026-09-21T12:00:00Z",
            "stocks": {
                "3324": {"name": "雙鴻", "folder": "散熱/舊分類", "updatedAt": "2026-09-21T12:00:00Z", "deletedAt": None},
            },
        }
        reports_state.save_state(state, self.state_file)

        nested = self.reports_dir / "散熱" / "舊分類"
        nested.mkdir(parents=True)
        (nested / "3324_雙鴻(TWO).html").write_text("html", encoding="utf-8")

        handler = self._make_handler("/api/folder/delete", {"path": "散熱/舊分類", "force": True})
        handler.do_POST()
        status, response = handler._json.call_args[0]
        self.assertEqual(status, 200)
        self.assertTrue(response["ok"])

        saved = reports_state.load_state(self.state_file)
        self.assertEqual(saved["stocks"]["3324"]["folder"], "散熱/舊分類")
        self.assertIsNotNone(saved["stocks"]["3324"]["deletedAt"])

    def test_list_folder_deduplication_and_needs_review_badge(self):
        """list_folder 按代號去重，名稱以狀態檔為準，多 HTML 變體標記 needsReview。"""
        import reports_state
        state = {
            "version": 1,
            "updatedAt": "2026-09-21T12:00:00Z",
            "stocks": {
                "3324": {"name": "雙鴻科技", "folder": "散熱", "updatedAt": "2026-09-21T12:00:00Z", "deletedAt": None}
            }
        }
        reports_state.save_state(state, self.state_file)

        folder = self.reports_dir / "散熱"
        folder.mkdir()
        # 同代號雙版本
        (folder / "3324_雙鴻(TWO).html").write_text("一般", encoding="utf-8")
        (folder / "3324_雙鴻(TWO)_處置股.html").write_text("處置", encoding="utf-8")

        folders, reports = server.list_folder(folder)
        # 驗證去重後只剩 1 筆
        self.assertEqual(len(reports), 1)
        r = reports[0]
        self.assertEqual(r["code"], "3324")
        # 驗證名稱優先採用狀態檔中的「雙鴻科技」
        self.assertEqual(r["name"], "雙鴻科技")
        # 驗證標註 needsReview
        self.assertTrue(r["needsReview"])
        self.assertEqual(r["htmlVariantsCount"], 2)



    def test_new_stock_delete_records_accurate_folder_and_name_tombstone(self):
        """測試未在狀態檔中的新股票刪除時，tombstone 能精確記錄來源資料夾與名稱，而非根目錄或單純代號。"""
        import reports_state
        # 初始狀態檔存在，但不含 3324
        state = {
            "version": 1,
            "updatedAt": "2026-09-21T12:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "半導體", "updatedAt": "2026-09-21T12:00:00Z", "deletedAt": None}
            }
        }
        reports_state.save_state(state, self.state_file)

        folder = self.reports_dir / "散熱"
        folder.mkdir()
        (folder / "3324_雙鴻(TWO).html").write_text("html", encoding="utf-8")
        (folder / "3324_雙鴻_4階段技術分析報告.md").write_text("md", encoding="utf-8")

        h_del = self._make_handler("/api/report/delete", {
            "folder": "散熱", "code": "3324", "base": "3324_雙鴻(TWO)"
        })
        h_del.do_POST()
        code, resp = h_del._json.call_args[0]
        self.assertEqual(code, 200)
        self.assertTrue(resp["ok"])

        # 驗證 tombstone 資料夾為 散熱，名稱為 雙鴻
        st = reports_state.load_state(self.state_file)
        self.assertIn("3324", st["stocks"])
        rec = st["stocks"]["3324"]
        self.assertEqual(rec["folder"], "散熱", "來源資料夾必須精確記錄為 散熱")
        self.assertEqual(rec["name"], "雙鴻", "名稱不得退化成純代號")
        self.assertIsNotNone(rec["deletedAt"])

    def test_batch_move_isolation_failure_does_not_pollute_next_item(self):
        """測試批次搬移中第一項狀態儲存失敗時，其變更不會被第二項寫入狀態檔。"""
        import reports_state
        state = {
            "version": 1,
            "updatedAt": "2026-09-21T12:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "分類A", "updatedAt": "2026-09-21T12:00:00Z", "deletedAt": None},
                "2454": {"name": "聯發科", "folder": "分類A", "updatedAt": "2026-09-21T12:00:00Z", "deletedAt": None},
            }
        }
        reports_state.save_state(state, self.state_file)

        src = self.reports_dir / "分類A"
        dest = self.reports_dir / "分類B"
        src.mkdir()
        dest.mkdir()

        (src / "2330_台積電(TW).html").write_text("2330", encoding="utf-8")
        (src / "2454_聯發科(TW).html").write_text("2454", encoding="utf-8")

        # 模擬第一項儲存失敗，第二項儲存成功
        real_save = server.save_reports_state_atomic
        call_count = [0]
        def mock_save(st):
            call_count[0] += 1
            if call_count[0] == 1:
                raise IOError("第一項寫入失敗")
            return real_save(st)

        with patch.object(server, "save_reports_state_atomic", side_effect=mock_save):
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

        # 驗證實體檔案：第一項留在分類A，第二項搬到分類B
        self.assertTrue((src / "2330_台積電(TW).html").exists())
        self.assertTrue((dest / "2454_聯發科(TW).html").exists())

        # 驗證狀態檔：台積電仍為 分類A，聯發科更新為 分類B，台積電未被污染寫入
        final_state = reports_state.load_state(self.state_file)
        self.assertEqual(final_state["stocks"]["2330"]["folder"], "分類A")
        self.assertEqual(final_state["stocks"]["2454"]["folder"], "分類B")

    def test_batch_delete_isolation_failure_does_not_pollute_next_item(self):
        """測試批次刪除中第一項狀態儲存失敗時，其 tombstone 不會被第二項寫入狀態檔。"""
        import reports_state
        state = {
            "version": 1,
            "updatedAt": "2026-09-21T12:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "分類A", "updatedAt": "2026-09-21T12:00:00Z", "deletedAt": None},
                "2454": {"name": "聯發科", "folder": "分類A", "updatedAt": "2026-09-21T12:00:00Z", "deletedAt": None},
            }
        }
        reports_state.save_state(state, self.state_file)

        src = self.reports_dir / "分類A"
        src.mkdir()
        (src / "2330_台積電(TW).html").write_text("2330", encoding="utf-8")
        (src / "2454_聯發科(TW).html").write_text("2454", encoding="utf-8")

        real_save = server.save_reports_state_atomic
        call_count = [0]
        def mock_save(st):
            call_count[0] += 1
            if call_count[0] == 1:
                raise IOError("第一項刪除寫入失敗")
            return real_save(st)

        with patch.object(server, "save_reports_state_atomic", side_effect=mock_save):
            h_bdel = self._make_handler("/api/report/batch-delete", {
                "items": [
                    {"folder": "分類A", "code": "2330", "base": "2330_台積電(TW)"},
                    {"folder": "分類A", "code": "2454", "base": "2454_聯發科(TW)"}
                ]
            })
            h_bdel.do_POST()
            code, resp = h_bdel._json.call_args[0]
            self.assertEqual(code, 200)

        # 實體檔案：台積電還原保留在分類A，聯發科已刪除
        self.assertTrue((src / "2330_台積電(TW).html").exists())
        self.assertFalse((src / "2454_聯發科(TW).html").exists())

        # 狀態檔：台積電 deletedAt 必須仍為 None，聯發科 deletedAt 非空
        final_state = reports_state.load_state(self.state_file)
        self.assertIsNone(final_state["stocks"]["2330"]["deletedAt"])
        self.assertIsNotNone(final_state["stocks"]["2454"]["deletedAt"])

    def test_delete_cleanup_failure_reports_error_and_temp_outside_reports(self):
        """刪除暫存區位於 reports/ 外；若最終清理失敗，API 報錯且不宣稱成功。"""
        src = self.reports_dir / "散熱"
        src.mkdir()
        (src / "3324_雙鴻(TW).html").write_text("html", encoding="utf-8")

        with patch("shutil.rmtree", side_effect=OSError("Windows 檔案占用無法刪除")):
            h_del = self._make_handler("/api/report/delete", {
                "folder": "散熱", "code": "3324", "base": "3324_雙鴻(TW)"
            })
            h_del.do_POST()
            code, resp = h_del._json.call_args[0]
            # API 必須安全失敗並回報錯誤
            self.assertEqual(code, 500)
            self.assertFalse(resp["ok"])
            self.assertIn("暫存區清除失敗", resp["error"])
            # 確認暫存路徑確實位於 reports/ 目錄之外
            temp_path = Path(resp["tempPath"])
            self.assertTrue(temp_path.is_relative_to(self.reports_temp_dir))
            self.assertFalse(temp_path.is_relative_to(self.reports_dir))

    def test_batch_delete_cleanup_failure_reports_error_and_keeps_files_outside_reports(self):
        """批次刪除最終清理失敗時不可宣稱成功，也不可把暫存檔留在 reports/。"""
        src = self.reports_dir / "散熱"
        src.mkdir()
        report = src / "3324_雙鴻(TWO).html"
        report.write_text("html", encoding="utf-8")

        with patch("shutil.rmtree", side_effect=OSError("Windows 檔案占用無法刪除")):
            handler = self._make_handler("/api/report/batch-delete", {
                "items": [{"folder": "散熱", "code": "3324", "base": "3324_雙鴻(TWO)"}],
            })
            handler.do_POST()
            status, response = handler._json.call_args[0]

        self.assertEqual(status, 500)
        self.assertFalse(response["ok"])
        self.assertFalse(report.exists())
        self.assertEqual(len(response["cleanupErrors"]), 1)
        temp_path = Path(response["cleanupErrors"][0]["tempPath"])
        self.assertTrue(temp_path.is_relative_to(self.reports_temp_dir))
        self.assertFalse(temp_path.is_relative_to(self.reports_dir))


if __name__ == "__main__":
    unittest.main()
