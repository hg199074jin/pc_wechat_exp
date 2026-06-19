"""Tests for artifact-first AI group analysis."""
import json
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from engine.services import ai_analyzer
from engine.services.analysis_artifact import load_artifact


def _messages():
    return [{
        'id': 1,
        'msg_id': 1,
        'create_time': 1781512345,
        'msg_type': 1,
        'sender_name': '张三',
        'content': '这个可以做成SOP',
    }]


def test_format_messages_for_artifact_includes_msg_id():
    text = ai_analyzer.format_messages_for_artifact(_messages())
    assert '[msg_id=1]' in text
    assert '张三' in text
    assert '这个可以做成SOP' in text


def test_build_artifact_prompt_requires_json():
    system, user = ai_analyzer.build_artifact_prompt(
        '测试群', '2026-06-15',
        {'message_count': 1, 'unique_senders': 1, 'total_chars': 10},
        '[msg_id=1] [10:00] 张三: 这个可以做成SOP',
        style_prompt='',
    )
    assert '只输出 JSON' in system
    assert 'source_msg_ids' in system
    assert '[msg_id=1]' in user


def test_analyze_group_writes_artifact_and_markdown():
    raw = json.dumps({
        'summary': '今天讨论了 SOP。',
        'topics': [{
            'title': 'SOP',
            'summary': '讨论可复用流程。',
            'participants': ['张三'],
            'evidence': [{'msg_id': 1, 'sender': '张三', 'quote': '这个可以做成SOP'}],
            'knowledge_candidates': [{
                'title': 'SOP 卡片',
                'type': 'sop',
                'score': 90,
                'summary': '摘要',
                'why_valuable': '可复用',
                'content_md': '正文',
                'tags': ['SOP'],
                'source_msg_ids': [1],
            }],
        }],
    }, ensure_ascii=False)
    with tempfile.TemporaryDirectory() as tmp:
        decrypted = os.path.join(tmp, 'backup')
        os.makedirs(decrypted)
        with patch('engine.services.ai_analyzer.load_llm_config', return_value={
            'base_url': 'https://api.example.com/v1',
            'api_key': 'sk',
            'model': 'm',
        }), patch('engine.services.ai_analyzer.query_messages', return_value={'messages': _messages()}), \
                patch('engine.services.ai_analyzer.call_llm', return_value='```json\n' + raw + '\n```'):
            markdown, status, count = ai_analyzer.analyze_group(
                decrypted, 'c@chatroom', '测试群', '2026-06-15'
            )
        assert status == 'ok'
        assert count == 1
        assert markdown.startswith('# 群聊分析报告：测试群')
        artifact = load_artifact(ai_analyzer.storage_dir_for(decrypted), 'c@chatroom', '2026-06-15')
        assert artifact['summary'] == '今天讨论了 SOP。'
        assert artifact['verify']['status'] == 'verified'


def test_analyze_group_invalid_json_returns_error():
    with tempfile.TemporaryDirectory() as tmp:
        decrypted = os.path.join(tmp, 'backup')
        os.makedirs(decrypted)
        with patch('engine.services.ai_analyzer.load_llm_config', return_value={
            'base_url': 'https://api.example.com/v1',
            'api_key': 'sk',
            'model': 'm',
        }), patch('engine.services.ai_analyzer.query_messages', return_value={'messages': _messages()}), \
                patch('engine.services.ai_analyzer.call_llm', return_value='not json'):
            msg, status, count = ai_analyzer.analyze_group(
                decrypted, 'c@chatroom', '测试群', '2026-06-15'
            )
        assert status == 'error'
        assert count == 1
        assert '合法 JSON' in msg


def test_analyze_group_range_uses_date_range_and_saves_range_label():
    raw = json.dumps({
        'summary': 'range summary',
        'topics': [{
            'title': 'range topic',
            'summary': 'range topic summary',
            'participants': ['A'],
            'evidence': [{'msg_id': 1, 'sender': 'A', 'quote': 'range'}],
            'knowledge_candidates': [],
        }],
    }, ensure_ascii=False)
    with tempfile.TemporaryDirectory() as tmp:
        decrypted = os.path.join(tmp, 'backup')
        os.makedirs(decrypted)
        with patch('engine.services.ai_analyzer.load_llm_config', return_value={
            'base_url': 'https://api.example.com/v1',
            'api_key': 'sk',
            'model': 'm',
        }), patch('engine.services.ai_analyzer.query_messages', return_value={'messages': _messages()}) as mock_query, \
                patch('engine.services.ai_analyzer.call_llm', return_value=raw):
            markdown, status, count = ai_analyzer.analyze_group_range(
                decrypted, 'c@chatroom', 'Range Group', '2026-06-01', '2026-06-19'
            )

        assert status == 'ok'
        assert count == 1
        assert 'Range Group' in markdown
        assert mock_query.call_args.kwargs['start_date'] == '2026-06-01'
        assert mock_query.call_args.kwargs['end_date'] == '2026-06-19'
        artifact = load_artifact(
            ai_analyzer.storage_dir_for(decrypted),
            'c@chatroom',
            '2026-06-01_to_2026-06-19',
        )
        assert artifact['date'] == '2026-06-01_to_2026-06-19'


def test_analyze_group_repairs_malformed_artifact_json_once():
    repaired = json.dumps({
        'summary': 'repaired',
        'topics': [{
            'title': 'fixed',
            'summary': 'fixed summary',
            'participants': ['A'],
            'evidence': [{'msg_id': 1, 'sender': 'A', 'quote': 'fixed'}],
            'knowledge_candidates': [],
        }],
    }, ensure_ascii=False)
    with tempfile.TemporaryDirectory() as tmp:
        decrypted = os.path.join(tmp, 'backup')
        os.makedirs(decrypted)
        with patch('engine.services.ai_analyzer.load_llm_config', return_value={
            'base_url': 'https://api.example.com/v1',
            'api_key': 'sk',
            'model': 'm',
        }), patch('engine.services.ai_analyzer.query_messages', return_value={'messages': _messages()}), \
                patch('engine.services.ai_analyzer.call_llm', side_effect=['{"summary":"broken"', repaired]) as mock_call:
            markdown, status, count = ai_analyzer.analyze_group(
                decrypted, 'c@chatroom', 'Repair Group', '2026-06-15'
            )

        assert status == 'ok'
        assert count == 1
        assert mock_call.call_count == 2
        assert 'Repair Group' in markdown


def test_preprocess_llm_json_strips_comments_and_trailing_commas():
    from engine.services.ai_analyzer import _preprocess_llm_json
    # Trailing commas before } and ].
    assert _preprocess_llm_json('{"a": 1,}') == '{"a": 1}'
    assert _preprocess_llm_json('{"a": [1, 2,],}') == '{"a": [1, 2]}'
    # Line comments are stripped.
    assert 'comment' not in _preprocess_llm_json('{"a": 1 // comment\n}')
    # Block comments are stripped.
    assert 'x' not in _preprocess_llm_json('{"a": /* x */ 1}')


def test_preprocess_llm_json_strips_reasoning_block_before_json():
    from engine.services.ai_analyzer import _preprocess_llm_json
    raw = '<think>先分析格式</think>\n{"summary":"ok","topics":[]}'
    assert _preprocess_llm_json(raw) == '{"summary":"ok","topics":[]}'


def test_preprocess_llm_json_recovers_via_parse():
    """Trailing-comma cleanup should let parse_artifact_json succeed."""
    from engine.services.ai_analyzer import _preprocess_llm_json, parse_artifact_json
    raw = '{"summary": "ok", "topics": [],}'
    fixed = _preprocess_llm_json(raw)
    data = parse_artifact_json(fixed)
    assert data['summary'] == 'ok'


def test_repair_succeeds_on_second_round():
    """First repair fails, second repair succeeds (2-round retry loop)."""
    from engine.services.ai_analyzer import _parse_artifact_json_with_repair
    good = json.dumps({'summary': 'ok', 'topics': []}, ensure_ascii=False)
    cfg = {'base_url': 'u', 'api_key': 'k', 'model': 'm'}
    # Round 1 returns bad JSON, round 2 returns good JSON.
    with patch('engine.services.ai_analyzer.call_llm',
               side_effect=['{bad', good]) as mock_call:
        result = _parse_artifact_json_with_repair('{originally broken', cfg)
    assert result['summary'] == 'ok'
    assert mock_call.call_count == 2


def test_repair_exhausts_rounds_and_raises():
    """Both repair rounds fail -> ValueError mentions 2 attempts."""
    from engine.services.ai_analyzer import _parse_artifact_json_with_repair
    cfg = {'base_url': 'u', 'api_key': 'k', 'model': 'm'}
    with patch('engine.services.ai_analyzer.call_llm',
               side_effect=['{still bad', '{still bad 2']) as mock_call:
        try:
            _parse_artifact_json_with_repair('{broken', cfg)
            raise AssertionError('expected ValueError')
        except ValueError as e:
            assert '修复' in str(e)
    assert mock_call.call_count == 2


def test_repair_stops_when_model_returns_reasoning_without_json():
    """A reasoning-only repair must not be fed into another repair request."""
    from engine.services.ai_analyzer import _parse_artifact_json_with_repair
    cfg = {'base_url': 'u', 'api_key': 'k', 'model': 'm'}
    with patch('engine.services.ai_analyzer.call_llm',
               return_value='<think>我来修复 JSON</think>') as mock_call:
        try:
            _parse_artifact_json_with_repair('{broken', cfg)
            raise AssertionError('expected ValueError')
        except ValueError as e:
            assert '仅包含推理文本' in str(e)
    assert mock_call.call_count == 1


def test_rollup_falls_back_to_deterministic_merge_when_json_repair_fails():
    """Valid chunk artifacts remain usable even when LLM rollup JSON fails."""
    partials = [
        {
            'summary': '第一块讨论 SOP。',
            'topics': [{
                'title': 'SOP',
                'summary': '第一块的流程讨论。',
                'participants': ['甲'],
                'evidence': [{'msg_id': 1, 'sender': '甲', 'quote': '先做流程'}],
                'knowledge_candidates': [{
                    'title': '流程 SOP', 'score': 'invalid', 'tags': ['流程'],
                    'source_msg_ids': [1],
                }],
            }],
            'followups': [{'title': '整理流程', 'evidence_msg_ids': [1]}],
        },
        {
            'summary': '第二块补充 SOP。',
            'topics': [{
                'title': 'SOP',
                'summary': '第二块的补充讨论。',
                'participants': ['乙'],
                'evidence': [{'msg_id': 2, 'sender': '乙', 'quote': '补充检查'}],
                'knowledge_candidates': [{
                    'title': '流程 SOP', 'score': 92, 'tags': ['审计'],
                    'source_msg_ids': [2],
                }],
            }],
            'followups': [{'title': '整理流程', 'evidence_msg_ids': [2]}],
        },
    ]
    cfg = {'base_url': 'u', 'api_key': 'k', 'model': 'm'}
    with patch('engine.services.ai_analyzer.call_llm',
               side_effect=['{broken', '<think>只输出了推理</think>']):
        result = ai_analyzer._rollup_artifacts(
            '测试群', '2026-06-15', {'message_count': 2}, partials, '', cfg
        )
    assert result['stats']['rollup_mode'] == 'deterministic'
    assert len(result['topics']) == 1
    topic = result['topics'][0]
    assert topic['participants'] == ['甲', '乙']
    assert [item['msg_id'] for item in topic['evidence']] == [1, 2]
    assert topic['knowledge_candidates'][0]['score'] == 92
    assert topic['knowledge_candidates'][0]['tags'] == ['流程', '审计']
    assert result['followups'][0]['evidence_msg_ids'] == [1, 2]


def test_rollup_fallback_uses_markdown_polish_when_available():
    """Fallback keeps a final LLM prose pass without requiring JSON output."""
    artifact = {
        'summary': '结构化摘要',
        'topics': [],
        'stats': {'rollup_mode': 'deterministic'},
    }
    cfg = {'base_url': 'u', 'api_key': 'k', 'model': 'm'}
    with patch('engine.services.ai_analyzer.call_llm',
               return_value='# 群聊分析报告：测试群\n\n## 总体摘要\n润色后的总结'):
        markdown = ai_analyzer._render_artifact_markdown(
            artifact, '测试群', '2026-06-15', cfg
        )
    assert '润色后的总结' in markdown


def test_rollup_fallback_uses_structured_report_when_polish_is_invalid():
    artifact = {
        'summary': '结构化摘要',
        'topics': [{
            'title': 'SOP',
            'summary': '流程讨论。',
            'participants': ['甲'],
            'evidence': [],
            'knowledge_candidates': [],
        }],
        'followups': [],
        'stats': {'rollup_mode': 'deterministic'},
    }
    cfg = {'base_url': 'u', 'api_key': 'k', 'model': 'm'}
    with patch('engine.services.ai_analyzer.call_llm',
               return_value='<think>没有最终回答</think>'):
        markdown = ai_analyzer._render_artifact_markdown(
            artifact, '测试群', '2026-06-15', cfg
        )
    assert markdown.startswith('# 群聊分析报告：测试群')
    assert 'SOP' in markdown


def test_repair_failure_writes_debug_file():
    """Exhausted JSON repairs should persist raw outputs for debugging."""
    from engine.services.ai_analyzer import _parse_artifact_json_with_repair
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {
            'base_url': 'u',
            'api_key': 'k',
            'model': 'm',
            '_debug_dir': tmp,
        }
        with patch('engine.services.ai_analyzer.call_llm',
                   side_effect=['{"summary": "still bad"', '{"summary": bad}']):
            try:
                _parse_artifact_json_with_repair(
                    '{"summary": "broken"',
                    cfg,
                    debug_context={
                        'group_name': '测试群',
                        'date': '2026-06-15',
                        'phase': 'artifact',
                    },
                )
                raise AssertionError('expected ValueError')
            except ValueError as e:
                assert '调试文件' in str(e)

        files = os.listdir(tmp)
        assert len(files) == 1
        path = os.path.join(tmp, files[0])
        with open(path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        assert payload['context']['group_name'] == '测试群'
        assert payload['raw_output'] == '{"summary": "broken"'
        assert payload['repair_rounds'][0]['raw_output'] == '{"summary": "still bad"'
        assert payload['repair_rounds'][0]['error']['pos'] is not None
