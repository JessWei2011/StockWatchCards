"""
StockCenter 報表狀態模型與唯讀掃描器單元測試 (test_reports_state.py)

測試範圍：
1. 狀態模型讀取、驗證、原子寫入與損壞防護。
2. reports/ 唯讀掃描器與初始狀態預覽產生器。
3. 涵蓋：空資料夾、正常單一個股、同代號多 HTML、跨資料夾重複、英文名稱缺漏、
   Tombstone 舊檔復活防護、零副作用驗證。
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import reports_state as rs


class TestReportsState(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.reports_dir = self.root / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.root / "reports_state.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    # ─────────────────────────────────────────────────────────────────────────
    # 狀態模型與原子寫入測試
    # ─────────────────────────────────────────────────────────────────────────

    def test_load_state_returns_empty_when_file_not_exist(self):
        """測試狀態檔不存在時回傳空結構，不自動產生檔案。"""
        state = rs.load_state(self.state_file)
        self.assertEqual(state["version"], 1)
        self.assertEqual(state["stocks"], {})
        self.assertFalse(self.state_file.exists())

    def test_save_and_load_state_roundtrip(self):
        """測試狀態檔原子寫入與讀取 roundtrip。"""
        state = {
            "version": 1,
            "updatedAt": "2026-09-21T12:00:00Z",
            "stocks": {
                "3324": {
                    "name": "雙鴻",
                    "folder": "散熱",
                    "updatedAt": "2026-09-21T12:00:00Z",
                    "deletedAt": None,
                },
                "2330": {
                    "name": "台積電",
                    "folder": "",
                    "updatedAt": "2026-09-21T11:00:00Z",
                    "deletedAt": "2026-09-21T12:30:00Z",
                },
            },
        }
        rs.save_state(state, self.state_file)
        self.assertTrue(self.state_file.exists())

        loaded = rs.load_state(self.state_file)
        self.assertEqual(loaded["version"], 1)
        self.assertEqual(len(loaded["stocks"]), 2)
        self.assertEqual(loaded["stocks"]["3324"]["folder"], "散熱")
        self.assertIsNone(loaded["stocks"]["3324"]["deletedAt"])
        self.assertEqual(loaded["stocks"]["2330"]["folder"], "")
        self.assertEqual(loaded["stocks"]["2330"]["deletedAt"], "2026-09-21T12:30:00Z")

    def test_load_state_safe_failure_on_corrupt_json(self):
        """測試損壞 JSON 時安全失敗，丟出 ValueError，且不覆蓋原檔。"""
        corrupted_content = "{ invalid json content ... "
        self.state_file.write_text(corrupted_content, encoding="utf-8")

        with self.assertRaises(ValueError) as ctx:
            rs.load_state(self.state_file)
        self.assertIn("JSON 語法損壞", str(ctx.exception))

        # 確認損壞內容依然未被覆寫
        self.assertEqual(self.state_file.read_text(encoding="utf-8"), corrupted_content)

    def test_save_state_rejects_invalid_schema_and_preserves_disk_file(self):
        """測試寫入非法結構時被攔截，不破壞磁碟既有檔案。"""
        valid_state = {
            "version": 1,
            "updatedAt": "2026-09-21T12:00:00Z",
            "stocks": {
                "3324": {
                    "name": "雙鴻",
                    "folder": "散熱",
                    "updatedAt": "2026-09-21T12:00:00Z",
                    "deletedAt": None,
                }
            },
        }
        rs.save_state(valid_state, self.state_file)

        # 嘗試寫入缺少必要欄位的資料
        invalid_state = {
            "version": 1,
            "updatedAt": "2026-09-21T12:00:00Z",
            "stocks": {
                "3324": {
                    "name": "雙鴻",
                    # 缺少 folder
                    "updatedAt": "2026-09-21T12:00:00Z",
                    "deletedAt": None,
                }
            },
        }
        with self.assertRaises(ValueError):
            rs.save_state(invalid_state, self.state_file)

        # 確認磁碟檔案未被修改
        loaded = rs.load_state(self.state_file)
        self.assertEqual(loaded["stocks"]["3324"]["folder"], "散熱")

    def test_folder_normalization(self):
        """測試資料夾路徑 POSIX 正規化與防呆。"""
        self.assertEqual(rs.normalize_folder_path(""), "")
        self.assertEqual(rs.normalize_folder_path("."), "")
        self.assertEqual(rs.normalize_folder_path("散熱"), "散熱")
        self.assertEqual(rs.normalize_folder_path(r"散熱\模組"), "散熱/模組")
        self.assertEqual(rs.normalize_folder_path("/散熱/模組/"), "散熱/模組")

        with self.assertRaises(ValueError):
            rs.normalize_folder_path("../outside")
        with self.assertRaises(ValueError):
            rs.normalize_folder_path("folder/../nested")
        with self.assertRaises(ValueError):
            rs.normalize_folder_path("folder:name")

    # ─────────────────────────────────────────────────────────────────────────
    # 唯讀掃描器與初始狀態預覽測試
    # ─────────────────────────────────────────────────────────────────────────

    def test_scan_empty_directory(self):
        """測試空目錄掃描。"""
        scan = rs.scan_reports_directory(self.reports_dir)
        self.assertEqual(scan, {})

        preview = rs.generate_initial_state_preview(self.reports_dir)
        self.assertEqual(preview["summary"]["totalScanned"], 0)
        self.assertEqual(preview["conflicts"], [])
        self.assertEqual(preview["warnings"], [])

    def test_scan_single_clean_stock(self):
        """測試單一正常個股（.html + .md + _chart.png）。"""
        sub = self.reports_dir / "散熱"
        sub.mkdir()
        (sub / "3324_雙鴻(TWO).html").write_text("html content", encoding="utf-8")
        (sub / "3324_雙鴻_4階段技術分析報告.md").write_text("md content", encoding="utf-8")
        (sub / "3324_雙鴻(TWO)_chart.png").write_bytes(b"png")

        scan = rs.scan_reports_directory(self.reports_dir)
        self.assertIn("3324", scan)
        info = scan["3324"]
        self.assertEqual(info["code"], "3324")
        self.assertEqual(info["resolvedName"], "雙鴻")
        self.assertTrue(info["hasChineseName"])
        self.assertFalse(info["needsChineseName"])
        self.assertEqual(info["folders"], ["散熱"])
        self.assertFalse(info["hasMultipleFolders"])
        self.assertEqual(len(info["files"]), 3)
        self.assertEqual(len(info["htmlFiles"]), 1)
        self.assertEqual(len(info["mdFiles"]), 1)
        self.assertEqual(len(info["chartFiles"]), 1)

        preview = rs.generate_initial_state_preview(self.reports_dir)
        self.assertEqual(preview["summary"]["conflictCount"], 0)
        self.assertEqual(preview["summary"]["warningCount"], 0)
        proposed_stock = preview["proposedState"]["stocks"]["3324"]
        self.assertEqual(proposed_stock["name"], "雙鴻")
        self.assertEqual(proposed_stock["folder"], "散熱")
        self.assertIsNone(proposed_stock["deletedAt"])

    def test_scan_multiple_html_variants_in_same_folder(self):
        """測試同代號在同資料夾有一般版與處置期間 HTML 變體。"""
        folder = self.reports_dir / "散熱"
        folder.mkdir()
        (folder / "3324_雙鴻(TWO).html").write_text("一般版", encoding="utf-8")
        (folder / "3324_雙鴻(TWO)_處置股.html").write_text("處置版", encoding="utf-8")
        (folder / "3324_雙鴻_4階段技術分析報告.md").write_text("md", encoding="utf-8")

        scan = rs.scan_reports_directory(self.reports_dir)
        self.assertIn("3324", scan)
        info = scan["3324"]
        self.assertTrue(info["hasMultipleHtmls"])
        self.assertEqual(len(info["htmlFiles"]), 2)

        preview = rs.generate_initial_state_preview(self.reports_dir)
        # 應納入警告，但無位置衝突仍可產生提案
        self.assertEqual(len(preview["warnings"]), 1)
        self.assertEqual(preview["warnings"][0]["type"], "multiple_html_variants")
        self.assertEqual(preview["summary"]["conflictCount"], 0)
        self.assertIn("3324", preview["proposedState"]["stocks"])

    def test_scan_cross_folder_location_conflict(self):
        """測試跨資料夾同代號重複，正確標示 location_conflict。"""
        folder_a = self.reports_dir / "散熱"
        folder_b = self.reports_dir / "自選"
        folder_a.mkdir()
        folder_b.mkdir()

        (folder_a / "3324_雙鴻(TWO).html").write_text("A", encoding="utf-8")
        (folder_b / "3324_雙鴻(TWO).html").write_text("B", encoding="utf-8")

        scan = rs.scan_reports_directory(self.reports_dir)
        info = scan["3324"]
        self.assertTrue(info["hasMultipleFolders"])
        self.assertEqual(sorted(info["folders"]), ["散熱", "自選"])

        preview = rs.generate_initial_state_preview(self.reports_dir)
        self.assertEqual(len(preview["conflicts"]), 1)
        conflict = preview["conflicts"][0]
        self.assertEqual(conflict["type"], "location_conflict")
        self.assertEqual(conflict["code"], "3324")
        # 衝突個股不應被自動、靜默寫入提案
        self.assertNotIn("3324", preview["proposedState"]["stocks"])

    def test_scan_missing_chinese_name_fallback_and_dict_priority(self):
        """測試字典缺代號時英文名稱回退標記，以及字典有代號時優先採用。"""
        (self.reports_dir / "1560_Kinik Company(TW).html").write_text("Kinik", encoding="utf-8")

        # 1. 字典無此代號：回退英文並標註 warning
        scan_no_dict = rs.scan_reports_directory(self.reports_dir, stock_name_dict={})
        info_no_dict = scan_no_dict["1560"]
        self.assertEqual(info_no_dict["resolvedName"], "Kinik Company")
        self.assertTrue(info_no_dict["needsChineseName"])

        preview_no_dict = rs.generate_initial_state_preview(self.reports_dir, stock_name_dict={})
        self.assertEqual(len(preview_no_dict["warnings"]), 1)
        self.assertEqual(preview_no_dict["warnings"][0]["type"], "missing_chinese_name")

        # 2. 字典有此代號：優先採用中文名
        name_dict = {"1560": "中砂"}
        scan_with_dict = rs.scan_reports_directory(self.reports_dir, stock_name_dict=name_dict)
        info_with_dict = scan_with_dict["1560"]
        self.assertEqual(info_with_dict["resolvedName"], "中砂")
        self.assertFalse(info_with_dict["needsChineseName"])

        preview_with_dict = rs.generate_initial_state_preview(self.reports_dir, stock_name_dict=name_dict)
        self.assertEqual(len(preview_with_dict["warnings"]), 0)
        self.assertEqual(preview_with_dict["proposedState"]["stocks"]["1560"]["name"], "中砂")

    def test_deleted_tombstone_prevents_resurrection_and_preserves_in_proposal(self):
        """測試狀態檔中已有 deletedAt tombstone 時，若磁碟有舊檔，標記衝突但保留原 tombstone 在提案中防止舊檔復活或遺失刪除紀錄。"""
        (self.reports_dir / "2330_台積電(TW).html").write_text("舊檔", encoding="utf-8")

        original_rec = {
            "name": "台積電",
            "folder": "",
            "updatedAt": "2026-09-20T10:00:00.000000Z",
            "deletedAt": "2026-09-20T11:00:00.000000Z",  # 已刪除墓碑
        }
        existing_state = {
            "version": 1,
            "updatedAt": "2026-09-20T10:00:00.000000Z",
            "stocks": {
                "2330": dict(original_rec),
            },
        }

        preview = rs.generate_initial_state_preview(self.reports_dir, existing_state=existing_state)
        self.assertEqual(len(preview["conflicts"]), 1)
        conflict = preview["conflicts"][0]
        self.assertEqual(conflict["type"], "tombstone_conflict")
        self.assertEqual(conflict["code"], "2330")
        
        # 依 AI_A 規範：原樣保留該筆 tombstone 在 proposedState.stocks，絕不可因舊檔遺失刪除紀錄
        self.assertIn("2330", preview["proposedState"]["stocks"])
        preserved_rec = preview["proposedState"]["stocks"]["2330"]
        self.assertEqual(preserved_rec["name"], original_rec["name"])
        self.assertEqual(preserved_rec["folder"], original_rec["folder"])
        self.assertEqual(preserved_rec["updatedAt"], original_rec["updatedAt"])
        self.assertEqual(preserved_rec["deletedAt"], original_rec["deletedAt"])

    def test_stock_code_strict_validation(self):
        """測試股票代號必須為 2-6 碼英數字且統一大寫，非法格式必須丟出 ValueError。"""
        valid_codes = ["3324", "MKT01", "AB12", "AA"]
        for code in valid_codes:
            state = {
                "version": 1,
                "updatedAt": "2026-09-21T12:00:00Z",
                "stocks": {
                    code: {
                        "name": "測試",
                        "folder": "",
                        "updatedAt": "2026-09-21T12:00:00Z",
                        "deletedAt": None,
                    }
                },
            }
            # 合法代號應順利通過
            rs.validate_state(state)

        invalid_codes = ["33-24", "1234567", "33 24", "33_24", "mkt01", "a", "12345678"]
        for code in invalid_codes:
            state = {
                "version": 1,
                "updatedAt": "2026-09-21T12:00:00Z",
                "stocks": {
                    code: {
                        "name": "測試",
                        "folder": "",
                        "updatedAt": "2026-09-21T12:00:00Z",
                        "deletedAt": None,
                    }
                },
            }
            with self.assertRaises(ValueError, msg=f"代號 '{code}' 應該要引發 ValueError"):
                rs.validate_state(state)

    def test_utc_iso8601_strict_validation(self):
        """測試 UTC ISO-8601 時間格式必須嚴格以 Z 結尾，非法格式必須丟出 ValueError。"""
        valid_times = [
            "2026-09-21T12:00:00Z",
            "2026-09-21T12:00:00.123456Z",
            "2026-09-21T12:00:00.1Z",
        ]
        for t in valid_times:
            state = {
                "version": 1,
                "updatedAt": t,
                "stocks": {
                    "3324": {
                        "name": "雙鴻",
                        "folder": "",
                        "updatedAt": t,
                        "deletedAt": t,
                    }
                },
            }
            rs.validate_state(state)

        invalid_times = [
            "2026-09-21T12:00:00+08:00",  # 不得接受 offset
            "2026-09-21T12:00:00",        # 缺少時區/Z
            "2026-09-21 12:00:00Z",       # 中間有空格
            "2026-02-30T12:00:00Z",       # 非法日期（2月無30日）
            "invalid-string",
            "2026-09-21T12:00:00.1234567Z", # 微秒超過 6 位
        ]
        for t in invalid_times:
            state = {
                "version": 1,
                "updatedAt": t,
                "stocks": {},
            }
            with self.assertRaises(ValueError, msg=f"時間 '{t}' 應該要引發 ValueError"):
                rs.validate_state(state)

    def test_scan_is_strictly_read_only_with_zero_side_effects(self):
        """測試掃描與預覽為純讀取，對 reports/ 目錄所有檔案的 mtime 與內容無任何更動。"""
        folder = self.reports_dir / "測試"
        folder.mkdir()
        file_path = folder / "2454_聯發科(TW).html"
        file_path.write_text("最初內容", encoding="utf-8")
        before_stat = file_path.stat()
        before_dir_entries = sorted(list(self.reports_dir.rglob("*")))

        # 執行多次掃描與預覽
        for _ in range(3):
            _ = rs.scan_reports_directory(self.reports_dir)
            _ = rs.generate_initial_state_preview(self.reports_dir)

        after_stat = file_path.stat()
        after_dir_entries = sorted(list(self.reports_dir.rglob("*")))

        # 驗證完全無新增/刪除檔案
        self.assertEqual(before_dir_entries, after_dir_entries)
        # 驗證 mtime、檔案大小與內容完全未變
        self.assertEqual(before_stat.st_mtime, after_stat.st_mtime)
        self.assertEqual(before_stat.st_size, after_stat.st_size)
        self.assertEqual(file_path.read_text(encoding="utf-8"), "最初內容")


class TestReportsStateBasisAndExclusiveCreate(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.reports_dir = self.root / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.root / "reports_state.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_calculate_preview_basis_reproducible_and_changes_on_content_diff(self):
        """測試基準指紋具有 Canonical 排序一致性，且在檔案內容變更時雜湊必定不同。"""
        sub = self.reports_dir / "半導體"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "2330_台積電(TW).html").write_text("html content 1", encoding="utf-8")
        (sub / "2330_台積電(TW)_chart.png").write_bytes(b"png content 1")

        name_dict = {"2330": "台積電"}
        basis1 = rs.calculate_preview_basis(self.reports_dir, self.state_file, name_dict)
        basis2 = rs.calculate_preview_basis(self.reports_dir, self.state_file, name_dict)

        # 兩次計算指紋完全一致
        self.assertEqual(basis1["fingerprint"], basis2["fingerprint"])
        self.assertEqual(basis1["reportsHash"], basis2["reportsHash"])
        self.assertEqual(basis1["stateFileStatus"], "absent")

        # 修改檔案內容後，reportsHash 必變
        (sub / "2330_台積電(TW).html").write_text("html content modified", encoding="utf-8")
        basis3 = rs.calculate_preview_basis(self.reports_dir, self.state_file, name_dict)
        self.assertNotEqual(basis1["reportsHash"], basis3["reportsHash"])
        self.assertNotEqual(basis1["fingerprint"], basis3["fingerprint"])

    def test_calculate_preview_basis_state_file_status(self):
        """測試狀態檔不存在為 absent，為資料夾為 non_regular_file，為檔案為 sha256。"""
        # 1. absent
        basis_absent = rs.calculate_preview_basis(self.reports_dir, self.state_file)
        self.assertEqual(basis_absent["stateFileStatus"], "absent")

        # 2. non_regular_file (directory)
        self.state_file.mkdir()
        basis_dir = rs.calculate_preview_basis(self.reports_dir, self.state_file)
        self.assertEqual(basis_dir["stateFileStatus"], "non_regular_file")
        self.state_file.rmdir()

        # 3. sha256 (regular file)
        self.state_file.write_text("content", encoding="utf-8")
        basis_file = rs.calculate_preview_basis(self.reports_dir, self.state_file)
        self.assertTrue(basis_file["stateFileStatus"].startswith("sha256:"))

    def test_create_initial_state_exclusive_success_and_rejects_overwrite(self):
        """測試排他首次建立：成功建立且無暫存檔殘留；若目標檔已存在則拋出 FileExistsError 且絕不覆寫。"""
        valid_state = {
            "version": 1,
            "updatedAt": "2026-09-21T00:00:00.000000Z",
            "stocks": {
                "2330": {
                    "name": "台積電",
                    "folder": "",
                    "updatedAt": "2026-09-21T00:00:00.000000Z",
                    "deletedAt": None,
                }
            },
        }
        # 1. 成功建立
        rs.create_initial_state_exclusive(valid_state, self.state_file)
        self.assertTrue(self.state_file.is_file())
        loaded = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(loaded["stocks"]["2330"]["name"], "台積電")

        # 確認同目錄沒有殘留 .tmp 檔案
        tmp_files = list(self.root.glob(".tmp_*"))
        self.assertEqual(len(tmp_files), 0)

        # 2. 第二次建立時目標已存在，必須排他失敗，原檔案內容不可被動到
        modified_state = dict(valid_state)
        modified_state["stocks"] = {"9999": {"name": "測試", "folder": "", "updatedAt": "2026-09-21T00:00:00.000000Z", "deletedAt": None}}
        with self.assertRaises(FileExistsError):
            rs.create_initial_state_exclusive(modified_state, self.state_file)

        # 原始狀態依然保持
        loaded_again = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertIn("2330", loaded_again["stocks"])
        self.assertNotIn("9999", loaded_again["stocks"])

    def test_create_initial_state_exclusive_rejects_directory(self):
        """測試目標為資料夾時拋出 ValueError。"""
        self.state_file.mkdir()
        valid_state = rs.create_empty_state()
        with self.assertRaises(ValueError):
            rs.create_initial_state_exclusive(valid_state, self.state_file)

    def test_create_initial_state_exclusive_fails_safely_when_hard_links_unsupported(self):
        """不支援排他發布時必須安全失敗，不能降級建立可能留下半成品的目標檔。"""
        valid_state = rs.create_empty_state()
        with patch.object(rs.os, "link", side_effect=OSError("hard links unsupported")):
            with self.assertRaises(OSError):
                rs.create_initial_state_exclusive(valid_state, self.state_file)

        self.assertFalse(self.state_file.exists())
        self.assertEqual(list(self.root.glob(".tmp_init_*.tmp")), [])

    def test_create_initial_state_exclusive_rejects_symbolic_link_when_supported(self):
        """符號連結不可被當成一般狀態檔路徑；無權建立連結的環境略過。"""
        target = self.root / "external_state.json"
        target.write_text("external", encoding="utf-8")
        try:
            self.state_file.symlink_to(target)
        except OSError as error:
            self.skipTest(f"目前環境無建立 symbolic link 權限: {error}")

        with self.assertRaises(ValueError):
            rs.create_initial_state_exclusive(rs.create_empty_state(), self.state_file)
        self.assertEqual(target.read_text(encoding="utf-8"), "external")

    def test_update_stock_name_dict_rejects_symbolic_link_when_supported(self):
        """字典補名不得跟隨 symbolic link 覆寫外部檔案。"""
        target = self.root / "external_dict.json"
        target.write_text('{"2330":"外部名稱"}', encoding="utf-8")
        dict_link = self.root / "stock_name_dict.json"
        try:
            dict_link.symlink_to(target)
        except OSError as error:
            self.skipTest(f"目前環境無建立 symbolic link 權限: {error}")

        with self.assertRaises(ValueError):
            rs.update_stock_name_dict_entry(dict_link, "1560", "中砂")
        self.assertEqual(target.read_text(encoding="utf-8"), '{"2330":"外部名稱"}')


class TestMergeReportsStatesAndSyncPreview(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.reports_dir = self.root / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_merge_unilateral_addition(self):
        local_state = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "半導體", "updatedAt": "2026-03-30T10:00:00Z", "deletedAt": None}
            }
        }
        incoming_state = {
            "version": 1,
            "updatedAt": "2026-03-30T11:00:00Z",
            "stocks": {
                "2317": {"name": "鴻海", "folder": "電子", "updatedAt": "2026-03-30T11:00:00Z", "deletedAt": None}
            }
        }
        result = rs.merge_reports_states(local_state, incoming_state)
        merged = result["mergedState"]
        self.assertIn("2330", merged["stocks"])
        self.assertIn("2317", merged["stocks"])
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["itemDecisions"]["2317"]["source"], "incoming")
        self.assertEqual(result["itemDecisions"]["2317"]["decision"], "incoming_only")

    def test_merge_active_different_moves_newer_wins(self):
        local_state = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "舊分類", "updatedAt": "2026-03-30T10:00:00Z", "deletedAt": None}
            }
        }
        incoming_state = {
            "version": 1,
            "updatedAt": "2026-03-30T11:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "新分類", "updatedAt": "2026-03-30T11:00:00Z", "deletedAt": None}
            }
        }
        result = rs.merge_reports_states(local_state, incoming_state)
        merged = result["mergedState"]
        self.assertEqual(merged["stocks"]["2330"]["folder"], "新分類")
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["itemDecisions"]["2330"]["source"], "incoming")
        self.assertEqual(result["itemDecisions"]["2330"]["decision"], "incoming_active_newer")

    def test_merge_active_concurrent_move_conflict(self):
        local_state = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "分類A", "updatedAt": "2026-03-30T10:00:00Z", "deletedAt": None}
            }
        }
        incoming_state = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "分類B", "updatedAt": "2026-03-30T10:00:00Z", "deletedAt": None}
            }
        }
        result = rs.merge_reports_states(local_state, incoming_state)
        self.assertNotIn("2330", result["mergedState"]["stocks"])
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(result["conflicts"][0]["type"], "concurrent_move_conflict")
        self.assertEqual(result["conflicts"][0]["code"], "2330")

    def test_merge_tombstone_wins_over_older_active(self):
        local_state = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "半導體", "updatedAt": "2026-03-30T09:00:00Z", "deletedAt": None}
            }
        }
        incoming_state = {
            "version": 1,
            "updatedAt": "2026-03-30T11:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "半導體", "updatedAt": "2026-03-30T11:00:00Z", "deleted": True, "deletedAt": "2026-03-30T10:00:00Z"}
            }
        }
        result = rs.merge_reports_states(local_state, incoming_state)
        merged = result["mergedState"]
        self.assertTrue(merged["stocks"]["2330"]["deleted"])
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["itemDecisions"]["2330"]["source"], "incoming")

    def test_merge_active_newer_than_tombstone_conflict(self):
        local_state = {
            "version": 1,
            "updatedAt": "2026-03-30T11:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "半導體", "updatedAt": "2026-03-30T11:00:00Z", "deletedAt": None}
            }
        }
        incoming_state = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "半導體", "updatedAt": "2026-03-30T10:00:00Z", "deleted": True, "deletedAt": "2026-03-30T10:00:00Z"}
            }
        }
        result = rs.merge_reports_states(local_state, incoming_state)
        self.assertNotIn("2330", result["mergedState"]["stocks"])
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(result["conflicts"][0]["type"], "delete_active_conflict")

    def test_merge_both_tombstones(self):
        local_state = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {
                "2330": {"name": "舊名", "folder": "半導體", "updatedAt": "2026-03-30T10:00:00Z", "deleted": True, "deletedAt": "2026-03-30T10:00:00Z"}
            }
        }
        incoming_state = {
            "version": 1,
            "updatedAt": "2026-03-30T11:00:00Z",
            "stocks": {
                "2330": {"name": "新名", "folder": "半導體", "updatedAt": "2026-03-30T11:00:00Z", "deleted": True, "deletedAt": "2026-03-30T11:00:00Z"}
            }
        }
        result = rs.merge_reports_states(local_state, incoming_state)
        merged = result["mergedState"]
        self.assertEqual(merged["stocks"]["2330"]["name"], "新名")
        self.assertEqual(result["conflicts"], [])

    def test_merge_invalid_inputs(self):
        with self.assertRaises(ValueError):
            rs.merge_reports_states(None, {})
        with self.assertRaises(ValueError):
            rs.merge_reports_states({"version": 1}, {"version": 1})
        with self.assertRaises(ValueError):
            rs.merge_reports_states({"stocks": {}}, {"stocks": {}})

    def test_generate_sync_preview_read_only_and_actions(self):
        # 建立磁碟檔案結構
        semi_dir = self.reports_dir / "半導體"
        semi_dir.mkdir(parents=True, exist_ok=True)
        t_html = semi_dir / "2330_台積電.html"
        t_html.write_text("dummy", encoding="utf-8")

        elec_dir = self.reports_dir / "電子"
        elec_dir.mkdir(parents=True, exist_ok=True)
        h_html = elec_dir / "2317_鴻海.html"
        h_html.write_text("dummy", encoding="utf-8")

        # 未受管檔案
        unmanaged_file = self.reports_dir / "9999_未知.html"
        unmanaged_file.write_text("dummy", encoding="utf-8")

        local_state = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "半導體", "updatedAt": "2026-03-30T10:00:00Z", "deletedAt": None},
                "2317": {"name": "鴻海", "folder": "電子", "updatedAt": "2026-03-30T10:00:00Z", "deletedAt": None},
                "2454": {"name": "聯發科", "folder": "IC設計", "updatedAt": "2026-03-30T10:00:00Z", "deletedAt": None} # 磁碟缺失
            }
        }
        # incoming 欲將 2330 搬移至 "先進製程"，並將 2317 刪除
        incoming_state = {
            "version": 1,
            "updatedAt": "2026-03-30T11:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "先進製程", "updatedAt": "2026-03-30T11:00:00Z", "deletedAt": None},
                "2317": {"name": "鴻海", "folder": "電子", "updatedAt": "2026-03-30T11:00:00Z", "deleted": True, "deletedAt": "2026-03-30T11:00:00Z"}
            }
        }

        # 記錄執行前的檔案狀態
        files_before = sorted([str(p.relative_to(self.reports_dir)) for p in self.reports_dir.rglob("*") if p.is_file()])

        preview = rs.generate_sync_preview(local_state, incoming_state, self.reports_dir)

        # 驗證純唯讀：磁碟檔案完全零修改
        files_after = sorted([str(p.relative_to(self.reports_dir)) for p in self.reports_dir.rglob("*") if p.is_file()])
        self.assertEqual(files_before, files_after)

        actions = preview["fileActions"]
        action_types = {a["action"] for a in actions}
        self.assertIn("move_candidate", action_types)
        self.assertIn("delete_candidate", action_types)
        self.assertIn("unmanaged_candidate", action_types)
        self.assertIn("missing_on_disk", action_types)

        summary = preview["fileSummary"]
        self.assertEqual(summary["movesCount"], 1)
        self.assertEqual(summary["deletionsCount"], 1)
        self.assertEqual(summary["unmanagedCount"], 1)
        self.assertEqual(summary["missingCount"], 1)

    def test_sync_preview_conflicts_do_not_produce_action_candidates(self):
        """同代號合併或位置衝突只能顯示衝突，不能另列為可搬移／可新增建議。"""
        folder_a = self.reports_dir / "分類A"
        folder_a.mkdir(parents=True, exist_ok=True)
        (folder_a / "2330_台積電(TW).html").write_text("html", encoding="utf-8")
        local_state = {
            "version": 1, "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {"2330": {"name": "台積電", "folder": "分類A", "updatedAt": "2026-03-30T10:00:00Z", "deletedAt": None}},
        }
        incoming_state = {
            "version": 1, "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {"2330": {"name": "台積電", "folder": "分類B", "updatedAt": "2026-03-30T10:00:00Z", "deletedAt": None}},
        }

        preview = rs.generate_sync_preview(local_state, incoming_state, self.reports_dir)
        self.assertTrue(any(c["type"] == "concurrent_move_conflict" for c in preview["conflicts"]))
        self.assertFalse(any(a["code"] == "2330" for a in preview["fileActions"]))

    def test_sync_preview_basis_includes_incoming_state_content(self):
        """外來狀態檔內容變動時，後續套用用的同步預覽基準必須不同。"""
        state_path = self.root / "reports_state.json"
        state_path.write_text(json.dumps({"version": 1, "updatedAt": "2026-03-30T10:00:00Z", "stocks": {} }), encoding="utf-8")
        incoming_a = {"version": 1, "updatedAt": "2026-03-30T10:00:00Z", "stocks": {}}
        incoming_b = {
            "version": 1, "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {"2330": {"name": "台積電", "folder": "半導體", "updatedAt": "2026-03-30T10:00:00Z", "deletedAt": None}},
        }
        basis_a = rs.calculate_sync_preview_basis(self.reports_dir, state_path, incoming_a)
        basis_b = rs.calculate_sync_preview_basis(self.reports_dir, state_path, incoming_b)
        self.assertNotEqual(basis_a["incomingStateHash"], basis_b["incomingStateHash"])
        self.assertNotEqual(basis_a["fingerprint"], basis_b["fingerprint"])


class TestSyncApplyTransactional(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.reports_dir = self.root / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "reports_state.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sync_preview_keeps_conflicts_for_manual_file_management(self):
        local_state = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "半導體", "updatedAt": "2026-03-30T10:00:00Z", "deletedAt": None}
            }
        }
        incoming_state = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "先進製程", "updatedAt": "2026-03-30T10:00:00Z", "deletedAt": None}
            }
        }
        preview = rs.generate_sync_preview(local_state, incoming_state, self.reports_dir)
        self.assertEqual(len(preview["conflicts"]), 1)
        self.assertEqual(preview["conflicts"][0]["code"], "2330")
        self.assertEqual(preview["itemDecisions"]["2330"]["decision"], "conflict")

    def test_execute_sync_plan_transactional_success(self):
        # 1. 準備檔案
        semi_dir = self.reports_dir / "半導體"
        semi_dir.mkdir(parents=True, exist_ok=True)
        html_2330 = semi_dir / "2330_台積電(TW).html"
        md_2330 = semi_dir / "2330_台積電_4階段技術分析報告.md"
        html_2330.write_text("html 2330", encoding="utf-8")
        md_2330.write_text("md 2330", encoding="utf-8")

        del_html = semi_dir / "9998_舊檔.html"
        del_html.write_text("to delete", encoding="utf-8")

        unmanaged_html = self.reports_dir / "9999_未受管.html"
        unmanaged_html.write_text("unmanaged", encoding="utf-8")

        # 2. 準備 file_actions 與 target_state
        file_actions = [
            {
                "code": "2330",
                "action": "move_candidate",
                "targetFolder": "先進製程",
                "files": ["半導體/2330_台積電(TW).html", "半導體/2330_台積電_4階段技術分析報告.md"]
            },
            {
                "code": "9998",
                "action": "delete_candidate",
                "files": ["半導體/9998_舊檔.html"]
            },
            {
                "code": "9999",
                "action": "unmanaged_candidate",
                "files": ["9999_未受管.html"]
            }
        ]

        target_state = {
            "version": 1,
            "updatedAt": "2026-03-30T12:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "先進製程", "updatedAt": "2026-03-30T12:00:00Z", "deletedAt": None},
                "9998": {"name": "舊檔", "folder": "半導體", "updatedAt": "2026-03-30T12:00:00Z", "deleted": True, "deletedAt": "2026-03-30T12:00:00Z"}
            }
        }

        # 3. 執行交易
        result = rs.execute_sync_plan_transactional(
            self.reports_dir, file_actions, target_state, self.state_path, temp_dir=self.root
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["movedCount"], 2)
        self.assertEqual(result["deletedCount"], 1)

        # 4. 驗證實體檔案：2330 搬移至 先進製程
        self.assertFalse(html_2330.exists())
        self.assertFalse(md_2330.exists())
        self.assertTrue((self.reports_dir / "先進製程" / "2330_台積電(TW).html").exists())
        self.assertTrue((self.reports_dir / "先進製程" / "2330_台積電_4階段技術分析報告.md").exists())

        # 5. 驗證刪除檔案已不存在
        self.assertFalse(del_html.exists())

        # 6. 驗證未受管檔案完全保留於原處
        self.assertTrue(unmanaged_html.exists())

        # 7. 驗證狀態檔正確更新
        self.assertTrue(self.state_path.exists())
        saved_state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved_state["stocks"]["2330"]["folder"], "先進製程")

    def test_execute_sync_plan_transactional_rollback_on_failure(self):
        semi_dir = self.reports_dir / "半導體"
        semi_dir.mkdir(parents=True, exist_ok=True)
        html_2330 = semi_dir / "2330_台積電(TW).html"
        html_2330.write_text("html 2330", encoding="utf-8")

        del_html = semi_dir / "9998_舊檔.html"
        del_html.write_text("to delete", encoding="utf-8")

        file_actions = [
            {
                "code": "2330",
                "action": "move_candidate",
                "targetFolder": "先進製程",
                "files": ["半導體/2330_台積電(TW).html"]
            },
            {
                "code": "9998",
                "action": "delete_candidate",
                "files": ["半導體/9998_舊檔.html"]
            }
        ]

        target_state = {
            "version": 1,
            "updatedAt": "2026-03-30T12:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "先進製程", "updatedAt": "2026-03-30T12:00:00Z", "deletedAt": None}
            }
        }

        # 模擬寫入狀態檔時發生 IOError
        with patch("reports_state.save_state", side_effect=IOError("模擬磁碟寫入中斷")):
            with self.assertRaises(IOError):
                rs.execute_sync_plan_transactional(
                    self.reports_dir, file_actions, target_state, self.state_path, temp_dir=self.root
                )

        # 驗證回滾：2330 仍保留在原處、9998 亦還原保留在原處
        self.assertTrue(html_2330.exists())
        self.assertEqual(html_2330.read_text(encoding="utf-8"), "html 2330")
        self.assertTrue(del_html.exists())
        self.assertEqual(del_html.read_text(encoding="utf-8"), "to delete")
        self.assertFalse((self.reports_dir / "先進製程" / "2330_台積電(TW).html").exists())
        self.assertFalse(self.state_path.exists())

    def test_execute_sync_plan_preflight_rejects_destination_collision_without_changes(self):
        source_dir = self.reports_dir / "半導體"
        target_dir = self.reports_dir / "先進製程"
        source_dir.mkdir()
        target_dir.mkdir()
        source_file = source_dir / "2330_台積電(TW).html"
        target_file = target_dir / "2330_台積電(TW).html"
        source_file.write_text("source", encoding="utf-8")
        target_file.write_text("existing", encoding="utf-8")
        target_state = {
            "version": 1, "updatedAt": "2026-03-30T12:00:00Z",
            "stocks": {"2330": {"name": "台積電", "folder": "先進製程", "updatedAt": "2026-03-30T12:00:00Z", "deletedAt": None}},
        }

        with self.assertRaises(FileExistsError):
            rs.execute_sync_plan_transactional(self.reports_dir, [{
                "action": "move_candidate", "targetFolder": "先進製程", "files": ["半導體/2330_台積電(TW).html"]
            }], target_state, self.state_path, temp_dir=self.root)

        self.assertEqual(source_file.read_text(encoding="utf-8"), "source")
        self.assertEqual(target_file.read_text(encoding="utf-8"), "existing")
        self.assertFalse(self.state_path.exists())

    def test_execute_sync_plan_restores_existing_state_on_write_failure(self):
        source_dir = self.reports_dir / "半導體"
        source_dir.mkdir()
        source_file = source_dir / "2330_台積電(TW).html"
        source_file.write_text("source", encoding="utf-8")
        original_state = {
            "version": 1, "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {"2330": {"name": "台積電", "folder": "半導體", "updatedAt": "2026-03-30T10:00:00Z", "deletedAt": None}},
        }
        self.state_path.write_text(json.dumps(original_state, ensure_ascii=False), encoding="utf-8")
        target_state = {
            "version": 1, "updatedAt": "2026-03-30T12:00:00Z",
            "stocks": {"2330": {"name": "台積電", "folder": "先進製程", "updatedAt": "2026-03-30T12:00:00Z", "deletedAt": None}},
        }

        with patch("reports_state.save_state", side_effect=IOError("write failed")):
            with self.assertRaises(IOError):
                rs.execute_sync_plan_transactional(self.reports_dir, [{
                    "action": "move_candidate", "targetFolder": "先進製程", "files": ["半導體/2330_台積電(TW).html"]
                }], target_state, self.state_path, temp_dir=self.root)

        self.assertTrue(source_file.exists())
        self.assertEqual(json.loads(self.state_path.read_text(encoding="utf-8")), original_state)

    def test_execute_sync_plan_reports_cleanup_failure_after_commit(self):
        source_dir = self.reports_dir / "半導體"
        source_dir.mkdir()
        source_file = source_dir / "2330_台積電(TW).html"
        source_file.write_text("source", encoding="utf-8")
        target_state = {
            "version": 1, "updatedAt": "2026-03-30T12:00:00Z",
            "stocks": {"2330": {"name": "台積電", "folder": "先進製程", "updatedAt": "2026-03-30T12:00:00Z", "deletedAt": None}},
        }

        with patch("reports_state.shutil.rmtree", side_effect=OSError("cleanup failed")):
            with self.assertRaises(rs.SyncCleanupError):
                rs.execute_sync_plan_transactional(self.reports_dir, [{
                    "action": "move_candidate", "targetFolder": "先進製程", "files": ["半導體/2330_台積電(TW).html"]
                }], target_state, self.state_path, temp_dir=self.root)

        self.assertTrue((self.reports_dir / "先進製程" / source_file.name).exists())
        self.assertEqual(json.loads(self.state_path.read_text(encoding="utf-8"))["stocks"]["2330"]["folder"], "先進製程")


class TestDuplicateCleanup(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.reports_dir = self.root / "reports"
        self.reports_dir.mkdir()
        self.folder = self.reports_dir / "半導體"
        self.folder.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_preview_selects_canonical_chinese_and_standard_html(self):
        (self.folder / "1560_Kinik Company(TW).html").write_text("english", encoding="utf-8")
        (self.folder / "1560_Kinik Company_4階段技術分析報告.md").write_text("english md", encoding="utf-8")
        chinese_html = self.folder / "1560_中砂(TW).html"
        chinese_html.write_text("chinese", encoding="utf-8")
        (self.folder / "1560_中砂_4階段技術分析報告.md").write_text("chinese md", encoding="utf-8")
        standard_html = self.folder / "3324_雙鴻(TWO).html"
        standard_html.write_text("standard", encoding="utf-8")
        disposal_html = self.folder / "3324_雙鴻(TWO)(處置期間0908-0914).html"
        disposal_html.write_text("disposal", encoding="utf-8")

        preview = rs.generate_duplicate_cleanup_preview(self.reports_dir, {"1560": "中砂", "3324": "雙鴻"})
        self.assertTrue(preview["isReadOnlyPreview"])
        self.assertEqual(preview["summary"]["groups"], 2)
        by_code = {item["code"]: item for item in preview["candidates"]}
        self.assertEqual(by_code["1560"]["keepFile"], "半導體/1560_中砂(TW).html")
        self.assertEqual(set(by_code["1560"]["archiveFiles"]), {
            "半導體/1560_Kinik Company(TW).html",
            "半導體/1560_Kinik Company_4階段技術分析報告.md",
        })
        self.assertEqual(by_code["3324"]["keepFile"], "半導體/3324_雙鴻(TWO).html")
        self.assertEqual(by_code["3324"]["archiveFiles"], ["半導體/3324_雙鴻(TWO)(處置期間0908-0914).html"])
        self.assertTrue(chinese_html.exists())
        self.assertTrue(disposal_html.exists())

    def test_archive_moves_only_previewed_files_to_recoverable_quarantine(self):
        keep_file = self.folder / "3324_雙鴻(TWO).html"
        duplicate_file = self.folder / "3324_雙鴻(TWO)(處置期間0908-0914).html"
        keep_file.write_text("keep", encoding="utf-8")
        duplicate_file.write_text("duplicate", encoding="utf-8")
        preview = rs.generate_duplicate_cleanup_preview(self.reports_dir, {"3324": "雙鴻"})

        result = rs.archive_duplicate_variants_transactional(
            self.reports_dir, preview["candidates"], self.root / "quarantine"
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["archivedCount"], 1)
        self.assertTrue(keep_file.exists())
        self.assertFalse(duplicate_file.exists())
        self.assertTrue((result["session"] / "半導體" / duplicate_file.name).exists())


if __name__ == "__main__":
    unittest.main()
