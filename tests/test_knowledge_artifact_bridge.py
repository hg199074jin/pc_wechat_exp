"""Tests for converting analysis artifact knowledge candidates to cards."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from engine.services.knowledge_extractor import cards_from_analysis_artifact


def test_cards_from_artifact_filters_and_maps_sources():
    artifact = {
        'chat_id': 'c1',
        'group_name': 'AI审计',
        'date': '2026-06-15',
        'topics': [{
            'title': '工具讨论',
            'evidence': [
                {'msg_id': 1, 'sender': '张三', 'quote': '这个可以做成SOP', 'time': '10:00'},
                {'msg_id': 2, 'sender': '李四', 'quote': '低价值闲聊', 'time': '10:01'},
            ],
            'knowledge_candidates': [
                {
                    'title': 'Codex 安装 SOP',
                    'type': 'sop',
                    'score': 90,
                    'summary': '安装排障流程',
                    'why_valuable': '可复用',
                    'content_md': '步骤',
                    'tags': ['Codex'],
                    'source_msg_ids': [1],
                },
                {'title': '低分', 'type': 'note', 'score': 50, 'source_msg_ids': [2]},
            ],
        }],
    }
    cards = cards_from_analysis_artifact(artifact, min_score=80)
    assert len(cards) == 1
    assert cards[0]['title'] == 'Codex 安装 SOP'
    assert cards[0]['source_chat_ids'] == ['c1']
    assert cards[0]['sources'][0]['msg_id'] == 1
    assert cards[0]['sources'][0]['quote'] == '这个可以做成SOP'


def test_cards_from_artifact_marks_missing_source():
    artifact = {
        'chat_id': 'c1',
        'group_name': 'AI审计',
        'date': '2026-06-15',
        'topics': [{
            'evidence': [],
            'knowledge_candidates': [{
                'title': '无来源卡片',
                'type': 'note',
                'score': 90,
                'source_msg_ids': [999],
            }],
        }],
    }
    cards = cards_from_analysis_artifact(artifact, min_score=80)
    assert len(cards) == 1
    assert cards[0]['sources'] == []
    assert '证据缺失' in cards[0]['why_valuable']
