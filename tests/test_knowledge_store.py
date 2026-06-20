"""Tests for knowledge_store.py — schema, CRUD, search, run history."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from engine.services.knowledge_store import (
    init_db, save_card, get_card, list_cards, update_card, delete_card,
    bulk_update, create_run, finish_run, list_runs, get_stats,
    create_derivative, list_derivatives, get_derivative,
    create_agent_rule, get_agent_rule, list_agent_rules, update_agent_rule,
    publish_agent_rule, archive_agent_rule, get_agent_rule_stats,
    CardHasRulesError, CURRENT_SCHEMA_VERSION,
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


def test_save_card_preserves_created_at_on_replace(tmp_path):
    """Re-saving a card without created_at must keep the original timestamp."""
    import time as _time
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    before = int(_time.time())
    card_id = save_card(db_path, {'title': 'First', 'score': 60})
    original = get_card(db_path, card_id)
    assert original['created_at'] >= before
    # Re-save without created_at (typical re-scan): created_at must not reset.
    _time.sleep(1)
    save_card(db_path, {'id': card_id, 'title': 'Updated', 'score': 90})
    updated = get_card(db_path, card_id)
    assert updated['created_at'] == original['created_at']
    assert updated['score'] == 90


def test_list_cards_escapes_like_wildcards(tmp_path):
    """Search terms with % or _ must be matched literally, not as patterns."""
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    save_card(db_path, {'title': '100%完成', 'score': 80})
    save_card(db_path, {'title': '另一个卡片', 'score': 70})
    result = list_cards(db_path, q='%', limit=10)
    # Only the card literally containing '%' should match, not both.
    titles = [c['title'] for c in result['cards']]
    assert titles == ['100%完成']


def test_init_db_cached_does_not_rebuild(tmp_path):
    """init_db should skip the DDL script after the first run for a db_path."""
    from engine.services import knowledge_store as ks
    db_path = str(tmp_path / 'knowledge.db')
    ks.init_db(db_path)
    assert os.path.abspath(db_path) in ks._initialized_dbs
    # Second call is a no-op (cached); should not raise and should not drop data.
    ks.init_db(db_path)
    cid = save_card(db_path, {'title': 'survives cache', 'score': 50})
    assert get_card(db_path, cid) is not None


# ---------------------------------------------------------------------------
# Migration / lifecycle / derivatives / agent rules (Task 2)
# ---------------------------------------------------------------------------

def _make_old_db(db_path):
    """Create a pre-migration database without lifecycle_stage/derivatives/rules."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE knowledge_cards (
        id TEXT PRIMARY KEY, title TEXT, type TEXT DEFAULT 'note',
        status TEXT DEFAULT 'inbox', score INTEGER DEFAULT 0,
        summary TEXT DEFAULT '', why_valuable TEXT DEFAULT '',
        content_md TEXT DEFAULT '', tags_json TEXT DEFAULT '[]',
        source_chat_ids_json TEXT DEFAULT '[]', date TEXT DEFAULT '',
        created_at INTEGER, updated_at INTEGER)""")
    conn.execute("INSERT INTO knowledge_cards (id, title, created_at, updated_at) VALUES ('c1', 'old', 1, 1)")
    conn.commit()
    conn.close()


def test_migration_adds_lifecycle_and_tables(tmp_path):
    db_path = str(tmp_path / 'knowledge.db')
    _make_old_db(db_path)
    init_db(db_path)
    import sqlite3
    conn = sqlite3.connect(db_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(knowledge_cards)")}
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert 'lifecycle_stage' in cols
    assert 'knowledge_derivatives' in tables
    assert 'agent_rules' in tables
    assert version == CURRENT_SCHEMA_VERSION
    # Old card survives with default lifecycle.
    card = get_card(db_path, 'c1')
    assert card['lifecycle_stage'] == 'captured'


def test_migration_is_idempotent(tmp_path):
    """Second init_db after creating derivatives/rules must not drop data."""
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    cid = save_card(db_path, {'title': 'card', 'score': 80})
    did = create_derivative(db_path, cid, 'my_version', '我的版本', '内容')
    rid = create_agent_rule(db_path, cid, title='规则', content_md='正文')
    # Re-run init_db (simulates process restart or concurrent init).
    init_db(db_path)
    assert get_derivative(db_path, did) is not None
    assert get_agent_rule(db_path, rid) is not None
    assert get_card(db_path, cid)['lifecycle_stage'] == 'transformed'


def test_update_card_lifecycle_preserves_other_fields(tmp_path):
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    cid = save_card(db_path, {'title': 'T', 'score': 70, 'content_md': '正文'})
    assert update_card(db_path, cid, {'lifecycle_stage': 'ingested'})
    card = get_card(db_path, cid)
    assert card['lifecycle_stage'] == 'ingested'
    assert card['score'] == 70
    assert card['content_md'] == '正文'


def test_update_card_rejects_invalid_lifecycle(tmp_path):
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    cid = save_card(db_path, {'title': 'T'})
    import pytest
    with pytest.raises(ValueError):
        update_card(db_path, cid, {'lifecycle_stage': 'bogus'})


def test_save_card_preserves_lifecycle_on_resave(tmp_path):
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    cid = save_card(db_path, {'title': 'T', 'score': 60})
    update_card(db_path, cid, {'lifecycle_stage': 'applied'})
    # Re-scan re-saves the card without lifecycle_stage; must stay 'applied'.
    save_card(db_path, {'id': cid, 'title': 'T2', 'score': 90})
    assert get_card(db_path, cid)['lifecycle_stage'] == 'applied'


def test_create_derivative_does_not_overwrite_card(tmp_path):
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    cid = save_card(db_path, {'title': '原卡', 'type': 'note', 'content_md': '原文', 'score': 70})
    did = create_derivative(db_path, cid, 'my_version', '我的版本', '转化内容')
    card = get_card(db_path, cid)
    assert card['content_md'] == '原文'      # original unchanged
    assert card['type'] == 'note'
    assert card['lifecycle_stage'] == 'transformed'
    derivs = list_derivatives(db_path, cid)
    assert len(derivs) == 1
    assert derivs[0]['id'] == did
    assert derivs[0]['kind'] == 'my_version'


def test_create_derivative_rejects_invalid_kind(tmp_path):
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    cid = save_card(db_path, {'title': 'T'})
    import pytest
    with pytest.raises(ValueError):
        create_derivative(db_path, cid, 'bogus', 'x', 'y')


def test_agent_rule_lifecycle_publish_archive(tmp_path):
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    cid = save_card(db_path, {'title': 'T', 'score': 90})
    rid = create_agent_rule(db_path, cid, title='规则', category='engineering', content_md='正文')
    assert get_agent_rule(db_path, rid)['status'] == 'draft'
    # Update bumps version (only allowed while still a draft).
    assert update_agent_rule(db_path, rid, {'title': '规则2'})
    assert get_agent_rule(db_path, rid)['version'] == 2
    # Publish (becomes immutable).
    published = publish_agent_rule(db_path, rid)
    assert published['status'] == 'published'
    assert published['published_at'] is not None
    # Archive.
    assert archive_agent_rule(db_path, rid)
    assert get_agent_rule(db_path, rid)['status'] == 'archived'


def test_resave_card_by_id_preserves_derivatives_and_rules(tmp_path):
    """C1 regression: re-saving a card by id must NOT cascade-delete its
    derivatives or crash on agent_rules. INSERT OR REPLACE used to DELETE+INSERT
    under foreign_keys=ON, wiping derivatives (CASCADE) and tripping rules (RESTRICT)."""
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    cid = save_card(db_path, {'id': 'c1', 'title': 'orig', 'score': 60,
                              'lifecycle_stage': 'transformed'})
    original = get_card(db_path, cid)
    original_created_at = original['created_at']
    assert original['lifecycle_stage'] == 'transformed'
    did = create_derivative(db_path, cid, 'my_version', 'mv', 'content')
    rid = create_agent_rule(db_path, cid, title='r', content_md='c')
    # Re-save by id (simulates a re-scan or importer that reuses ids).
    save_card(db_path, {'id': 'c1', 'title': 'RESCAN', 'score': 90})
    # Derivative survived (no cascade), rule survived (no RESTRICT crash).
    assert get_derivative(db_path, did) is not None
    assert get_agent_rule(db_path, rid) is not None
    # Card itself updated.
    card = get_card(db_path, cid)
    assert card['title'] == 'RESCAN'
    assert card['score'] == 90
    # created_at preserved (not reset to now).
    assert card['created_at'] == original_created_at
    # lifecycle_stage preserved (not reset to 'captured').
    assert card['lifecycle_stage'] == 'transformed'


def test_update_agent_rule_rejects_published(tmp_path):
    """I1: published rules are immutable audit records."""
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    cid = save_card(db_path, {'title': 'T'})
    rid = create_agent_rule(db_path, cid, title='t', content_md='c')
    publish_agent_rule(db_path, rid)
    import pytest
    with pytest.raises(ValueError):
        update_agent_rule(db_path, rid, {'title': 'changed'})


def test_create_agent_rule_rejects_cross_card_derivative(tmp_path):
    """I2: a rule's derivative must belong to its own source card."""
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    cid_a = save_card(db_path, {'id': 'a', 'title': 'A'})
    cid_b = save_card(db_path, {'id': 'b', 'title': 'B'})
    did_b = create_derivative(db_path, cid_b, 'my_version', 'mv', 'content')
    import pytest
    with pytest.raises(ValueError):
        create_agent_rule(db_path, cid_a, derivative_id=did_b, title='r', content_md='c')


def test_publish_rejects_blank_rule(tmp_path):
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    cid = save_card(db_path, {'title': 'T'})
    rid = create_agent_rule(db_path, cid, title='', content_md='')
    import pytest
    with pytest.raises(ValueError):
        publish_agent_rule(db_path, rid)


def test_agent_rule_stats(tmp_path):
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    cid = save_card(db_path, {'title': 'T'})
    r1 = create_agent_rule(db_path, cid, title='a', content_md='x')
    r2 = create_agent_rule(db_path, cid, title='b', content_md='y')
    publish_agent_rule(db_path, r1)
    stats = get_agent_rule_stats(db_path)
    assert stats['draft'] == 1
    assert stats['published'] == 1
    assert stats['archived'] == 0


def test_delete_card_blocked_when_has_rules(tmp_path):
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    cid = save_card(db_path, {'title': 'T'})
    create_agent_rule(db_path, cid, title='r', content_md='c')
    import pytest
    with pytest.raises(CardHasRulesError):
        delete_card(db_path, cid)
    # Card still exists.
    assert get_card(db_path, cid) is not None


def test_delete_card_cascades_derivatives(tmp_path):
    db_path = str(tmp_path / 'knowledge.db')
    init_db(db_path)
    cid = save_card(db_path, {'title': 'T'})
    did = create_derivative(db_path, cid, 'my_version', 'd', 'content')
    assert delete_card(db_path, cid)
    assert get_derivative(db_path, did) is None  # cascaded


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
