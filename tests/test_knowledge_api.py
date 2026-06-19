"""Tests for knowledge_api.py — lifecycle, derivatives, agent rules, sync (Task 5)."""
import json
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from web.app import create_app
from engine.services import knowledge_store as store


@pytest.fixture()
def app(tmp_path):
    """A Flask app backed by a temporary decrypted dir + knowledge.db."""
    # knowledge_db_path uses os.path.dirname(decrypted_dir), so put the
    # account dir one level under tmp to keep ai_analysis/ alongside it.
    decrypted = str(tmp_path / 'account')
    os.makedirs(decrypted, exist_ok=True)
    app = create_app(decrypted, wxid=None, db_dir=None)
    app.config['TESTING'] = True
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def _db(app):
    return store.knowledge_db_path(app.config['DECRYPTED_DIR'])


def _seed_card(app, **over):
    base = {'title': '测试卡', 'type': 'methodology', 'score': 90,
            'content_md': '正文', 'status': 'saved'}
    base.update(over)
    return store.save_card(_db(app), base)


def _llm_returning(text):
    """A fake make_llm_call that always returns `text`."""
    def _call(system, user):
        return text
    return _call


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def test_set_lifecycle_ok(client, app):
    cid = _seed_card(app)
    r = client.post(f'/api/knowledge/cards/{cid}/lifecycle',
                    json={'lifecycle_stage': 'ingested'})
    assert r.status_code == 200
    assert r.get_json()['lifecycle_stage'] == 'ingested'
    assert store.get_card(_db(app), cid)['lifecycle_stage'] == 'ingested'


def test_set_lifecycle_invalid_stage(client, app):
    cid = _seed_card(app)
    r = client.post(f'/api/knowledge/cards/{cid}/lifecycle',
                    json={'lifecycle_stage': 'bogus'})
    assert r.status_code == 400


def test_set_lifecycle_unknown_card(client, app):
    r = client.post('/api/knowledge/cards/nope/lifecycle',
                    json={'lifecycle_stage': 'ingested'})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# My-version derivative
# ---------------------------------------------------------------------------

def test_my_version_creates_derivative_without_overwriting(client, app):
    cid = _seed_card(app, content_md='原文不可变')
    with patch('engine.services.knowledge_extractor.make_llm_call',
               return_value=_llm_returning('## 原始观点\n我的版本内容')):
        r = client.post(f'/api/knowledge/cards/{cid}/my-version')
    assert r.status_code == 200
    data = r.get_json()
    assert data['card_unchanged'] is True
    assert data['derivative_id']
    # Original card content untouched.
    assert store.get_card(_db(app), cid)['content_md'] == '原文不可变'
    derivs = store.list_derivatives(_db(app), cid)
    assert len(derivs) == 1
    assert derivs[0]['kind'] == 'my_version'


def test_my_version_missing_llm_config(client, app):
    cid = _seed_card(app)
    with patch('engine.services.knowledge_extractor.make_llm_call',
               side_effect=RuntimeError('LLM 未配置')):
        r = client.post(f'/api/knowledge/cards/{cid}/my-version')
    assert r.status_code == 400


def test_list_derivatives(client, app):
    cid = _seed_card(app)
    store.create_derivative(_db(app), cid, 'sop', 'SOP', '步骤')
    r = client.get(f'/api/knowledge/cards/{cid}/derivatives')
    assert r.status_code == 200
    derivs = r.get_json()['derivatives']
    assert len(derivs) == 1
    assert derivs[0]['kind'] == 'sop'


# ---------------------------------------------------------------------------
# Convert (now creates derivative, does not overwrite card)
# ---------------------------------------------------------------------------

def test_convert_creates_derivative(client, app):
    cid = _seed_card(app, content_md='原文', type='note')
    with patch('engine.services.knowledge_extractor.make_llm_call',
               return_value=_llm_returning('SOP正文')):
        r = client.post(f'/api/knowledge/cards/{cid}/convert',
                        json={'target_type': 'sop'})
    assert r.status_code == 200
    data = r.get_json()
    assert data['card_unchanged'] is True
    assert data['derivative_id']
    # Card untouched.
    card = store.get_card(_db(app), cid)
    assert card['content_md'] == '原文'
    assert card['type'] == 'note'


def test_convert_invalid_target_type(client, app):
    cid = _seed_card(app)
    r = client.post(f'/api/knowledge/cards/{cid}/convert',
                    json={'target_type': 'bogus'})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Agent rules
# ---------------------------------------------------------------------------

def test_draft_rule_ok(client, app):
    cid = _seed_card(app)
    fake = _llm_returning(json.dumps({
        'title': '拆分任务', 'category': 'engineering',
        'content_md': '超过3个文件先拆分。',
    }, ensure_ascii=False))
    with patch('engine.services.knowledge_extractor.make_llm_call', return_value=fake):
        r = client.post(f'/api/knowledge/cards/{cid}/agent-rules/draft')
    assert r.status_code == 200
    rule = r.get_json()['rule']
    assert rule['status'] == 'draft'
    assert rule['title'] == '拆分任务'
    assert rule['source_card_id'] == cid


def test_draft_rule_unknown_card(client, app):
    with patch('engine.services.knowledge_extractor.make_llm_call',
               return_value=_llm_returning('{}')):
        r = client.post('/api/knowledge/cards/nope/agent-rules/draft')
    assert r.status_code == 404


def test_list_card_rules(client, app):
    cid = _seed_card(app)
    store.create_agent_rule(_db(app), cid, title='r', content_md='c')
    r = client.get(f'/api/knowledge/cards/{cid}/agent-rules')
    assert r.status_code == 200
    assert len(r.get_json()['rules']) == 1


def test_update_rule(client, app):
    cid = _seed_card(app)
    rid = store.create_agent_rule(_db(app), cid, title='t', content_md='c')
    r = client.put(f'/api/knowledge/agent-rules/{rid}',
                   json={'title': 't2', 'content_md': 'c2'})
    assert r.status_code == 200
    rule = r.get_json()['rule']
    assert rule['title'] == 't2'
    assert rule['version'] == 2


def test_publish_rule(client, app):
    cid = _seed_card(app)
    rid = store.create_agent_rule(_db(app), cid, title='t', content_md='c')
    r = client.post(f'/api/knowledge/agent-rules/{rid}/publish')
    assert r.status_code == 200
    assert r.get_json()['rule']['status'] == 'published'


def test_publish_blank_rule_rejected(client, app):
    cid = _seed_card(app)
    rid = store.create_agent_rule(_db(app), cid, title='', content_md='')
    r = client.post(f'/api/knowledge/agent-rules/{rid}/publish')
    assert r.status_code == 400


def test_archive_rule(client, app):
    cid = _seed_card(app)
    rid = store.create_agent_rule(_db(app), cid, title='t', content_md='c')
    r = client.post(f'/api/knowledge/agent-rules/{rid}/archive')
    assert r.status_code == 200
    assert store.get_agent_rule(_db(app), rid)['status'] == 'archived'


def test_rule_stats(client, app):
    cid = _seed_card(app)
    r1 = store.create_agent_rule(_db(app), cid, title='a', content_md='x')
    store.create_agent_rule(_db(app), cid, title='b', content_md='y')
    store.publish_agent_rule(_db(app), r1)
    r = client.get('/api/knowledge/agent-rules/stats')
    stats = r.get_json()
    assert stats['draft'] == 1
    assert stats['published'] == 1


# ---------------------------------------------------------------------------
# Delete conflict
# ---------------------------------------------------------------------------

def test_delete_card_with_rules_returns_409(client, app):
    cid = _seed_card(app)
    store.create_agent_rule(_db(app), cid, title='r', content_md='c')
    r = client.delete(f'/api/knowledge/cards/{cid}')
    assert r.status_code == 409
    assert 'rule_count' in r.get_json()
    # Card still present.
    assert store.get_card(_db(app), cid) is not None


# ---------------------------------------------------------------------------
# Sync agent rules
# ---------------------------------------------------------------------------

def test_sync_rules_without_vault_returns_400(client, app):
    cid = _seed_card(app)
    store.create_agent_rule(_db(app), cid, title='r', content_md='c')
    with patch('engine.config_file.get_obsidian_vault_path', return_value=''):
        r = client.post('/api/knowledge/sync-agent-rules')
    assert r.status_code == 400


def test_sync_rules_writes_to_vault(client, app, tmp_path):
    cid = _seed_card(app)
    rid = store.create_agent_rule(_db(app), cid, title='规则', content_md='正文')
    store.publish_agent_rule(_db(app), rid)
    vault = str(tmp_path / 'vault')
    os.makedirs(vault, exist_ok=True)
    with patch('engine.config_file.get_obsidian_vault_path', return_value=vault):
        r = client.post('/api/knowledge/sync-agent-rules')
    assert r.status_code == 200
    data = r.get_json()
    assert data['written'] >= 1
    # File exists in published/.
    import glob
    files = glob.glob(os.path.join(vault, '30_AI可调用', 'published', '*.md'))
    assert len(files) == 1
