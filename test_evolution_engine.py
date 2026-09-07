import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import evolution_engine as engine


class RankingPolicyTests(unittest.TestCase):
    @staticmethod
    def stock_from_closes(closes, last_high=None, volumes=None):
        rows = []
        volumes = volumes or [100] * len(closes)
        for i, (close, volume) in enumerate(zip(closes, volumes)):
            rows.append({'date': f'08/{i+1:02d}', 'open': close, 'high': close + 1,
                         'low': close - 1, 'close': close, 'volume': volume})
        if last_high is not None:
            rows[-1]['high'] = last_high
        return {'code': '1234', 'name': '測試股', 'category': '測試', 'semantic_tags': [],
                'kline': rows, 'institutions': [], 'path': 'test', 'session_date': '2026-09-04'}

    def test_empty_ranking_and_threshold(self):
        self.assertEqual(engine.select_qualified_candidates([{'holistic_score': 179.9}]), [])
        scores = [{'holistic_score': n} for n in range(175, 195)]
        self.assertEqual([c['holistic_score'] for c in engine.select_qualified_candidates(scores)],
                         list(range(194, 184, -1)))

    def test_actionable_selection_supplies_labeled_watch_candidates(self):
        active = {'code': 'A', 'setup_stage': '剛突破起漲', 'holistic_score': 110,
                  'score': 110, 'pivot_distance': 0, 'today_pct': 2, 'ret5': 4}
        watch = {'code': 'W', 'setup_stage': '趨勢中段／訊號未明', 'holistic_score': 90,
                 'score': 90, 'pivot_distance': -2, 'today_pct': 1, 'ret5': 3}
        rejected = {'code': 'R', 'setup_stage': '漲幅延伸／追價風險', 'holistic_score': 200,
                    'score': 200, 'pivot_distance': 10, 'today_pct': 9, 'ret5': 20}
        result = engine.select_actionable_candidates([watch, rejected, active])
        self.assertEqual([x['code'] for x in result], ['A', 'W'])
        self.assertIn('尚待突破', result[1]['selection_tier'])

    def test_watch_candidates_rank_by_final_holistic_score(self):
        common = {'setup_stage': '趨勢中段／訊號未明', 'score': 90,
                  'pivot_distance': -2, 'today_pct': 1, 'ret5': 3}
        lower = {**common, 'code': 'L', 'pre_audit_score': 120, 'holistic_score': 100}
        higher = {**common, 'code': 'H', 'pre_audit_score': 90, 'holistic_score': 130}
        result = engine.select_actionable_candidates([lower, higher])
        self.assertEqual([x['code'] for x in result], ['H', 'L'])

    def test_disposition_sources_have_same_penalty_and_can_clear(self):
        original = {'score': 150.0, 'reasons': ['回測量縮守穩(0.8x)', '站穩5MA']}
        local = engine.apply_disposition_penalty(copy.deepcopy(original), 'local')
        remote = engine.apply_disposition_penalty(copy.deepcopy(original), 'remote')
        self.assertEqual(local['score'], 111.0)
        self.assertEqual(local['score'], remote['score'])
        engine.apply_disposition_penalty(local, 'remote')
        self.assertEqual(local['score'], 111.0)
        engine.apply_disposition_penalty(local, '')
        self.assertEqual(local['score'], 150.0)
        self.assertEqual(local['reasons'], original['reasons'])

    def test_full_session_date_and_year_boundary(self):
        self.assertEqual(engine.report_session_date('09/04', '2026-09-04'), '2026-09-04')
        self.assertEqual(engine.report_session_date('12/31', '2026-01-02'), '2025-12-31')
        self.assertEqual(engine.report_session_date('2026-09-04', ''), '2026-09-04')

    def test_daily_cache_invalidation(self):
        candidate = {'code': '1234', 'name': 'Test', 'category': 'Test'}
        for cached_day, expected_calls in [('2026-09-07', 0), ('2026-09-04', 1), ('09/07', 1)]:
            with self.subTest(cached_day=cached_day):
                cache = {'1234': {'schema_version': 2, 'updated_at': cached_day,
                                  'target_price': '100', 'monthly_rev': 'data'}}
                with patch.object(engine, 'load_fundamental_cache', return_value=cache), \
                     patch.object(engine, 'call_gemini_search', return_value=(None, None)) as api:
                    engine.audit_stock_with_gemini(candidate, 'test-key', '2026-09-07')
                    self.assertEqual(api.call_count, expected_calls)

    def test_failed_sector_search_does_not_invent_hot_sectors(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(engine, 'SECTOR_CACHE_FILE', Path(tmp) / 'sectors.json'), \
                 patch.object(engine, 'call_gemini_search', return_value=(None, None)):
                _, sectors = engine.fetch_market_hot_sectors('test-key', '2026-09-07')
                self.assertEqual(sectors, [])

    def test_old_sector_cache_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / 'sectors.json'
            cache_file.write_text('{"as_of_date":"2026-09-07","hot_sectors":[{"sector_name":"old"}]}', encoding='utf-8')
            with patch.object(engine, 'SECTOR_CACHE_FILE', cache_file), \
                 patch.object(engine, 'call_gemini_search', return_value=(None, None)) as api:
                _, sectors = engine.fetch_market_hot_sectors('test-key', '2026-09-07')
                self.assertEqual(api.call_count, 1)
                self.assertEqual(sectors, [])

    def test_same_day_empty_sector_cache_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / 'sectors.json'
            cache_file.write_text(json.dumps({
                'schema_version': 2, 'as_of_date': '2026-09-07',
                'overview': '無合格風口', 'hot_sectors': []
            }, ensure_ascii=False), encoding='utf-8')
            with patch.object(engine, 'SECTOR_CACHE_FILE', cache_file), \
                 patch.object(engine, 'call_gemini_search') as api:
                overview, sectors = engine.fetch_market_hot_sectors('test-key', '2026-09-07')
            self.assertEqual(overview, '無合格風口')
            self.assertEqual(sectors, [])
            api.assert_not_called()

    def test_sector_search_keeps_only_recent_pre_breakout_evidence(self):
        response = '''{"market_overview":"test","hot_sectors":[
          {"sector_name":"valid","heat_level":4,"stage":"emerging","catalysts":"new","event_date":"2026-09-01","source_url":"https://example.com/a","related_tags":[]},
          {"sector_name":"extended","heat_level":5,"stage":"extended","catalysts":"old","event_date":"2026-09-01","source_url":"https://example.com/b","related_tags":[]},
          {"sector_name":"future","heat_level":5,"stage":"accelerating","catalysts":"future","event_date":"2026-09-05","source_url":"https://example.com/c","related_tags":[]}] }'''
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(engine, 'SECTOR_CACHE_FILE', Path(tmp) / 'sectors.json'), \
             patch.object(engine, 'call_gemini_search', return_value=(response, 'test')):
            _, sectors = engine.fetch_market_hot_sectors('key', '2026-09-04')
            self.assertEqual([s['sector_name'] for s in sectors], ['valid'])

    def test_evidence_requires_past_recent_date_and_url(self):
        valid = {'event_date': '2026-09-01', 'source_url': 'https://example.com/news'}
        self.assertTrue(engine.evidence_is_recent(valid, '2026-09-04', 14))
        self.assertFalse(engine.evidence_is_recent({**valid, 'event_date': '2026-09-05'}, '2026-09-04', 14))
        self.assertFalse(engine.evidence_is_recent({**valid, 'source_url': None}, '2026-09-04', 14))

    def test_negative_catalyst_is_not_rewarded(self):
        base = {'score': 100, 'reasons': [], 'category': '', 'name': '', 'price': 100,
                'session_date': '2026-09-04', 'semantic_tags': [], 'audit_data': {
                    'catalyst': {'direction': 'negative', 'new_information': True,
                                 'event_date': '2026-09-01', 'source_url': 'https://example.com/news'}}}
        result = engine.evaluate_holistic_score(base, [])
        self.assertEqual(result['holistic_score'], 85.0)  # 搜尋無結果不扣分、負事件 -15

    def test_structured_audit_is_parsed_and_cached(self):
        response = '''{"monthly_revenue":{"period":"8月","amount_text":"10億元","mom_pct":5,"yoy_pct":20,"event_date":"2026-09-03","source_url":"https://example.com/rev"},"earnings":{"period":"Q2","eps":2.5,"eps_yoy_pct":12,"gross_margin_pct":30,"event_date":"2026-08-10","source_url":"https://example.com/eps"},"catalyst":{"summary":"取得新訂單","direction":"positive","new_information":true,"event_date":"2026-09-01","source_url":"https://example.com/news"},"analyst_target":{"median_price":120,"rating":"買進","sample_size":3,"event_date":"2026-08-20","source_url":"https://example.com/tp"},"disposition":{"status":"normal","event_date":"2026-09-04","source_url":"https://example.com/twse"}}'''
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(engine, 'FUNDAMENTAL_CACHE_FILE', Path(tmp) / 'cache.json'), \
             patch.object(engine, 'call_gemini_search', return_value=(response, 'test-model')):
            result = engine.audit_stock_with_gemini(
                {'code': '1234', 'name': '測試', 'category': '測試'}, 'key', '2026-09-04')
            self.assertIn('YoY+20%', result['monthly_rev'])
            self.assertIn('中位數120元', result['target_price'])
            self.assertTrue(result['disposition_checked'])
            cached = engine.load_fundamental_cache()['1234']
            self.assertEqual(cached['schema_version'], 2)

    def test_partial_audit_payload_is_rejected(self):
        partial = {'monthly_revenue': {}, 'earnings': {}, 'catalyst': {}}
        self.assertFalse(engine.is_complete_audit_payload(partial))
        self.assertIsNone(engine.normalize_audit_payload(partial, '2026-09-04', 'test'))

    def test_complete_audit_payload_allows_null_values(self):
        complete = {key: {} for key in engine.AUDIT_BLOCKS}
        self.assertTrue(engine.is_complete_audit_payload(complete))
        self.assertIsNotNone(engine.normalize_audit_payload(complete, '2026-09-04', 'test'))

    def test_cross_ranking_memberships_are_display_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            chatgpt = Path(tmp) / 'chatgpt.md'
            gemini = Path(tmp) / 'gemini.md'
            chatgpt.write_text('| 1 | `1111` | 甲 |', encoding='utf-8')
            gemini.write_text('| 1 | `1111` | 甲 |\n| 2 | `2222` | 乙 |', encoding='utf-8')
            with patch.object(engine, 'CHATGPT_RANKING_MD', chatgpt), \
                 patch.object(engine, 'GEMINI_RANKING_MD', gemini):
                memberships = engine.load_cross_ranking_memberships()
            self.assertEqual(memberships['chatgpt'], {'1111'})
            self.assertEqual(memberships['gemini'], {'1111', '2222'})

    def test_failed_new_audit_uses_same_day_legacy_for_display_only(self):
        legacy = {'1234': {'updated_at': '2026-09-04', 'monthly_rev': '舊營收',
                           'earnings': '舊獲利', 'target_price': '舊目標價'}}
        with patch.object(engine, 'load_fundamental_cache', return_value=legacy), \
             patch.object(engine, 'call_gemini_search', return_value=(None, None)):
            result = engine.audit_stock_with_gemini(
                {'code': '1234', 'name': '測試', 'category': '測試'}, 'key', '2026-09-04')
            self.assertTrue(result['_legacy_fallback'])
            self.assertEqual(result['monthly_rev'], '舊營收')
            self.assertEqual(result['audit_data'], {})

    def test_batch_audit_uses_one_request_for_multiple_stocks(self):
        stock = lambda code: {"code": code, "monthly_revenue": {}, "earnings": {},
                              "catalyst": {}, "analyst_target": {}, "disposition": {}}
        response = json.dumps({'stocks': [stock('1111'), stock('2222')]}, ensure_ascii=False)
        candidates = [{'code': '1111', 'name': '甲', 'category': '測試'},
                      {'code': '2222', 'name': '乙', 'category': '測試'}]
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(engine, 'FUNDAMENTAL_CACHE_FILE', Path(tmp) / 'cache.json'), \
             patch.object(engine, 'call_gemini_search', return_value=(response, 'test')) as api:
            results = engine.audit_candidates_with_gemini(candidates, 'key', '2026-09-04')
            self.assertEqual(api.call_count, 1)
            self.assertEqual(set(results), {'1111', '2222'})

    def test_gemini_38_is_primary_and_uses_low_thinking(self):
        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {'candidates': [{'content': {'parts': [{'text': '{}'}]}}]}

        with patch.object(engine.requests, 'post', return_value=FakeResponse()) as post:
            text, model = engine.call_gemini_search('test', 'test-key')
        self.assertEqual(model, 'gemini-3.8-flash')
        self.assertEqual(text, '{}')
        self.assertIn('/gemini-3.8-flash:generateContent', post.call_args.args[0])
        config = post.call_args.kwargs['json']['generationConfig']
        self.assertEqual(config['thinkingConfig']['thinkingLevel'], 'low')
        self.assertNotIn('temperature', config)

    def test_api_quota_failure_creates_manual_handoff(self):
        class QuotaResponse:
            status_code = 429

            @staticmethod
            def json():
                return {'error': {'message': 'quota exceeded'}}

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(engine, 'LLM_HANDOFF_FILE', Path(tmp) / 'handoff.json'), \
             patch.object(engine.requests, 'post', return_value=QuotaResponse()):
            with self.assertRaises(engine.ManualLLMResponseRequired):
                engine.call_gemini_search('manual prompt', 'test-key')
            handoff = json.loads(engine.LLM_HANDOFF_FILE.read_text(encoding='utf-8'))
            self.assertEqual(handoff['status'], 'awaiting_response')
            self.assertEqual(handoff['prompt'], 'manual prompt')

    def test_ready_manual_response_is_consumed_without_api_call(self):
        prompt = 'manual prompt'
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(engine, 'LLM_HANDOFF_FILE', Path(tmp) / 'handoff.json'):
            engine.LLM_HANDOFF_FILE.write_text(json.dumps({
                'status': 'ready', 'prompt_id': engine._manual_prompt_id(prompt),
                'prompt': prompt, 'response': '{"market_overview":"ok"}'
            }), encoding='utf-8')
            with patch.object(engine.requests, 'post') as post:
                text, model = engine.call_gemini_search(prompt, 'test-key')
            self.assertEqual(text, '{"market_overview":"ok"}')
            self.assertIn('網頁版人工查核', model)
            post.assert_not_called()

    def test_universe_relative_strength_uses_cross_section(self):
        rows = [{'ret3': n, 'ret5': n, 'score': 50, 'reasons': []} for n in range(5)]
        engine.apply_universe_relative_strength(rows)
        self.assertGreater(rows[-1]['score'], rows[0]['score'])
        self.assertEqual(rows[-1]['universe_rs_percentile'], 100.0)

    def test_large_one_day_gain_is_extended_not_breakout(self):
        result = engine.calculate_evolution_score(self.stock_from_closes([100] * 29 + [108]))
        self.assertEqual(result['setup_stage'], '漲幅延伸／追價風險')
        self.assertEqual(result['target_price'], '')
        self.assertGreater(result['technical_target_price'], result['price'])

    def test_repair_bounce_is_not_called_breakout(self):
        stock = self.stock_from_closes([105] * 28 + [100, 103], last_high=104)
        stock['kline'][-2]['high'] = 106
        result = engine.calculate_evolution_score(stock)
        self.assertEqual(result['setup_stage'], '修復反彈／尚待突破確認')

    def test_fresh_breakout_requires_moderate_gain_and_volume(self):
        stock = self.stock_from_closes([100] * 29 + [102], last_high=103,
                                       volumes=[100] * 29 + [200])
        result = engine.calculate_evolution_score(stock)
        self.assertEqual(result['setup_stage'], '剛突破起漲')


if __name__ == '__main__':
    unittest.main()
