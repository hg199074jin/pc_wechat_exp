"""Tests for knowledge_extractor.py — prompt building, JSON parsing, extraction."""
import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from engine.services.knowledge_extractor import (
    parse_llm_cards, build_knowledge_prompt, format_messages_for_knowledge,
    extract_cards_from_messages, build_convert_prompt, _extract_json_object,
    make_llm_call, readable_message_text, load_messages_for_scan,
    extract_cards_from_messages_chunked, dedupe_knowledge_cards,
    build_my_version_prompt, build_agent_rule_prompt, parse_agent_rule_draft,
)


def test_parse_llm_cards_filters_low_score():
    raw = json.dumps({
        "cards": [
            {"title": "高价值", "type": "sop", "score": 88, "summary": "x",
             "why_valuable": "y", "content_md": "z", "tags": ["AI"], "source_msg_ids": [1]},
            {"title": "低价值", "type": "note", "score": 40, "summary": "x",
             "why_valuable": "y", "content_md": "z", "tags": [], "source_msg_ids": [2]},
        ]
    })
    cards = parse_llm_cards(raw, min_score=70)
    assert len(cards) == 1
    assert cards[0]['title'] == '高价值'
    assert cards[0]['score'] == 88


def test_parse_llm_cards_handles_empty():
    cards = parse_llm_cards('{"cards": []}', min_score=0)
    assert cards == []


def test_parse_llm_cards_strips_markdown_fence():
    raw = '```json\n{"cards":[{"title":"T","type":"sop","score":80,"summary":"s","why_valuable":"w","content_md":"c","tags":[],"source_msg_ids":[]}]}\n```'
    cards = parse_llm_cards(raw, min_score=70)
    assert len(cards) == 1
    assert cards[0]['title'] == 'T'


def test_parse_llm_cards_strips_reasoning_before_json():
    raw = '<think>先检查字段</think>\n{"cards":[]}'
    assert parse_llm_cards(raw, min_score=0) == []


def test_parse_llm_cards_normalizes_type():
    raw = json.dumps({"cards": [
        {"title": "X", "type": "unknown_type", "score": 80, "summary": "", "why_valuable": "", "content_md": "", "tags": [], "source_msg_ids": []}
    ]})
    cards = parse_llm_cards(raw, min_score=0)
    assert cards[0]['type'] == 'note'


def test_format_messages_includes_msg_ids():
    messages = [{
        'id': 123,
        'create_time': 1781512345,
        'sender_name': '张三',
        'content': '这个可以做成SOP',
        'msg_type': 1,
    }]
    text = format_messages_for_knowledge(messages)
    assert '[msg_id=123]' in text
    assert '张三' in text
    assert '这个可以做成SOP' in text


def test_readable_message_text_extracts_xml_title():
    xml = '<?xml version="1.0"?><msg><appmsg><title>我发现一个问题超过一定的问答量，就会开始慢</title><type>57</type></appmsg></msg>'
    assert readable_message_text(xml) == '我发现一个问题超过一定的问答量，就会开始慢'


def test_format_messages_extracts_link_title_from_xml():
    messages = [{
        'id': 123,
        'create_time': 1781512345,
        'sender_name': '张三',
        'content': '<?xml version="1.0"?><msg><appmsg><title>只要这个标题</title></appmsg></msg>',
    }]
    text = format_messages_for_knowledge(messages)
    assert '只要这个标题' in text
    assert '<?xml' not in text


def test_format_messages_skips_empty():
    messages = [
        {'id': 1, 'create_time': 100, 'sender_name': 'A', 'content': 'hello'},
        {'id': 2, 'create_time': 101, 'sender_name': 'B', 'content': ''},
        {'id': 3, 'create_time': 102, 'sender_name': 'C', 'content': '  '},
    ]
    text = format_messages_for_knowledge(messages)
    assert 'hello' in text
    assert text.count('[msg_id=') == 1


def test_format_messages_sorted_by_time():
    messages = [
        {'id': 2, 'create_time': 200, 'sender_name': 'B', 'content': 'second'},
        {'id': 1, 'create_time': 100, 'sender_name': 'A', 'content': 'first'},
    ]
    text = format_messages_for_knowledge(messages)
    pos_first = text.index('first')
    pos_second = text.index('second')
    assert pos_first < pos_second


def test_build_knowledge_prompt_contains_min_score():
    system, user = build_knowledge_prompt('audit', 80, 'test messages')
    assert '80' in system
    assert 'test messages' in user


def test_build_knowledge_prompt_domain_guidance():
    system, _ = build_knowledge_prompt('audit', 70, '')
    assert '审计' in system
    system2, _ = build_knowledge_prompt('ai', 70, '')
    assert 'prompt' in system2.lower() or 'Prompt' in system2


def test_extract_cards_with_fake_llm():
    messages = [{'id': 1, 'create_time': 1781512345, 'sender_name': 'A', 'content': '可复用SOP'}]

    def fake_llm(system, user):
        return json.dumps({"cards": [{
            "title": "可复用SOP", "type": "sop", "score": 90,
            "summary": "x", "why_valuable": "y", "content_md": "z",
            "tags": ["SOP"], "source_msg_ids": [1],
        }]})

    cards = extract_cards_from_messages(messages, 'AI审计', '2026-06-15', fake_llm, min_score=70)
    assert len(cards) == 1
    assert cards[0]['title'] == '可复用SOP'
    assert cards[0]['date'] == '2026-06-15'
    assert len(cards[0]['sources']) == 1
    assert cards[0]['sources'][0]['msg_id'] == 1
    assert cards[0]['sources'][0]['chat_name'] == 'AI审计'


def test_extract_cards_repairs_malformed_json_once():
    messages = [{'id': 1, 'create_time': 100, 'sender_name': 'A', 'content': '可复用SOP'}]
    repaired = json.dumps({'cards': [{
        'title': '修复后卡片', 'type': 'sop', 'score': 90,
        'summary': 's', 'why_valuable': 'w', 'content_md': 'c',
        'tags': [], 'source_msg_ids': [1],
    }]})
    calls = []

    def fake_llm(system, user):
        calls.append((system, user))
        return '{"cards":[' if len(calls) == 1 else repaired

    cards = extract_cards_from_messages(messages, '测试群', '2026-06-15', fake_llm)
    assert [card['title'] for card in cards] == ['修复后卡片']
    assert len(calls) == 2


def test_extract_cards_skips_reasoning_only_repair_and_reports_error():
    messages = [{'id': 1, 'create_time': 100, 'sender_name': 'A', 'content': '可复用SOP'}]
    errors = []
    calls = []

    def fake_llm(system, user):
        calls.append((system, user))
        return '{"cards":[' if len(calls) == 1 else '<think>只输出推理</think>'

    cards = extract_cards_from_messages(
        messages, '测试群', '2026-06-15', fake_llm, error_cb=errors.append
    )
    assert cards == []
    assert len(calls) == 2
    assert len(errors) == 1
    assert '仅包含推理文本' in errors[0]


def test_extract_cards_empty_messages():
    cards = extract_cards_from_messages([], 'test', '2026-01-01', lambda s, u: '{}', min_score=0)
    assert cards == []


def test_extract_cards_fallback_source():
    """When source_msg_ids don't match, fallback to first message."""
    messages = [{'id': 99, 'create_time': 100, 'sender_name': 'X', 'content': 'hello'}]

    def fake_llm(system, user):
        return json.dumps({"cards": [{
            "title": "T", "type": "note", "score": 80,
            "summary": "s", "why_valuable": "w", "content_md": "c",
            "tags": [], "source_msg_ids": [12345],  # doesn't exist
        }]})

    cards = extract_cards_from_messages(messages, 'test', '2026-01-01', fake_llm, min_score=0)
    assert cards[0]['sources'][0]['msg_id'] == 99  # fallback


def test_build_convert_prompt():
    card = {'title': 'x', 'content_md': 'y', 'type': 'note', 'summary': 's', 'tags': ['a']}
    system, user = build_convert_prompt(card, 'sop')
    assert 'SOP' in system or 'sop' in system.lower()
    assert 'x' in user


def test_extract_json_object_strips_fence():
    text = '```json\n{"cards":[]}\n```'
    result = _extract_json_object(text)
    assert result == '{"cards":[]}'


def test_extract_json_object_handles_no_fence():
    text = '  {"cards":[]}  '
    result = _extract_json_object(text)
    assert result == '{"cards":[]}'


def test_extract_json_object_uses_first_complete_object():
    text = '{"cards":[]}{"extra":true}'
    result = _extract_json_object(text)
    assert result == '{"cards":[]}'


def test_parse_llm_cards_ignores_trailing_explanation():
    raw = '{"cards":[{"title":"T","type":"note","score":80,"summary":"","why_valuable":"","content_md":"","tags":[],"source_msg_ids":[]}]}\\n说明：以上是结果'
    cards = parse_llm_cards(raw, min_score=70)
    assert len(cards) == 1
    assert cards[0]['title'] == 'T'


def test_make_llm_call_uses_configured_timeout():
    with patch('engine.services.ai_analyzer.load_llm_config', return_value={
        'base_url': 'https://api.example.com/v1',
        'api_key': 'sk',
        'model': 'm',
        'timeout': 600,
    }), patch('engine.services.ai_analyzer.call_llm', return_value='{"cards": []}') as mock_call:
        llm_call = make_llm_call('dummy')
        llm_call('system', 'user')
    assert mock_call.call_args.kwargs['timeout'] == 600


def test_load_messages_for_scan_accepts_end_date():
    with patch('engine.services.message.query_messages', return_value={'messages': []}) as mock_query:
        load_messages_for_scan('decrypted', 'c@chatroom', '2026-06-01', wxid='me', end_date='2026-06-19')
    assert mock_query.call_args.kwargs['start_date'] == '2026-06-01'
    assert mock_query.call_args.kwargs['end_date'] == '2026-06-19'


def test_dedupe_knowledge_cards_merges_sources_and_tags():
    cards = [
        {'title': 'Same', 'score': 80, 'tags': ['AI'], 'sources': [{'chat_id': 'c', 'msg_id': 1}]},
        {'title': 'Same', 'score': 90, 'tags': ['Audit'], 'sources': [{'chat_id': 'c', 'msg_id': 2}]},
    ]
    merged = dedupe_knowledge_cards(cards)
    assert len(merged) == 1
    assert merged[0]['score'] == 90
    assert merged[0]['tags'] == ['AI', 'Audit']
    assert [s['msg_id'] for s in merged[0]['sources']] == [2, 1]


def test_extract_cards_from_messages_chunked_dedupes_large_inputs():
    messages = []
    for i in range(20):
        messages.append({
            'id': i,
            'create_time': i,
            'sender_name': 'A',
            'content': 'x' * 700,
        })

    def fake_llm(system, user):
        first_id = 0
        if '[msg_id=' in user:
            first_id = int(user.split('[msg_id=')[1].split(']')[0])
        return json.dumps({"cards": [{
            "title": "Same", "type": "note", "score": 80,
            "summary": "s", "why_valuable": "w", "content_md": "c",
            "tags": ["T"], "source_msg_ids": [first_id],
        }]})

    cards = extract_cards_from_messages_chunked(
        messages, 'chat', '2026-06-01_to_2026-06-19', fake_llm, min_score=0
    )
    assert len(cards) == 1
    assert cards[0]['date'] == '2026-06-01_to_2026-06-19'
    assert len(cards[0]['sources']) >= 1


def test_chunked_extraction_keeps_successful_chunks_when_one_chunk_has_bad_json():
    messages = []
    for i in range(20):
        messages.append({
            'id': i,
            'create_time': i,
            'sender_name': 'A',
            'content': 'x' * 700,
        })
    calls = []
    errors = []

    def fake_llm(system, user):
        calls.append((system, user))
        if len(calls) == 1:
            return '{"cards":['
        if len(calls) == 2:
            return '<think>只输出推理</think>'
        return json.dumps({'cards': [{
            'title': '后续分块知识', 'type': 'note', 'score': 90,
            'summary': 's', 'why_valuable': 'w', 'content_md': 'c',
            'tags': [], 'source_msg_ids': [10],
        }]})

    cards = extract_cards_from_messages_chunked(
        messages, '测试群', '2026-06-01_to_2026-06-19', fake_llm,
        min_score=0, error_cb=errors.append,
    )
    assert [card['title'] for card in cards] == ['后续分块知识']
    assert errors


# ---------------------------------------------------------------------------
# My-version derivative + agent-rule draft (Task 3)
# ---------------------------------------------------------------------------

def _sample_card():
    return {
        'title': 'Codex 子任务拆分',
        'type': 'methodology',
        'summary': '把大任务拆成子任务并行执行更稳',
        'why_valuable': '减少单次上下文，避免遗漏',
        'content_md': '正文：拆分原则…',
        'tags': ['AI', '编程'],
        'sources': [{'chat_name': 'AI提效群', 'sender': '张三', 'quote': '拆分更稳'}],
    }


def test_build_my_version_prompt_contains_required_headings():
    system, user = build_my_version_prompt(_sample_card())
    # All seven required Markdown sections must be requested.
    for heading in ['原始观点', '对我的启发', '适用场景', '我的理解',
                    '可执行动作', '可复用', '是否建议长期保留']:
        assert heading in system or heading in user
    # Evidence is cited.
    assert '张三' in user or 'AI提效群' in user
    assert '不编造' in system or '不要编造' in system


def test_build_agent_rule_prompt_requests_executable_rule():
    card = _sample_card()
    system, user = build_agent_rule_prompt(card, derivative=None)
    assert '规则' in system
    assert 'Codex 子任务拆分' in user
    # Must demand concrete directives, not generic advice.
    assert '具体' in system or '可执行' in system


def test_parse_agent_rule_draft_valid():
    raw = json.dumps({
        'title': '拆分大任务为子任务',
        'category': 'engineering',
        'content_md': '当一个任务涉及超过3个文件时，先拆成子任务。',
    })
    rule = parse_agent_rule_draft(raw)
    assert rule['title'] == '拆分大任务为子任务'
    assert rule['category'] == 'engineering'
    assert rule['content_md'].startswith('当一个任务')


def test_parse_agent_rule_draft_unknown_category_normalizes():
    raw = json.dumps({'title': 't', 'category': 'mystery', 'content_md': 'c'})
    rule = parse_agent_rule_draft(raw)
    assert rule['category'] == 'general'


def test_parse_agent_rule_draft_missing_content_raises():
    import pytest
    with pytest.raises(ValueError):
        parse_agent_rule_draft(json.dumps({'title': 't', 'category': 'general'}))


def test_parse_agent_rule_draft_invalid_json_raises():
    import pytest
    with pytest.raises(ValueError):
        parse_agent_rule_draft('not json at all {{')
