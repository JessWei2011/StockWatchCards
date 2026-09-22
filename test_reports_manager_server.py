import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import reports_manager_server as server
import reports_state


@unittest.skip("AI 選股與研究卡功能已移除")
class TestReportsManagerServerFinalHandoff(unittest.TestCase):
    def setUp(self):
        # 確保環境不含 GEMINI_API_KEY
        self.orig_api_key = os.environ.pop("GEMINI_API_KEY", None)
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_root = Path(self.temp_dir.name)
        self.handoff_file = temp_root / "llm_manual_handoff.json"
        self.cards_file = temp_root / "ai_research_cards.json"
        self.evo_md_file = temp_root / "stock_winrate_ranking_evolution.md"
        self.cards_temp_file = temp_root / "ai_research_cards.tmp"

        # 測試使用正式榜單的唯讀快照，所有後續寫入都限制在暫存目錄。
        source_ranking = server.EVOLUTION_RANKING_FILE
        self.evo_md_file.write_text(source_ranking.read_text(encoding="utf-8"), encoding="utf-8")
        self.path_patches = [
            patch.object(server, "LLM_HANDOFF_FILE", self.handoff_file),
            patch.object(server, "AI_RESEARCH_CARDS_FILE", self.cards_file),
            patch.object(server, "AI_RESEARCH_TEMP_FILE", self.cards_temp_file),
            patch.object(server, "EVOLUTION_RANKING_FILE", self.evo_md_file),
        ]
        for path_patch in self.path_patches:
            path_patch.start()

    def tearDown(self):
        if self.orig_api_key is not None:
            os.environ["GEMINI_API_KEY"] = self.orig_api_key
        for path_patch in reversed(self.path_patches):
            path_patch.stop()
        self.temp_dir.cleanup()

    def _make_handler(self, path, body=None):
        handler = server.Handler.__new__(server.Handler)
        handler.path = path
        handler._read_json_body = MagicMock(return_value=body or {})
        handler._json = MagicMock()
        return handler

    @patch("subprocess.Popen")
    def test_start_ai_research_does_not_call_subprocess_or_gemini(self, mock_popen):
        """驗證按開始後只產生 Prompt，不呼叫 subprocess.Popen、Gemini REST 或任何 LLM API。"""
        handler = self._make_handler("/api/ai-research/start")
        handler.do_POST()

        handler._json.assert_called_once()
        status_code, payload = handler._json.call_args[0]
        self.assertEqual(status_code, 200)
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("status"), "awaiting_response")
        self.assertTrue(payload.get("prompt_id"))
        self.assertTrue(payload.get("prompt"))
        self.assertTrue(payload.get("expected_codes"))

        # 絕對沒有啟動任何子程序或發送 API 請求
        mock_popen.assert_not_called()
        self.assertNotIn("GEMINI_API_KEY", os.environ)

    def test_complete_workflow_without_gemini_api_key(self):
        """驗證完全不設定 GEMINI_API_KEY 仍可完成開始、貼回、驗證與保存。"""
        # 1. Start
        h_start = self._make_handler("/api/ai-research/start")
        h_start.do_POST()
        _, p_start = h_start._json.call_args[0]
        prompt_id = p_start["prompt_id"]
        expected_codes = p_start["expected_codes"]

        # 2. Build valid response
        cards = []
        for code in expected_codes:
            cards.append({
                "code": code,
                "name": f"股票{code}",
                "as_of_date": "2026-09-07",
                "data_cutoff": "2026-09-07",
                "sources": [{"url": "https://mops.twse.com.tw", "publisher": "公開資訊觀測站", "published_at": "2026-09-07", "source_type": "official"}],
                "supporting_facts": ["近期營收符合預期"],
                "counter_evidence_and_risks": ["同業競爭加劇，毛利可能面臨下行風險"],
                "questions_for_human_review": ["確認下次法說會資本支出指引"],
                "data_quality_flags": ["news_lag"],
                "disclaimer": "此研究卡不參與評分、排序或投資建議。"
            })
        resp_json_str = json.dumps({"cards": cards}, ensure_ascii=False)

        # 3. Submit
        h_submit = self._make_handler("/api/ai-research/submit", {"prompt_id": prompt_id, "response": resp_json_str})
        h_submit.do_POST()
        status_code, p_submit = h_submit._json.call_args[0]
        self.assertEqual(status_code, 200)
        self.assertTrue(p_submit["ok"])
        self.assertEqual(p_submit["status"], "completed")
        self.assertEqual(p_submit["saved_count"], len(expected_codes))

        # Check saved cards file
        self.assertTrue(self.cards_file.exists())
        saved_cards = json.loads(self.cards_file.read_text(encoding="utf-8"))
        self.assertEqual(len(saved_cards), len(expected_codes))

    def test_submit_with_markdown_code_fence(self):
        """驗證以 Markdown ```json ... ``` code fence 包住的合法回覆可正常解析並保存。"""
        h_start = self._make_handler("/api/ai-research/start")
        h_start.do_POST()
        _, p_start = h_start._json.call_args[0]
        prompt_id = p_start["prompt_id"]
        expected_codes = p_start["expected_codes"]

        cards = [{
            "code": c,
            "name": f"名稱{c}",
            "counter_evidence_and_risks": ["客戶砍單警訊"],
            "supporting_facts": [],
            "questions_for_human_review": [],
            "sources": [],
            "data_quality_flags": ["unknown"],
            "disclaimer": "此研究卡不參與評分、排序或投資建議。"
        } for c in expected_codes]

        raw_fence_text = f"```json\n{json.dumps({'cards': cards})}\n```"

        h_submit = self._make_handler("/api/ai-research/submit", {"prompt_id": prompt_id, "response": raw_fence_text})
        h_submit.do_POST()
        status_code, p_submit = h_submit._json.call_args[0]
        self.assertEqual(status_code, 200)
        self.assertTrue(p_submit["ok"])
        self.assertEqual(p_submit["status"], "completed")

    def test_submit_rejects_invalid_json_missing_fields_and_order_mismatch(self):
        """驗證 JSON 語法錯誤、缺欄位、漏股票、順序不符及無風險項目均會被嚴格拒絕。"""
        h_start = self._make_handler("/api/ai-research/start")
        h_start.do_POST()
        _, p_start = h_start._json.call_args[0]
        prompt_id = p_start["prompt_id"]
        expected_codes = p_start["expected_codes"]

        # Case 1: JSON 語法錯誤
        h1 = self._make_handler("/api/ai-research/submit", {"prompt_id": prompt_id, "response": "{invalid_json}"})
        h1.do_POST()
        self.assertEqual(h1._json.call_args[0][0], 400)
        self.assertIn("JSON 語法錯誤", h1._json.call_args[0][1]["error"])

        # Case 2: 缺少 cards 陣列
        h2 = self._make_handler("/api/ai-research/submit", {"prompt_id": prompt_id, "response": json.dumps({"data": []})})
        h2.do_POST()
        self.assertEqual(h2._json.call_args[0][0], 400)
        self.assertIn("缺少 'cards' 陣列", h2._json.call_args[0][1]["error"])

        # Case 3: 缺少某一檔股票 (漏股)
        if len(expected_codes) > 1:
            partial_cards = [{"code": expected_codes[0], "name": "測試", "counter_evidence_and_risks": ["風險"]}]
            h3 = self._make_handler("/api/ai-research/submit", {"prompt_id": prompt_id, "response": json.dumps({"cards": partial_cards})})
            h3.do_POST()
            self.assertEqual(h3._json.call_args[0][0], 400)
            self.assertIn("缺少股票", h3._json.call_args[0][1]["error"])

        # Case 4: 股票順序不符
        if len(expected_codes) > 1:
            reversed_codes = list(reversed(expected_codes))
            reversed_cards = [{"code": c, "name": f"測試{c}", "counter_evidence_and_risks": ["風險"]} for c in reversed_codes]
            h4 = self._make_handler("/api/ai-research/submit", {"prompt_id": prompt_id, "response": json.dumps({"cards": reversed_cards})})
            h4.do_POST()
            self.assertEqual(h4._json.call_args[0][0], 400)
            self.assertIn("股票順序", h4._json.call_args[0][1]["error"])

        # Case 5: 缺少 counter_evidence_and_risks (違反反證紀律)
        no_risk_cards = [{"code": c, "name": f"測試{c}", "counter_evidence_and_risks": []} for c in expected_codes]
        h5 = self._make_handler("/api/ai-research/submit", {"prompt_id": prompt_id, "response": json.dumps({"cards": no_risk_cards})})
        h5.do_POST()
        self.assertEqual(h5._json.call_args[0][0], 400)
        self.assertIn("缺少反證與風險提示", h5._json.call_args[0][1]["error"])

    def test_submit_rejects_expired_prompt_id(self):
        """驗證過期或不匹配的 prompt_id 會被拒絕。"""
        # Start first
        h_start = self._make_handler("/api/ai-research/start")
        h_start.do_POST()

        # Submit with wrong prompt_id
        h_submit = self._make_handler("/api/ai-research/submit", {
            "prompt_id": "outdated_id_999",
            "response": json.dumps({"cards": []})
        })
        h_submit.do_POST()
        status_code, p_submit = h_submit._json.call_args[0]
        self.assertEqual(status_code, 400)
        self.assertIn("Prompt 已更新", p_submit["error"])

    def test_validation_failure_preserves_existing_cards(self):
        """驗證當 AI 回覆驗證失敗時，既有之有效研究卡檔案絕不被覆寫或清空。"""
        original_cards = [{"code": "2330", "name": "台積電", "counter_evidence_and_risks": ["舊有風險"]}]
        self.cards_file.write_text(json.dumps(original_cards, ensure_ascii=False), encoding="utf-8")

        h_start = self._make_handler("/api/ai-research/start")
        h_start.do_POST()
        _, p_start = h_start._json.call_args[0]

        # 送出錯誤回覆
        h_submit = self._make_handler("/api/ai-research/submit", {
            "prompt_id": p_start["prompt_id"],
            "response": "invalid json content"
        })
        h_submit.do_POST()
        self.assertEqual(h_submit._json.call_args[0][0], 400)

        # 檢查原始卡片檔案依然完好
        cards_on_disk = json.loads(self.cards_file.read_text(encoding="utf-8"))
        self.assertEqual(cards_on_disk, original_cards)

    def test_successful_research_keeps_quantitative_ranking_unchanged(self):
        """驗證成功保存 AI 研究卡後，量化榜單的股票代號、順序與分數 100% 保持一致，不受任何影響。"""
        as_of_date, candidates_before = server.load_evolution_candidates()
        self.assertTrue(len(candidates_before) > 0)

        # Start and submit
        h_start = self._make_handler("/api/ai-research/start")
        h_start.do_POST()
        _, p_start = h_start._json.call_args[0]

        cards = [{
            "code": c["code"],
            "name": c["name"],
            "as_of_date": as_of_date,
            "data_cutoff": as_of_date,
            "sources": [],
            "supporting_facts": [],
            "counter_evidence_and_risks": [f"風險提示：{c['name']}面臨市場修正"],
            "questions_for_human_review": [],
            "data_quality_flags": ["unknown"],
            "disclaimer": "此研究卡不參與評分、排序或投資建議。"
        } for c in candidates_before]

        h_submit = self._make_handler("/api/ai-research/submit", {
            "prompt_id": p_start["prompt_id"],
            "response": json.dumps({"cards": cards})
        })
        h_submit.do_POST()
        self.assertEqual(h_submit._json.call_args[0][0], 200)

        # 再次讀取榜單候選與分數
        _, candidates_after = server.load_evolution_candidates()
        self.assertEqual(len(candidates_before), len(candidates_after))
        for b, a in zip(candidates_before, candidates_after):
            self.assertEqual(b["code"], a["code"])
            self.assertEqual(b["name"], a["name"])
            self.assertEqual(b["price"], a["price"])
            self.assertEqual(b["today_pct"], a["today_pct"])
            self.assertEqual(b["score"], a["score"])

    def test_html_contains_three_column_grid_toast_and_no_stale_texts(self):
        """驗證前端 HTML 包含 1 列 x 3 欄、Toast、且無多階段或配額等過期文字。"""
        html_path = server.ROOT_DIR / "reports_manager.html"
        html_text = html_path.read_text(encoding="utf-8")

        # 1. 包含 .ai-research-grid 及 1fr 1.4fr 1fr
        self.assertIn(".ai-research-grid", html_text)
        self.assertIn("1fr 1.4fr 1fr", html_text)
        self.assertIn("@media (max-width: 900px)", html_text)

        # 2. 包含 Toast 函式
        self.assertIn("function showToast(", html_text)


class TestAiStockPickingRemoval(unittest.TestCase):
    def _make_handler(self, path):
        handler = server.Handler.__new__(server.Handler)
        handler.path = path
        handler._read_json_body = MagicMock(return_value={})
        handler._json = MagicMock()
        return handler

    def test_ai_stock_picking_endpoints_are_disabled(self):
        for path in ("/api/batch-scanner-evolution", "/api/ai-research/start", "/api/ai-research/submit"):
            handler = self._make_handler(path)
            handler.do_POST()
            self.assertEqual(handler._json.call_args[0][0], 410)
            self.assertEqual(handler._json.call_args[0][1]["error"], "AI 選股功能已移除")

    def test_ranking_page_has_read_only_four_board_snapshot(self):
        html_text = (server.ROOT_DIR / "reports_manager.html").read_text(encoding="utf-8")
        self.assertIn("四大榜單快照", html_text)
        start = html_text.index("function renderWinrateRankingUI")
        renderer = html_text[start:html_text.index("    return;", start)]
        self.assertNotIn("AI 獨有勝率", renderer)
        self.assertNotIn("onclick=", renderer)

    @patch.object(server.requests, "get")
    def test_rss_news_parses_stock_items_without_scoring(self, mock_get):
        mock_response = MagicMock()
        now_pub = server.datetime.datetime.now(server.datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        mock_response.content = f'''<?xml version="1.0"?><rss><channel><item><title>2330 test announcement</title><link>https://example.com/news</link><pubDate>{now_pub}</pubDate><source>Example News</source></item></channel></rss>'''.encode('utf-8')
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response
        server.RSS_CACHE.clear()

        items = server.fetch_rss_news(["2330"])

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["code"], "2330")
        self.assertEqual(items[0]["source"], "Example News")
        self.assertIn("label", items[0])

    @patch.object(server, "get_gemini_api_key", return_value="test-key")
    @patch.object(server.requests, "post")
    def test_gemini_rss_summary_is_structured_and_not_a_score(self, mock_post, _mock_key):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"candidates": [{"content": {"parts": [{"text": '{"summary":"公司發布營收資訊","facts":["來源提及營收"],"watch_items":["待確認細節"],"source_indices":[1]}'}]}}]}
        mock_post.return_value = mock_response
        server.RSS_SUMMARY_CACHE.clear()

        summary = server.summarize_rss_with_gemini("2330", [{"title": "營收公告", "source": "Example", "published": "today"}])

        self.assertEqual(summary["summary"], "公司發布營收資訊")
        self.assertEqual(summary["source_indices"], [1])
        self.assertNotIn("score", summary)


class TestReportsStateInitPreviewAPI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)
        self.temp_reports_dir = self.temp_root / "reports"
        self.temp_reports_dir.mkdir(parents=True, exist_ok=True)
        self.temp_state_file = self.temp_root / "reports_state.json"

        self.patches = [
            patch.object(server, "REPORTS_DIR", self.temp_reports_dir),
            patch.object(server, "REPORTS_STATE_FILE", self.temp_state_file),
            patch.object(server, "ROOT_DIR", self.temp_root),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp_dir.cleanup()

    def _make_handler(self, path="/api/reports-state/init-preview"):
        handler = server.Handler.__new__(server.Handler)
        handler.path = path
        handler._read_json_body = MagicMock(return_value={})
        handler._json = MagicMock()
        return handler

    def test_init_preview_no_state_file(self):
        """1. 無狀態檔：200、ok=true、hasExistingState=false、stateFilePath == 'reports_state.json'，並正確含掃描出的個股；呼叫前後狀態檔仍不存在。"""
        sub = self.temp_reports_dir / "半導體"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "2330_台積電(TW).html").write_text("dummy html", encoding="utf-8")
        (sub / "2330_台積電(TW)_chart.png").write_bytes(b"dummy png")

        self.assertFalse(self.temp_state_file.exists())

        handler = self._make_handler()
        handler.do_GET()

        handler._json.assert_called_once()
        status_code, payload = handler._json.call_args[0]
        self.assertEqual(status_code, 200)
        self.assertTrue(payload.get("ok"))
        self.assertFalse(payload.get("hasExistingState"))
        self.assertEqual(payload.get("stateFilePath"), "reports_state.json")
        self.assertNotIn("existingStateVersion", payload)
        self.assertNotIn("existingStateUpdatedAt", payload)
        self.assertNotIn("existingStockCount", payload)

        preview = payload.get("preview", {})
        self.assertIn("2330", preview.get("proposedState", {}).get("stocks", {}))
        self.assertEqual(preview["proposedState"]["stocks"]["2330"]["folder"], "半導體")
        self.assertEqual(preview["summary"]["totalScanned"], 1)

        # 呼叫後狀態檔依然不存在（零副作用）
        self.assertFalse(self.temp_state_file.exists())

    def test_init_preview_with_existing_valid_state_preserves_tombstone(self):
        """2. 合法狀態檔：200、hasExistingState=true、三個 existingState* 欄位正確；既有 tombstone 遇到磁碟舊檔時，同時有 tombstone_conflict，且 preview.proposedState.stocks[code] 的 tombstone 欄位完全不變。"""
        valid_state = {
            "version": 1,
            "updatedAt": "2026-09-20T12:00:00.000000Z",
            "stocks": {
                "3324": {
                    "name": "雙鴻",
                    "folder": "散熱",
                    "updatedAt": "2026-09-20T12:00:00.000000Z",
                    "deletedAt": "2026-09-20T12:00:00.000000Z",
                }
            }
        }
        self.temp_state_file.write_text(json.dumps(valid_state, ensure_ascii=False, indent=2), encoding="utf-8")

        sub = self.temp_reports_dir / "散熱"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "3324_雙鴻(TWO).html").write_text("old html", encoding="utf-8")

        handler = self._make_handler()
        handler.do_GET()

        handler._json.assert_called_once()
        status_code, payload = handler._json.call_args[0]
        self.assertEqual(status_code, 200)
        self.assertTrue(payload.get("ok"))
        self.assertTrue(payload.get("hasExistingState"))
        self.assertEqual(payload.get("existingStateVersion"), 1)
        self.assertEqual(payload.get("existingStateUpdatedAt"), "2026-09-20T12:00:00.000000Z")
        self.assertEqual(payload.get("existingStockCount"), 1)

        preview = payload.get("preview", {})
        conflicts = preview.get("conflicts", [])
        tombstone_conflicts = [c for c in conflicts if c.get("type") == "tombstone_conflict"]
        self.assertEqual(len(tombstone_conflicts), 1)
        self.assertEqual(tombstone_conflicts[0]["code"], "3324")

        # tombstone 欄位完全不變
        preserved_stock = preview.get("proposedState", {}).get("stocks", {}).get("3324")
        self.assertIsNotNone(preserved_stock)
        self.assertEqual(preserved_stock["name"], "雙鴻")
        self.assertEqual(preserved_stock["folder"], "散熱")
        self.assertEqual(preserved_stock["updatedAt"], "2026-09-20T12:00:00.000000Z")
        self.assertEqual(preserved_stock["deletedAt"], "2026-09-20T12:00:00.000000Z")

    def test_init_preview_corrupted_state_file_returns_422(self):
        """3. 損壞狀態檔：422、ok=false、corrupted=true、沒有 preview；呼叫前後原始位元組完全相同，且不洩露本機絕對路徑。"""
        corrupted_bytes = b'{"version": 1, "stocks": broken json'
        self.temp_state_file.write_bytes(corrupted_bytes)

        handler = self._make_handler()
        handler.do_GET()

        handler._json.assert_called_once()
        status_code, payload = handler._json.call_args[0]
        self.assertEqual(status_code, 422)
        self.assertFalse(payload.get("ok"))
        self.assertTrue(payload.get("corrupted"))
        self.assertNotIn("preview", payload)
        self.assertIn("error", payload)
        # 錯誤訊息中不得包含暫存目錄的絕對路徑
        self.assertNotIn(str(self.temp_root), payload["error"])

        # 呼叫前後檔案內容與位元組完全一致
        self.assertEqual(self.temp_state_file.read_bytes(), corrupted_bytes)

    def test_init_preview_state_file_is_directory_returns_422(self):
        """驗收修正 005：reports_state.json 路徑存在但為資料夾時，必須安全失敗回傳 422，不回傳 preview，且資料夾仍存在。"""
        self.temp_state_file.mkdir(parents=True, exist_ok=True)

        handler = self._make_handler()
        handler.do_GET()

        handler._json.assert_called_once()
        status_code, payload = handler._json.call_args[0]
        self.assertEqual(status_code, 422)
        self.assertFalse(payload.get("ok"))
        self.assertTrue(payload.get("corrupted"))
        self.assertNotIn("preview", payload)
        self.assertEqual(payload.get("error"), "狀態檔路徑不是一般檔案")

        # 呼叫後該資料夾仍存在，未被刪除或改名
        self.assertTrue(self.temp_state_file.is_dir())

    def test_init_preview_invalid_schema_state_file_returns_422(self):
        """驗收修正 005：reports_state.json 結構不合法（如小寫代號或非 Z 時間）時回傳 422、無 preview，且原始位元組完全不變。"""
        invalid_schema_state = {
            "version": 1,
            "updatedAt": "2026-09-20T12:00:00+08:00",
            "stocks": {
                "2330": {
                    "name": "台積電",
                    "folder": "",
                    "updatedAt": "2026-09-20T12:00:00+08:00",
                    "deletedAt": None,
                }
            }
        }
        raw_bytes = json.dumps(invalid_schema_state, ensure_ascii=False, indent=2).encode("utf-8")
        self.temp_state_file.write_bytes(raw_bytes)

        handler = self._make_handler()
        handler.do_GET()

        handler._json.assert_called_once()
        status_code, payload = handler._json.call_args[0]
        self.assertEqual(status_code, 422)
        self.assertFalse(payload.get("ok"))
        self.assertTrue(payload.get("corrupted"))
        self.assertNotIn("preview", payload)
        self.assertNotIn(str(self.temp_root), payload.get("error", ""))

        # 呼叫前後原始位元組完全相同
        self.assertEqual(self.temp_state_file.read_bytes(), raw_bytes)

    def test_init_preview_location_conflict_excluded_from_proposed_state(self):
        """4. 跨資料夾重複：同代號出現在兩資料夾時有 location_conflict，且該代號不在 proposedState.stocks。"""
        dir_a = self.temp_reports_dir / "散熱"
        dir_b = self.temp_reports_dir / "未分類"
        dir_a.mkdir(parents=True, exist_ok=True)
        dir_b.mkdir(parents=True, exist_ok=True)
        (dir_a / "8996_高力(TW).html").write_text("html a", encoding="utf-8")
        (dir_b / "8996_高力(TW).html").write_text("html b", encoding="utf-8")

        handler = self._make_handler()
        handler.do_GET()

        handler._json.assert_called_once()
        status_code, payload = handler._json.call_args[0]
        self.assertEqual(status_code, 200)

        preview = payload.get("preview", {})
        conflicts = preview.get("conflicts", [])
        loc_conflicts = [c for c in conflicts if c.get("type") == "location_conflict"]
        self.assertEqual(len(loc_conflicts), 1)
        self.assertEqual(loc_conflicts[0]["code"], "8996")
        self.assertEqual(set(loc_conflicts[0]["folders"]), {"未分類", "散熱"})

        # location_conflict 絕對不可出現在 proposedState.stocks 中
        self.assertNotIn("8996", preview.get("proposedState", {}).get("stocks", {}))

    def test_init_preview_warnings_for_multiple_html_and_missing_chinese_name(self):
        """5. 警告：同資料夾兩個 HTML 變體產生 multiple_html_variants；字典無對應的英文名稱產生 missing_chinese_name。"""
        sub1 = self.temp_reports_dir / "散熱"
        sub1.mkdir(parents=True, exist_ok=True)
        (sub1 / "3324_雙鴻(TWO).html").write_text("html 1", encoding="utf-8")
        (sub1 / "3324_雙鴻(TWO)_處置股.html").write_text("html 2", encoding="utf-8")

        sub2 = self.temp_reports_dir / "其他"
        sub2.mkdir(parents=True, exist_ok=True)
        (sub2 / "1560_Kinik Company(TW).html").write_text("html 3", encoding="utf-8")

        with patch.object(server, "STOCK_NAME_DICT", {"3324": "雙鴻"}):
            handler = self._make_handler()
            handler.do_GET()

        handler._json.assert_called_once()
        status_code, payload = handler._json.call_args[0]
        self.assertEqual(status_code, 200)

        preview = payload.get("preview", {})
        warnings = preview.get("warnings", [])
        types_by_code = {w.get("code"): w.get("type") for w in warnings}
        self.assertEqual(types_by_code.get("3324"), "multiple_html_variants")
        self.assertEqual(types_by_code.get("1560"), "missing_chinese_name")

    def test_init_preview_strictly_read_only_and_preserves_tree(self):
        """6. 嚴格唯讀：對含資料夾、報表與合法狀態檔的暫存樹，呼叫前後逐一比對所有檔案的相對路徑、內容、大小與 st_mtime_ns 完全一致，且不呼叫寫入函式。"""
        sub = self.temp_reports_dir / "半導體"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "2330_台積電(TW).html").write_text("<html>tsmc</html>", encoding="utf-8")
        (sub / "2330_台積電(TW)_chart.png").write_bytes(b"fake png bytes")
        valid_state = {
            "version": 1,
            "updatedAt": "2026-09-21T00:00:00.000000Z",
            "stocks": {
                "2330": {
                    "name": "台積電",
                    "folder": "半導體",
                    "updatedAt": "2026-09-21T00:00:00.000000Z",
                    "deletedAt": None,
                }
            }
        }
        self.temp_state_file.write_text(json.dumps(valid_state, ensure_ascii=False, indent=2), encoding="utf-8")

        # 記錄呼叫前的樹快照
        before_snapshot = {}
        for p in self.temp_root.rglob("*"):
            if p.is_file():
                st = p.stat()
                before_snapshot[p.relative_to(self.temp_root)] = (p.read_bytes(), st.st_size, st.st_mtime_ns)

        with patch.object(server, "save_reports_state_atomic") as mock_server_save, \
             patch("reports_state.save_state") as mock_state_save:
            handler = self._make_handler()
            handler.do_GET()

            mock_server_save.assert_not_called()
            mock_state_save.assert_not_called()

        handler._json.assert_called_once()
        self.assertEqual(handler._json.call_args[0][0], 200)

        # 比對呼叫後的樹快照
        after_snapshot = {}
        for p in self.temp_root.rglob("*"):
            if p.is_file():
                st = p.stat()
                after_snapshot[p.relative_to(self.temp_root)] = (p.read_bytes(), st.st_size, st.st_mtime_ns)

        self.assertEqual(set(before_snapshot.keys()), set(after_snapshot.keys()))
        for rel_path, before_info in before_snapshot.items():
            after_info = after_snapshot[rel_path]
            self.assertEqual(before_info[0], after_info[0], f"檔案內容被修改: {rel_path}")
            self.assertEqual(before_info[1], after_info[1], f"檔案大小被修改: {rel_path}")
            self.assertEqual(before_info[2], after_info[2], f"檔案 mtime 被修改: {rel_path}")


class TestReportsStateInitApplyAPI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)
        self.temp_reports_dir = self.temp_root / "reports"
        self.temp_reports_dir.mkdir(parents=True, exist_ok=True)
        self.temp_state_file = self.temp_root / "reports_state.json"

        self.patches = [
            patch.object(server, "REPORTS_DIR", self.temp_reports_dir),
            patch.object(server, "REPORTS_STATE_FILE", self.temp_state_file),
            patch.object(server, "ROOT_DIR", self.temp_root),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp_dir.cleanup()

    def _make_handler(self, path="/api/reports-state/init-apply", body=None):
        handler = server.Handler.__new__(server.Handler)
        handler.path = path
        handler._read_json_body = MagicMock(return_value=body if body is not None else {})
        handler._json = MagicMock()
        return handler

    def _get_preview_basis(self, name_dict=None):
        h = server.Handler.__new__(server.Handler)
        h.path = "/api/reports-state/init-preview"
        h._json = MagicMock()
        with patch.object(server, "STOCK_NAME_DICT", name_dict if name_dict is not None else {"2330": "台積電"}):
            h.do_GET()
        _, payload = h._json.call_args[0]
        return payload.get("basis")

    def test_init_apply_successful_first_time_creation(self):
        """1. 正常首次建立：基準相符、無衝突、無缺中文名，成功建立 reports_state.json。"""
        sub = self.temp_reports_dir / "半導體"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "2330_台積電(TW).html").write_text("html content", encoding="utf-8")

        name_dict = {"2330": "台積電"}
        basis = self._get_preview_basis(name_dict)

        handler = self._make_handler(body={"confirm": True, "basis": basis})
        with patch.object(server, "STOCK_NAME_DICT", name_dict):
            handler.do_POST()

        status_code, payload = handler._json.call_args[0]
        self.assertEqual(status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["stockCount"], 1)

        # 狀態檔成功建立
        self.assertTrue(self.temp_state_file.is_file())
        state = json.loads(self.temp_state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["stocks"]["2330"]["name"], "台積電")
        self.assertEqual(state["stocks"]["2330"]["folder"], "半導體")

    def test_init_apply_rejects_when_missing_confirm(self):
        """2. 缺 confirm 拒絕：回傳 400，且絕不建立狀態檔。"""
        basis = self._get_preview_basis()
        handler = self._make_handler(body={"basis": basis})
        handler.do_POST()

        status_code, payload = handler._json.call_args[0]
        self.assertEqual(status_code, 400)
        self.assertFalse(payload["ok"])
        self.assertFalse(self.temp_state_file.exists())

    def test_init_apply_rejects_when_basis_mismatch_reports_modified(self):
        """3. basis 因報表檔案變更不符：預覽後檔案被修改，回傳 409 basis_mismatch。"""
        sub = self.temp_reports_dir / "半導體"
        sub.mkdir(parents=True, exist_ok=True)
        html_file = sub / "2330_台積電(TW).html"
        html_file.write_text("orig", encoding="utf-8")

        name_dict = {"2330": "台積電"}
        basis = self._get_preview_basis(name_dict)

        # 預覽後修改報表
        html_file.write_text("modified", encoding="utf-8")

        handler = self._make_handler(body={"confirm": True, "basis": basis})
        with patch.object(server, "STOCK_NAME_DICT", name_dict):
            handler.do_POST()

        status_code, payload = handler._json.call_args[0]
        self.assertEqual(status_code, 409)
        self.assertEqual(payload.get("conflictType"), "basis_mismatch")
        self.assertFalse(self.temp_state_file.exists())

    def test_init_apply_rejects_when_basis_mismatch_name_dict_modified(self):
        """4. basis 因名稱字典變更不符：回傳 409 basis_mismatch。"""
        sub = self.temp_reports_dir / "半導體"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "2330_台積電(TW).html").write_text("orig", encoding="utf-8")

        basis = self._get_preview_basis({"2330": "台積電"})

        # 確認時傳入變更後的字典
        handler = self._make_handler(body={"confirm": True, "basis": basis})
        with patch.object(server, "STOCK_NAME_DICT", {"2330": "台積電新名"}):
            handler.do_POST()

        status_code, payload = handler._json.call_args[0]
        self.assertEqual(status_code, 409)
        self.assertEqual(payload.get("conflictType"), "basis_mismatch")
        self.assertFalse(self.temp_state_file.exists())

    def test_init_apply_rejects_when_state_file_already_exists(self):
        """5. 狀態檔已存在拒絕：回傳 409，且外來狀態檔內容絕不被覆寫。"""
        self.temp_state_file.write_text('{"version": 1, "existing": true}', encoding="utf-8")
        basis = {"fingerprint": "fake", "reportsHash": "fake", "stateFileStatus": "absent", "nameDictHash": "fake"}

        handler = self._make_handler(body={"confirm": True, "basis": basis})
        handler.do_POST()

        status_code, payload = handler._json.call_args[0]
        self.assertEqual(status_code, 409)
        self.assertEqual(payload.get("conflictType"), "state_file_already_exists")
        # 原檔案未變
        self.assertEqual(self.temp_state_file.read_text(encoding="utf-8"), '{"version": 1, "existing": true}')

    def test_init_apply_rejects_when_state_file_is_directory(self):
        """6. 狀態檔路徑為資料夾時安全失敗回傳 422。"""
        self.temp_state_file.mkdir()
        basis = {"fingerprint": "fake", "reportsHash": "fake", "stateFileStatus": "absent", "nameDictHash": "fake"}

        handler = self._make_handler(body={"confirm": True, "basis": basis})
        handler.do_POST()

        status_code, payload = handler._json.call_args[0]
        self.assertEqual(status_code, 422)
        self.assertTrue(self.temp_state_file.is_dir())

    def test_init_apply_rejects_when_conflicts_exist(self):
        """7. 存在跨資料夾衝突拒絕：回傳 409 unresolved_conflicts，絕不建立部分狀態檔。"""
        dir_a = self.temp_reports_dir / "散熱"
        dir_b = self.temp_reports_dir / "未分類"
        dir_a.mkdir(parents=True, exist_ok=True)
        dir_b.mkdir(parents=True, exist_ok=True)
        (dir_a / "8996_高力(TW).html").write_text("a", encoding="utf-8")
        (dir_b / "8996_高力(TW).html").write_text("b", encoding="utf-8")

        name_dict = {"8996": "高力"}
        basis = self._get_preview_basis(name_dict)

        handler = self._make_handler(body={"confirm": True, "basis": basis})
        with patch.object(server, "STOCK_NAME_DICT", name_dict):
            handler.do_POST()

        status_code, payload = handler._json.call_args[0]
        self.assertEqual(status_code, 409)
        self.assertEqual(payload.get("conflictType"), "unresolved_conflicts")
        self.assertFalse(self.temp_state_file.exists())

    def test_init_apply_allows_official_english_name(self):
        """8. 缺中文名只是提示；英文正式名稱可安全建立狀態檔。"""
        sub = self.temp_reports_dir / "其他"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "1560_Kinik Company(TW).html").write_text("html", encoding="utf-8")

        # 字典中沒有 1560 的中文名
        basis = self._get_preview_basis({})

        handler = self._make_handler(body={"confirm": True, "basis": basis})
        with patch.object(server, "STOCK_NAME_DICT", {}):
            handler.do_POST()

        status_code, payload = handler._json.call_args[0]
        self.assertEqual(status_code, 200)
        self.assertTrue(payload.get("ok"))
        saved = json.loads(self.temp_state_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["stocks"]["1560"]["name"], "Kinik Company")

    def test_init_apply_allows_multiple_html_variants_as_warning(self):
        """9. 多 HTML 變體警告允許建立：同資料夾多 HTML 不阻礙首次建立。"""
        sub = self.temp_reports_dir / "散熱"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "3324_雙鴻(TWO).html").write_text("v1", encoding="utf-8")
        (sub / "3324_雙鴻(TWO)_處置股.html").write_text("v2", encoding="utf-8")

        name_dict = {"3324": "雙鴻"}
        basis = self._get_preview_basis(name_dict)

        handler = self._make_handler(body={"confirm": True, "basis": basis})
        with patch.object(server, "STOCK_NAME_DICT", name_dict):
            handler.do_POST()

        status_code, payload = handler._json.call_args[0]
        self.assertEqual(status_code, 200)
        self.assertTrue(self.temp_state_file.is_file())

    def test_init_apply_duplicate_submission_idempotency(self):
        """10. 重複提交冪等阻擋：第一次成功建立，第二次回傳 409 state_file_already_exists。"""
        sub = self.temp_reports_dir / "半導體"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "2330_台積電(TW).html").write_text("content", encoding="utf-8")

        name_dict = {"2330": "台積電"}
        basis = self._get_preview_basis(name_dict)

        # 第一次呼叫
        h1 = self._make_handler(body={"confirm": True, "basis": basis})
        with patch.object(server, "STOCK_NAME_DICT", name_dict):
            h1.do_POST()
        self.assertEqual(h1._json.call_args[0][0], 200)

        # 第二次呼叫
        h2 = self._make_handler(body={"confirm": True, "basis": basis})
        with patch.object(server, "STOCK_NAME_DICT", name_dict):
            h2.do_POST()
        status_code, payload = h2._json.call_args[0]
        self.assertEqual(status_code, 409)
        self.assertEqual(payload.get("conflictType"), "state_file_already_exists")

    def test_init_apply_preserves_external_state_file_created_during_publish(self):
        """外部程序在最後發布瞬間建立狀態檔時，API 必須回 409 且保留外來內容。"""
        sub = self.temp_reports_dir / "半導體"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "2330_台積電(TW).html").write_text("content", encoding="utf-8")
        name_dict = {"2330": "台積電"}
        basis = self._get_preview_basis(name_dict)
        foreign_bytes = b'{"external": true}'

        def external_writer(_temp_path, destination):
            Path(destination).write_bytes(foreign_bytes)
            raise FileExistsError("external writer won the race")

        handler = self._make_handler(body={"confirm": True, "basis": basis})
        with patch.object(server, "STOCK_NAME_DICT", name_dict), \
             patch("reports_state.os.link", side_effect=external_writer):
            handler.do_POST()

        status_code, payload = handler._json.call_args[0]
        self.assertEqual(status_code, 409)
        self.assertEqual(payload.get("conflictType"), "state_file_already_exists")
        self.assertEqual(self.temp_state_file.read_bytes(), foreign_bytes)
        self.assertEqual(list(self.temp_root.glob(".tmp_init_*.tmp")), [])

    def test_init_apply_strictly_zero_modification_to_reports_tree(self):
        """11. 嚴格唯讀 reports/ 樹：寫入狀態檔前後 reports/ 內所有檔案路徑、內容、大小與 mtime 完全一致。"""
        sub = self.temp_reports_dir / "半導體"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "2330_台積電(TW).html").write_text("<html>tsmc</html>", encoding="utf-8")
        (sub / "2330_台積電(TW)_chart.png").write_bytes(b"chart png")

        name_dict = {"2330": "台積電"}
        basis = self._get_preview_basis(name_dict)

        # 記錄樹快照
        before = {}
        for p in self.temp_reports_dir.rglob("*"):
            if p.is_file():
                st = p.stat()
                before[p.relative_to(self.temp_reports_dir)] = (p.read_bytes(), st.st_size, st.st_mtime_ns)

        handler = self._make_handler(body={"confirm": True, "basis": basis})
        with patch.object(server, "STOCK_NAME_DICT", name_dict):
            handler.do_POST()

        self.assertEqual(handler._json.call_args[0][0], 200)

        after = {}
        for p in self.temp_reports_dir.rglob("*"):
            if p.is_file():
                st = p.stat()
                after[p.relative_to(self.temp_reports_dir)] = (p.read_bytes(), st.st_size, st.st_mtime_ns)

        self.assertEqual(set(before.keys()), set(after.keys()))
        for rel_path, b_info in before.items():
            a_info = after[rel_path]
            self.assertEqual(b_info[0], a_info[0], f"內容變動: {rel_path}")
            self.assertEqual(b_info[1], a_info[1], f"大小變動: {rel_path}")
            self.assertEqual(b_info[2], a_info[2], f"mtime 變動: {rel_path}")


class TestFixStockNameAPI(unittest.TestCase):
    """測試 POST /api/reports-state/fix-stock-name 名稱補正端點。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)
        self.temp_reports_dir = self.temp_root / "reports"
        self.temp_reports_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dict_file = self.temp_root / "stock_name_dict.json"
        self.temp_state_file = self.temp_root / "reports_state.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_handler(self, path="/api/reports-state/fix-stock-name", body=None):
        handler = server.Handler.__new__(server.Handler)
        handler.path = path
        handler._read_json_body = MagicMock(return_value=body if body is not None else {})
        handler._json = MagicMock()
        return handler

    def test_fix_stock_name_success_updates_dict_and_reloads(self):
        self.temp_dict_file.write_text(json.dumps({"2330": "台積電"}, ensure_ascii=False), encoding="utf-8")
        handler = self._make_handler(body={"code": "1560", "chineseName": "中砂"})

        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "STOCK_NAME_DICT_PATH", self.temp_dict_file):
            server.reload_stock_name_dict()
            handler.do_POST()

        status, payload = handler._json.call_args[0]
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("code"), "1560")
        self.assertEqual(payload.get("chineseName"), "中砂")

        saved = json.loads(self.temp_dict_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["1560"], "中砂")
        self.assertEqual(saved["2330"], "台積電")
        # 驗證絕未建立狀態檔
        self.assertFalse(self.temp_state_file.exists())

    def test_fix_stock_name_validation_fails_on_bad_code_or_empty_name(self):
        handler = self._make_handler(body={"code": "INVALID_TOO_LONG_12345", "chineseName": "中砂"})
        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "STOCK_NAME_DICT_PATH", self.temp_dict_file):
            handler.do_POST()
        status, payload = handler._json.call_args[0]
        self.assertEqual(status, 400)
        self.assertFalse(payload.get("ok"))

        handler2 = self._make_handler(body={"code": "1560", "chineseName": "   "})
        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "STOCK_NAME_DICT_PATH", self.temp_dict_file):
            handler2.do_POST()
        status2, _ = handler2._json.call_args[0]
        self.assertEqual(status2, 400)

    def test_fix_stock_name_validation_fails_on_pure_ascii_name(self):
        handler = self._make_handler(body={"code": "1560", "chineseName": "Kinik Company"})
        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "STOCK_NAME_DICT_PATH", self.temp_dict_file):
            handler.do_POST()
        status, payload = handler._json.call_args[0]
        self.assertEqual(status, 400)
        self.assertIn("中文字元", payload.get("error", ""))

    def test_fix_stock_name_safe_fails_on_directory_dict(self):
        self.temp_dict_file.mkdir()
        handler = self._make_handler(body={"code": "1560", "chineseName": "中砂"})
        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "STOCK_NAME_DICT_PATH", self.temp_dict_file):
            handler.do_POST()
        status, payload = handler._json.call_args[0]
        self.assertEqual(status, 422)

    def test_fix_stock_name_allows_subsequent_preview_unblock_and_does_not_create_state(self):
        # 1. 建立一個缺少中文名的個股檔案
        (self.temp_reports_dir / "1560_Kinik Company(TW).html").write_text("<html>kinik</html>", encoding="utf-8")
        self.temp_dict_file.write_text("{}", encoding="utf-8")

        # 2. 補名前預覽：應該有 missing_chinese_name
        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "REPORTS_DIR", self.temp_reports_dir), \
             patch.object(server, "REPORTS_STATE_FILE", self.temp_state_file), \
             patch.object(server, "STOCK_NAME_DICT_PATH", self.temp_dict_file):
            server.reload_stock_name_dict()
            preview1 = reports_state.generate_initial_state_preview(
                self.temp_reports_dir,
                existing_state=None,
                stock_name_dict=server.STOCK_NAME_DICT,
            )
            missing1 = [w for w in preview1.get("warnings", []) if w.get("type") == "missing_chinese_name"]
            self.assertEqual(len(missing1), 1)

            # 3. 呼叫補名 API
            handler = self._make_handler(body={"code": "1560", "chineseName": "中砂"})
            handler.do_POST()
            self.assertEqual(handler._json.call_args[0][0], 200)

            # 4. 補名後重新取得預覽：missing_chinese_name 解除
            preview2 = reports_state.generate_initial_state_preview(
                self.temp_reports_dir,
                existing_state=None,
                stock_name_dict=server.STOCK_NAME_DICT,
            )
            missing2 = [w for w in preview2.get("warnings", []) if w.get("type") == "missing_chinese_name"]
            self.assertEqual(len(missing2), 0)
            self.assertIn("1560", preview2["proposedState"]["stocks"])
            self.assertEqual(preview2["proposedState"]["stocks"]["1560"]["name"], "中砂")

            # 驗證過程中絕對沒有建立 reports_state.json
            self.assertFalse(self.temp_state_file.exists())


class TestSyncPreviewAPI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)
        self.temp_reports_dir = self.temp_root / "reports"
        self.temp_reports_dir.mkdir(parents=True, exist_ok=True)
        self.temp_state_file = self.temp_root / "reports_state.json"
        self.temp_dict_file = self.temp_root / "stock_name_dict.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_handler(self, path="/api/reports-state/sync-preview", body=None):
        handler = server.Handler.__new__(server.Handler)
        handler.path = path
        handler._read_json_body = MagicMock(return_value=body if body is not None else {})
        handler._json = MagicMock()
        return handler

    def test_sync_preview_rejects_path_parameters(self):
        """拒絕路徑參數：傳入 incomingPath 或 filePath 必須回傳 400。"""
        handler = self._make_handler(body={"incomingPath": "C:\\fake\\reports_state.json"})
        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "REPORTS_DIR", self.temp_reports_dir), \
             patch.object(server, "REPORTS_STATE_FILE", self.temp_state_file):
            handler.do_POST()

        status, payload = handler._json.call_args[0]
        self.assertEqual(status, 400)
        self.assertFalse(payload.get("ok"))
        self.assertIn("不接受路徑參數", payload.get("error", ""))

    def test_sync_preview_local_state_missing_returns_404(self):
        """本機狀態檔缺失時回傳 404。"""
        valid_incoming = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {}
        }
        handler = self._make_handler(body={"incomingState": valid_incoming})
        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "REPORTS_DIR", self.temp_reports_dir), \
             patch.object(server, "REPORTS_STATE_FILE", self.temp_state_file):
            handler.do_POST()

        status, payload = handler._json.call_args[0]
        self.assertEqual(status, 404)
        self.assertFalse(payload.get("ok"))

    def test_sync_preview_local_state_corrupted_returns_422(self):
        """本機狀態檔損壞時安全失敗回傳 422。"""
        self.temp_state_file.write_text("invalid json content", encoding="utf-8")
        valid_incoming = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {}
        }
        handler = self._make_handler(body={"incomingState": valid_incoming})
        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "REPORTS_DIR", self.temp_reports_dir), \
             patch.object(server, "REPORTS_STATE_FILE", self.temp_state_file):
            handler.do_POST()

        status, payload = handler._json.call_args[0]
        self.assertEqual(status, 422)
        self.assertFalse(payload.get("ok"))

    def test_sync_preview_incoming_state_corrupted_returns_400_or_422(self):
        """外來狀態缺失或格式不合法時回傳 400/422。"""
        local_valid = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {}
        }
        self.temp_state_file.write_text(json.dumps(local_valid), encoding="utf-8")

        # 缺少 incomingState
        handler = self._make_handler(body={})
        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "REPORTS_DIR", self.temp_reports_dir), \
             patch.object(server, "REPORTS_STATE_FILE", self.temp_state_file):
            handler.do_POST()
        status, _ = handler._json.call_args[0]
        self.assertEqual(status, 400)

        # incomingState 結構不合法
        handler2 = self._make_handler(body={"incomingState": {"version": 1}})
        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "REPORTS_DIR", self.temp_reports_dir), \
             patch.object(server, "REPORTS_STATE_FILE", self.temp_state_file):
            handler2.do_POST()
        status2, _ = handler2._json.call_args[0]
        self.assertIn(status2, (400, 422))

    def test_sync_preview_success_and_strictly_read_only(self):
        """成功情境：純唯讀預覽，且對磁碟狀態檔與 reports/ 檔案庫完全零變更。"""
        local_state = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "半導體", "updatedAt": "2026-03-30T10:00:00Z", "deletedAt": None}
            }
        }
        self.temp_state_file.write_text(json.dumps(local_state, ensure_ascii=False, indent=2), encoding="utf-8")

        semi_dir = self.temp_reports_dir / "半導體"
        semi_dir.mkdir(parents=True, exist_ok=True)
        (semi_dir / "2330_台積電(TW).html").write_text("html content", encoding="utf-8")

        incoming_state = {
            "version": 1,
            "updatedAt": "2026-03-30T11:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "半導體", "updatedAt": "2026-03-30T10:00:00Z", "deletedAt": None},
                "2317": {"name": "鴻海", "folder": "電子", "updatedAt": "2026-03-30T11:00:00Z", "deletedAt": None}
            }
        }

        # 記錄快照
        before_snapshot = {}
        for p in self.temp_root.rglob("*"):
            if p.is_file():
                st = p.stat()
                before_snapshot[p.relative_to(self.temp_root)] = (p.read_bytes(), st.st_size, st.st_mtime_ns)

        handler = self._make_handler(body={"incomingState": incoming_state})
        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "REPORTS_DIR", self.temp_reports_dir), \
             patch.object(server, "REPORTS_STATE_FILE", self.temp_state_file):
            handler.do_POST()

        status, payload = handler._json.call_args[0]
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("ok"))
        self.assertTrue(payload.get("readOnly"))
        self.assertIn("preview", payload)
        preview = payload["preview"]
        self.assertEqual(preview["stateSummary"]["addedFromIncoming"], 1)

        # 驗證完全零修改
        after_snapshot = {}
        for p in self.temp_root.rglob("*"):
            if p.is_file():
                st = p.stat()
                after_snapshot[p.relative_to(self.temp_root)] = (p.read_bytes(), st.st_size, st.st_mtime_ns)

        self.assertEqual(set(before_snapshot.keys()), set(after_snapshot.keys()))
        for rel_path, before_info in before_snapshot.items():
            after_info = after_snapshot[rel_path]
            self.assertEqual(before_info[0], after_info[0], f"檔案內容被修改: {rel_path}")
            self.assertEqual(before_info[1], after_info[1], f"檔案大小被修改: {rel_path}")
            self.assertEqual(before_info[2], after_info[2], f"檔案修改時間被更動: {rel_path}")


class TestSyncApplyAPI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)
        self.temp_reports_dir = self.temp_root / "reports"
        self.temp_reports_dir.mkdir(parents=True, exist_ok=True)
        self.temp_state_file = self.temp_root / "reports_state.json"
        self.temp_dict_file = self.temp_root / "stock_name_dict.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_handler(self, path="/api/reports-state/sync-apply", body=None):
        handler = server.Handler.__new__(server.Handler)
        handler.path = path
        handler._read_json_body = MagicMock(return_value=body if body is not None else {})
        handler._json = MagicMock()
        return handler

    def test_sync_apply_rejects_path_parameters(self):
        """拒絕路徑參數：傳入 incomingPath 或 filePath 必須回傳 400。"""
        handler = self._make_handler(body={"confirm": True, "incomingPath": "C:\\fake\\reports_state.json"})
        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "REPORTS_DIR", self.temp_reports_dir), \
             patch.object(server, "REPORTS_STATE_FILE", self.temp_state_file):
            handler.do_POST()

        status, payload = handler._json.call_args[0]
        self.assertEqual(status, 400)
        self.assertFalse(payload.get("ok"))
        self.assertIn("不接受路徑參數", payload.get("error", ""))

    def test_sync_apply_rejects_missing_confirm(self):
        """缺少 confirm: true 必須拒絕套用（400）。"""
        handler = self._make_handler(body={"basis": {"localFingerprint": "abc", "incomingFingerprint": "def"}})
        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "REPORTS_DIR", self.temp_reports_dir), \
             patch.object(server, "REPORTS_STATE_FILE", self.temp_state_file):
            handler.do_POST()

        status, payload = handler._json.call_args[0]
        self.assertEqual(status, 400)
        self.assertFalse(payload.get("ok"))
        self.assertIn("confirm", payload.get("error", ""))

    def test_sync_apply_rejects_missing_or_invalid_basis(self):
        """缺少 basis 或缺少指紋時必須回傳 400。"""
        local_state = {"version": 1, "updatedAt": "2026-03-30T10:00:00Z", "stocks": {}}
        self.temp_state_file.write_text(json.dumps(local_state), encoding="utf-8")

        # 缺少 basis
        handler = self._make_handler(body={"confirm": True, "incomingState": {"version": 1, "updatedAt": "2026-03-30T10:00:00Z", "stocks": {}}})
        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "REPORTS_DIR", self.temp_reports_dir), \
             patch.object(server, "REPORTS_STATE_FILE", self.temp_state_file):
            handler.do_POST()
        status, payload = handler._json.call_args[0]
        self.assertEqual(status, 400)

        # basis 缺少 fingerprint
        handler2 = self._make_handler(body={
            "confirm": True,
            "basis": {"otherKey": "abc"},
            "incomingState": {"version": 1, "updatedAt": "2026-03-30T10:00:00Z", "stocks": {}}
        })
        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "REPORTS_DIR", self.temp_reports_dir), \
             patch.object(server, "REPORTS_STATE_FILE", self.temp_state_file):
            handler2.do_POST()
        status2, _ = handler2._json.call_args[0]
        self.assertEqual(status2, 400)

    def test_sync_apply_rejects_basis_mismatch(self):
        """基準指紋不符時必須安全失敗回傳 409 (basis_mismatch)。"""
        local_state = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {}
        }
        self.temp_state_file.write_text(json.dumps(local_state), encoding="utf-8")
        incoming_state = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {}
        }
        handler = self._make_handler(body={
            "confirm": True,
            "basis": {
                "fingerprint": "wrong_fingerprint_hash"
            },
            "incomingState": incoming_state
        })
        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "REPORTS_DIR", self.temp_reports_dir), \
             patch.object(server, "REPORTS_STATE_FILE", self.temp_state_file):
            handler.do_POST()

        status, payload = handler._json.call_args[0]
        self.assertEqual(status, 409)
        self.assertEqual(payload.get("code"), "basis_mismatch")

    def test_sync_apply_rejects_conflicts_even_if_client_sends_a_choice(self):
        """存在衝突時一律拒絕；用戶端選邊資料不得繞過人工檔案處理流程。"""
        local_state = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "半導體A", "updatedAt": "2026-03-30T10:00:00Z", "deletedAt": None}
            }
        }
        self.temp_state_file.write_text(json.dumps(local_state), encoding="utf-8")
        incoming_state = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "半導體B", "updatedAt": "2026-03-30T10:00:00Z", "deletedAt": None}
            }
        }
        basis = reports_state.calculate_sync_preview_basis(
            self.temp_reports_dir,
            self.temp_state_file,
            incoming_state,
            stock_name_dict={}
        )
        handler = self._make_handler(body={
            "confirm": True,
            "basis": basis,
            "incomingState": incoming_state,
            "conflictResolutions": {"2330": "incoming"}
        })
        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "REPORTS_DIR", self.temp_reports_dir), \
             patch.object(server, "REPORTS_STATE_FILE", self.temp_state_file), \
             patch.object(server, "STOCK_NAME_DICT", {}):
            handler.do_POST()

        status, payload = handler._json.call_args[0]
        self.assertEqual(status, 409)
        self.assertEqual(payload.get("code"), "unresolved_conflicts")
        self.assertIn("2330", payload.get("unresolvedCodes", []))

    def test_sync_apply_with_newer_incoming_state_success(self):
        """無衝突的較新外來狀態可套用，狀態與檔案皆正確更新。"""
        # 本機在 半導體A 有檔案
        dir_a = self.temp_reports_dir / "半導體A"
        dir_a.mkdir(parents=True, exist_ok=True)
        file_a = dir_a / "2330_台積電(TW).html"
        file_a.write_text("content 2330", encoding="utf-8")

        local_state = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "半導體A", "updatedAt": "2026-03-30T10:00:00Z", "deletedAt": None}
            }
        }
        self.temp_state_file.write_text(json.dumps(local_state, ensure_ascii=False, indent=2), encoding="utf-8")

        # incoming 較新，要求改到 半導體B
        incoming_state = {
            "version": 1,
            "updatedAt": "2026-03-30T11:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "半導體B", "updatedAt": "2026-03-30T11:00:00Z", "deletedAt": None}
            }
        }

        basis = reports_state.calculate_sync_preview_basis(
            self.temp_reports_dir,
            self.temp_state_file,
            incoming_state,
            stock_name_dict={}
        )

        handler = self._make_handler(body={
            "confirm": True,
            "basis": basis,
            "incomingState": incoming_state
        })
        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "REPORTS_DIR", self.temp_reports_dir), \
             patch.object(server, "REPORTS_STATE_FILE", self.temp_state_file), \
             patch.object(server, "STOCK_NAME_DICT", {}):
            handler.do_POST()

        status, payload = handler._json.call_args[0]
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("ok"))
        self.assertTrue(payload.get("applied"))

        # 驗證檔案已搬移到 半導體B
        dest_file = self.temp_reports_dir / "半導體B" / "2330_台積電(TW).html"
        self.assertTrue(dest_file.exists())
        self.assertFalse(file_a.exists())

        # 驗證狀態檔已更新
        saved_state = json.loads(self.temp_state_file.read_text(encoding="utf-8"))
        self.assertEqual(saved_state["stocks"]["2330"]["folder"], "半導體B")

    def test_sync_apply_unmanaged_files_preserved(self):
        """套用同步時，未受管候選檔案絕不會被刪除或異動。"""
        # 建立未受管檔案
        unmanaged_file = self.temp_reports_dir / "未分類" / "9999_未知個股(TW).html"
        unmanaged_file.parent.mkdir(parents=True, exist_ok=True)
        unmanaged_file.write_text("unmanaged", encoding="utf-8")
        unmanaged_mtime = unmanaged_file.stat().st_mtime_ns

        local_state = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {}
        }
        self.temp_state_file.write_text(json.dumps(local_state), encoding="utf-8")
        incoming_state = {
            "version": 1,
            "updatedAt": "2026-03-30T11:00:00Z",
            "stocks": {}
        }
        basis = reports_state.calculate_sync_preview_basis(
            self.temp_reports_dir,
            self.temp_state_file,
            incoming_state,
            stock_name_dict={}
        )

        handler = self._make_handler(body={
            "confirm": True,
            "basis": basis,
            "incomingState": incoming_state
        })
        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "REPORTS_DIR", self.temp_reports_dir), \
             patch.object(server, "REPORTS_STATE_FILE", self.temp_state_file), \
             patch.object(server, "STOCK_NAME_DICT", {}):
            handler.do_POST()

        status, payload = handler._json.call_args[0]
        self.assertEqual(status, 200)
        self.assertTrue(unmanaged_file.exists())
        self.assertEqual(unmanaged_file.stat().st_mtime_ns, unmanaged_mtime)

    def test_sync_apply_rollback_on_failure(self):
        """套用過程中若拋出例外，必須完整回滾並回傳 500。"""
        dir_a = self.temp_reports_dir / "半導體A"
        dir_a.mkdir(parents=True, exist_ok=True)
        file_a = dir_a / "2330_台積電(TW).html"
        file_a.write_text("initial content", encoding="utf-8")

        local_state = {
            "version": 1,
            "updatedAt": "2026-03-30T10:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "半導體A", "updatedAt": "2026-03-30T10:00:00Z", "deletedAt": None}
            }
        }
        self.temp_state_file.write_text(json.dumps(local_state), encoding="utf-8")
        incoming_state = {
            "version": 1,
            "updatedAt": "2026-03-30T11:00:00Z",
            "stocks": {
                "2330": {"name": "台積電", "folder": "半導體B", "updatedAt": "2026-03-30T11:00:00Z", "deletedAt": None}
            }
        }
        basis = reports_state.calculate_sync_preview_basis(
            self.temp_reports_dir,
            self.temp_state_file,
            incoming_state,
            stock_name_dict={}
        )

        handler = self._make_handler(body={
            "confirm": True,
            "basis": basis,
            "incomingState": incoming_state
        })

        # 模擬 execute_sync_plan_transactional 失敗
        with patch.object(server, "ROOT_DIR", self.temp_root), \
             patch.object(server, "REPORTS_DIR", self.temp_reports_dir), \
             patch.object(server, "REPORTS_STATE_FILE", self.temp_state_file), \
             patch.object(server, "STOCK_NAME_DICT", {}), \
             patch.object(server.reports_state, "execute_sync_plan_transactional", side_effect=RuntimeError("Disk write failed")):
            handler.do_POST()

        status, payload = handler._json.call_args[0]
        self.assertEqual(status, 500)
        self.assertFalse(payload.get("ok"))
        self.assertIn("Disk write failed", payload.get("error", ""))

        # 檔案與狀態檔必須完好如初
        self.assertTrue(file_a.exists())
        saved_state = json.loads(self.temp_state_file.read_text(encoding="utf-8"))
        self.assertEqual(saved_state["stocks"]["2330"]["folder"], "半導體A")


class TestDuplicateCleanupAPI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.reports_dir = self.root / "reports"
        self.reports_dir.mkdir()
        self.folder = self.reports_dir / "散熱"
        self.folder.mkdir()
        self.keep_file = self.folder / "3324_雙鴻(TWO).html"
        self.duplicate_file = self.folder / "3324_雙鴻(TWO)(處置期間0908-0914).html"
        self.keep_file.write_text("keep", encoding="utf-8")
        self.duplicate_file.write_text("duplicate", encoding="utf-8")
        self.state_file = self.root / "reports_state.json"
        self.quarantine_dir = self.root / ".reports_duplicate_quarantine"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _handler(self, path, body=None):
        handler = server.Handler.__new__(server.Handler)
        handler.path = path
        handler._json = MagicMock()
        handler._read_json_body = MagicMock(return_value=body if body is not None else {})
        return handler

    def test_preview_is_read_only_and_apply_archives_duplicate(self):
        handler = self._handler("/api/reports/duplicate-cleanup-preview")
        with patch.object(server, "ROOT_DIR", self.root), \
             patch.object(server, "REPORTS_DIR", self.reports_dir), \
             patch.object(server, "REPORTS_STATE_FILE", self.state_file), \
             patch.object(server, "REPORTS_DUPLICATE_QUARANTINE_DIR", self.quarantine_dir), \
             patch.object(server, "STOCK_NAME_DICT", {"3324": "雙鴻"}):
            handler.do_GET()

        status, payload = handler._json.call_args[0]
        self.assertEqual(status, 200)
        self.assertTrue(payload["preview"]["isReadOnlyPreview"])
        self.assertTrue(self.duplicate_file.exists())

        apply_handler = self._handler("/api/reports/duplicate-cleanup-apply", {
            "confirm": True, "basis": payload["basis"]
        })
        with patch.object(server, "ROOT_DIR", self.root), \
             patch.object(server, "REPORTS_DIR", self.reports_dir), \
             patch.object(server, "REPORTS_STATE_FILE", self.state_file), \
             patch.object(server, "REPORTS_DUPLICATE_QUARANTINE_DIR", self.quarantine_dir), \
             patch.object(server, "STOCK_NAME_DICT", {"3324": "雙鴻"}):
            apply_handler.do_POST()

        status, payload = apply_handler._json.call_args[0]
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(self.duplicate_file.exists())
        self.assertTrue(self.keep_file.exists())
        self.assertTrue(self.quarantine_dir.exists())

    def test_apply_rejects_stale_basis(self):
        handler = self._handler("/api/reports/duplicate-cleanup-apply", {
            "confirm": True, "basis": {"fingerprint": "stale"}
        })
        with patch.object(server, "ROOT_DIR", self.root), \
             patch.object(server, "REPORTS_DIR", self.reports_dir), \
             patch.object(server, "REPORTS_STATE_FILE", self.state_file), \
             patch.object(server, "REPORTS_DUPLICATE_QUARANTINE_DIR", self.quarantine_dir), \
             patch.object(server, "STOCK_NAME_DICT", {"3324": "雙鴻"}):
            handler.do_POST()

        status, payload = handler._json.call_args[0]
        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "basis_mismatch")
        self.assertTrue(self.duplicate_file.exists())


class TestInstitutionalBreakdown(unittest.TestCase):
    @patch("requests.get")
    def test_fetch_institutional_breakdown_excludes_hedging_and_recalculates_total(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "stat": "OK",
            "date": "20260922",
            "data": [
                ["自營商(自行買賣)", "11,606,932,360", "8,098,761,830", "3,508,170,530"],
                ["自營商(避險)", "42,563,031,667", "30,217,059,355", "12,345,972,312"],
                ["投信", "22,842,946,443", "23,209,364,598", "-366,418,155"],
                ["外資及陸資(不含外資自營商)", "385,352,538,106", "340,031,610,542", "45,320,927,564"],
                ["外資自營商", "0", "0", "0"],
                ["合計", "462,365,448,576", "401,556,796,325", "60,808,652,251"]
            ]
        }
        mock_get.return_value = mock_resp

        result, err = server.fetch_institutional_breakdown()
        self.assertIsNone(err)
        self.assertIsNotNone(result)
        self.assertEqual(result["date"], "2026-09-22")

        rows = result["rows"]
        # 應只有 4 筆：外資、投信、自營商(自行買賣)、合計
        self.assertEqual(len(rows), 4)
        labels = [r["label"] for r in rows]
        self.assertEqual(labels, ["外資及陸資(不含外資自營商)", "投信", "自營商(自行買賣)", "合計"])

        foreign_net = rows[0]["net"]
        trust_net = rows[1]["net"]
        dealer_net = rows[2]["net"]
        total_row = rows[3]

        self.assertEqual(foreign_net, 453.21)
        self.assertEqual(trust_net, -3.66)
        self.assertEqual(dealer_net, 35.08)

        # 動態計算合計（不含自營避險）：453.21 + (-3.66) + 35.08 = 484.63
        self.assertEqual(total_row["net"], 484.63)
        self.assertAlmostEqual(round(foreign_net + trust_net + dealer_net, 2), total_row["net"])


if __name__ == "__main__":
    unittest.main()
