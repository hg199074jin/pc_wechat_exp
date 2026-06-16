"""Tests for knowledge_extractor.py — prompt building, JSON parsing, extraction."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from engine.services.knowledge_extractor import (
    parse_llm_cards, build_knowledge_prompt, format_messages_for_knowledge,
    extract_cards_from_messages, build_convert_prompt, _extract_json_object,
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
