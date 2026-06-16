"""Tests for knowledge_store.py — schema, CRUD, search, run history."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from engine.services.knowledge_store import (
    init_db, save_card, get_card, list_cards, update_card, delete_card,
    bulk_update, create_run, finish_run, list_runs, get_stats,
)


def test_init_db_creates_tables(tmp_path):
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    import sqlite3
    conn = sqlite3.connect(db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert 'knowledge_cards' in tables
    assert 'knowledge_sources' in tables
    assert 'knowledge_runs' in tables


def test_save_and_get_card(tmp_path):
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    card = {
        'title': '函证差异判断',
        'type': 'audit_case',
        'score': 86,
        'summary': '结合合同和期后回款判断函证差异。',
        'why_valuable': '可复用于应收账款审计。',
        'content_md': '# 函证差异判断',
        'tags': ['函证', '应收账款'],
        'source_chat_ids': ['g1@chatroom'],
        'date': '2026-06-15',
        'sources': [{
            'chat_id': 'g1@chatroom',
            'chat_name': 'AI审计',
            'msg_id': 1,
            'sender': '张三',
            'create_time': 1781512345,
            'quote': '不能只看回函金额',
            'context': [],
        }],
    }
    card_id = save_card(db_path, card)
    loaded = get_card(db_path, card_id)
    assert loaded is not None
    assert loaded['title'] == '函证差异判断'
    assert loaded['tags'] == ['函证', '应收账款']
    assert loaded['score'] == 86
    assert len(loaded['sources']) == 1
    assert loaded['sources'][0]['quote'] == '不能只看回函金额'


def test_list_cards_with_filters(tmp_path):
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    save_card(db_path, {'title': 'A', 'type': 'sop', 'score': 90, 'date': '2026-06-15', 'status': 'inbox'})
    save_card(db_path, {'title': 'B', 'type': 'faq', 'score': 50, 'date': '2026-06-15', 'status': 'saved'})
    save_card(db_path, {'title': 'C', 'type': 'sop', 'score': 80, 'date': '2026-06-16', 'status': 'inbox'})

    # All
    result = list_cards(db_path)
    assert result['total'] == 3

    # Filter by type
    result = list_cards(db_path, card_type='sop')
    assert result['total'] == 2

    # Filter by status
    result = list_cards(db_path, status='saved')
    assert result['total'] == 1
    assert result['cards'][0]['title'] == 'B'

    # Filter by min_score
    result = list_cards(db_path, min_score=70)
    assert result['total'] == 2

    # Search
    result = list_cards(db_path, q='函证')
    assert result['total'] == 0  # no match


def test_update_card(tmp_path):
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    card_id = save_card(db_path, {'title': 'Original', 'score': 60})
    assert update_card(db_path, card_id, {'title': 'Updated', 'status': 'saved'})
    card = get_card(db_path, card_id)
    assert card['title'] == 'Updated'
    assert card['status'] == 'saved'
    assert card['score'] == 60  # unchanged


def test_delete_card(tmp_path):
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    card_id = save_card(db_path, {'title': 'To Delete', 'sources': [{'quote': 'x'}]})
    assert delete_card(db_path, card_id)
    assert get_card(db_path, card_id) is None


def test_bulk_update(tmp_path):
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    id1 = save_card(db_path, {'title': 'A', 'score': 80})
    id2 = save_card(db_path, {'title': 'B', 'score': 90})
    id3 = save_card(db_path, {'title': 'C', 'score': 70})
    count = bulk_update(db_path, [id1, id2], 'archived')
    assert count == 2
    assert get_card(db_path, id1)['status'] == 'archived'
    assert get_card(db_path, id3)['status'] == 'inbox'


def test_run_history(tmp_path):
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    run_id = create_run(db_path, '2026-06-15', '2026-06-15', ['g1@chatroom'])
    runs = list_runs(db_path)
    assert len(runs) == 1
    assert runs[0]['status'] == 'running'
    finish_run(db_path, run_id, status='done', total_messages=100, card_count=5)
    runs = list_runs(db_path)
    assert runs[0]['status'] == 'done'
    assert runs[0]['card_count'] == 5


def test_stats(tmp_path):
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    save_card(db_path, {'title': 'A', 'type': 'sop', 'score': 90, 'status': 'inbox'})
    save_card(db_path, {'title': 'B', 'type': 'faq', 'score': 70, 'status': 'saved'})
    stats = get_stats(db_path)
    assert stats['total'] == 2
    assert stats['by_status']['inbox'] == 1
    assert stats['by_type']['sop'] == 1
    assert stats['avg_score'] == 80.0


def test_card_not_found(tmp_path):
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    assert get_card(db_path, 'nonexistent') is None
    assert update_card(db_path, 'nonexistent', {'title': 'x'}) is False
    assert delete_card(db_path, 'nonexistent') is False
