"""Tests for knowledge_export.py — Markdown export."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from engine.services.knowledge_export import cards_to_markdown


def test_export_cards_to_markdown():
    cards = [{
        'title': '函证差异判断',
        'score': 90,
        'type': 'audit_case',
        'status': 'inbox',
        'date': '2026-06-15',
        'tags': ['函证', '应收账款'],
        'summary': '结合合同和期后回款判断。',
        'why_valuable': '可复用于类似项目。',
        'content_md': '# 函证差异判断\n\n1. 核对合同\n2. 检查回款',
        'sources': [{
            'chat_name': 'AI审计',
            'sender': '张三',
            'create_time': 1781512345,
            'quote': '不能只看回函金额',
        }],
    }]
    md = cards_to_markdown(cards)
    assert '# 知识沉淀导出' in md
    assert '函证差异判断' in md
    assert '评分' in md
    assert '90' in md
    assert '函证' in md
    assert 'AI审计' in md
    assert '不能只看回函金额' in md


def test_export_multiple_cards():
    cards = [
        {'title': 'A', 'score': 80, 'type': 'sop', 'tags': [], 'sources': []},
        {'title': 'B', 'score': 70, 'type': 'faq', 'tags': [], 'sources': []},
    ]
    md = cards_to_markdown(cards)
    assert md.count('## ') == 2
    assert 'A' in md
    assert 'B' in md


def test_export_empty_cards():
    md = cards_to_markdown([])
    assert '# 知识沉淀导出' in md
    assert md.count('## ') == 0
