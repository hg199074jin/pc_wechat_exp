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
