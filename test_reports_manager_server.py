import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import reports_manager_server as server


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

        # 3. 包含三大標題
        self.assertIn("1. 複製 AI Prompt", html_text)
        self.assertIn("2. 貼上 AI 回覆", html_text)
        self.assertIn("3. 執行狀態", html_text)
        self.assertIn("驗證並更新研究卡", html_text)

        # 4. 沒有殘留多階段或舊字串
        self.assertNotIn("第 1/2 階段", html_text)
        self.assertNotIn("第 1／2 階段", html_text)
        self.assertNotIn("繼續評分", html_text)
        self.assertNotIn("API 配額不足", html_text)
        self.assertNotIn("等待模型重試", html_text)


if __name__ == "__main__":
    unittest.main()
