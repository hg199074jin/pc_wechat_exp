"""Tests for reusing AI analysis artifacts in Knowledge Radar."""
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from engine.services.ai_analyzer import storage_dir_for
from engine.services.analysis_artifact import normalize_artifact, save_artifact
from web.routes.knowledge_api import (
    _cards_from_existing_artifact,
    _knowledge_source_mode,
    _save_scan_cards,
)


def test_cards_from_existing_artifact_converts_candidates():
    with tempfile.TemporaryDirectory() as tmp:
        decrypted = os.path.join(tmp, 'backup')
        os.makedirs(decrypted)
        artifact = normalize_artifact({
            'summary': 'summary',
            'topics': [{
                'title': 'topic',
                'summary': 'topic summary',
                'evidence': [{'msg_id': 1, 'sender': 'A', 'quote': 'quote'}],
                'knowledge_candidates': [{
                    'title': 'Reusable card',
                    'type': 'note',
                    'score': 90,
                    'summary': 'card summary',
                    'why_valuable': 'valuable',
                    'content_md': 'content',
                    'tags': ['AI'],
                    'source_msg_ids': [1],
                }],
            }],
        }, chat_id='c@chatroom', group_name='Chat', date='2026-06-01_to_2026-06-19', stats={})
        save_artifact(storage_dir_for(decrypted), artifact)

        cards = _cards_from_existing_artifact(
            decrypted, 'c@chatroom', '2026-06-01_to_2026-06-19', 80
        )

    assert cards is not None
    assert len(cards) == 1
    assert cards[0]['title'] == 'Reusable card'
    assert cards[0]['source_chat_ids'] == ['c@chatroom']


def test_cards_from_existing_artifact_returns_none_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        decrypted = os.path.join(tmp, 'backup')
        os.makedirs(decrypted)
        cards = _cards_from_existing_artifact(decrypted, 'missing@chatroom', '2026-06-01', 80)
    assert cards is None


def test_save_scan_cards_sets_chat_id_and_limits_count():
    cards = [
        {'title': 'A', 'sources': [{'msg_id': 1}]},
        {'title': 'B', 'sources': [{'msg_id': 2}]},
    ]
    with patch('web.routes.knowledge_api.store.save_card') as mock_save:
        saved = _save_scan_cards('db.sqlite', cards, 'c@chatroom', 1)

    assert saved == 1
    assert mock_save.call_count == 1
    saved_card = mock_save.call_args.args[1]
    assert saved_card['source_chat_ids'] == ['c@chatroom']
    assert saved_card['sources'][0]['chat_id'] == 'c@chatroom'


def test_knowledge_source_mode_defaults_to_quality_first_llm():
    assert _knowledge_source_mode({}) == 'llm'
    assert _knowledge_source_mode({'knowledge_source': 'auto'}) == 'auto'
    assert _knowledge_source_mode({'knowledge_source': 'artifact_only'}) == 'artifact_only'
    assert _knowledge_source_mode({'knowledge_source': 'bad'}) == 'llm'
