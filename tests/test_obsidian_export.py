"""Tests for obsidian_export.py — frontmatter, filenames, incremental sync, wikilinks."""
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from engine.services.obsidian_export import (
    _safe_filename,
    _card_filename,
    _card_dirname,
    _render_frontmatter,
    _render_card_body,
    sync_cards_to_vault,
    sync_agent_rules_to_vault,
    find_vault_files,
    TYPE_DIR_MAP,
    RULE_EXPORT_MARKER,
)


def _card(**overrides):
    base = {
        'id': 'a1b2c3d4-1234-5678-9abc-deadbeef0000',
        'title': '示例知识卡片',
        'type': 'sop',
        'status': 'saved',
        'score': 92,
        'summary': '这是一段摘要',
        'why_valuable': '因为它示范了结构',
        'content_md': '## 步骤\n1. 第一步\n2. 第二步',
        'tags': ['盘点', 'SOP'],
        'source_chat_ids': ['c@chatroom'],
        'date': '2026-06-15',
        'updated_at': 1718400000,
        'sources': [
            {
                'chat_id': 'c@chatroom',
                'chat_name': '审计交流群',
                'msg_id': 1,
                'sender': '张三',
                'create_time': 1718400000,
                'quote': '原文引用',
                'context': [{'topic': '盘点', 'time': '10:32', 'verified': True}],
            }
        ],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Filename safety
# ---------------------------------------------------------------------------

def test_safe_filename_keeps_chinese_and_replaces_punctuation():
    name = _safe_filename('盘点/SOP：第一篇', 'a1b2c3d4-1234')
    # Chinese preserved, '/' and '：' replaced with '_', id prefix appended.
    assert '盘点' in name
    assert '/' not in name
    assert '：' not in name
    assert name.endswith('__a1b2c3d4.md')


def test_safe_filename_handles_empty_title():
    name = _safe_filename('', 'a1b2c3d4')
    assert name == 'untitled__a1b2c3d4.md'


def test_card_filename_uses_card_title_and_id():
    assert _card_filename(_card()) == '示例知识卡片__a1b2c3d4.md'


# ---------------------------------------------------------------------------
# Directory mapping
# ---------------------------------------------------------------------------

def test_card_dirname_maps_known_types():
    assert _card_dirname({'type': 'sop'}) == 'SOP流程'
    assert _card_dirname({'type': 'audit_case'}) == '审计案例'
    assert _card_dirname({'type': 'unknown'}) == '笔记'  # fallback
    assert _card_dirname({}) == '笔记'  # missing type


def test_type_dir_map_covers_all_card_types():
    expected = {'audit_case', 'sop', 'prompt', 'faq', 'article',
                'tool', 'risk', 'methodology', 'note'}
    assert expected.issubset(set(TYPE_DIR_MAP.keys()))


# ---------------------------------------------------------------------------
# Frontmatter rendering
# ---------------------------------------------------------------------------

def test_render_frontmatter_includes_all_fields_and_marker():
    fm = _render_frontmatter(_card(), ['审计交流群'])
    assert fm.startswith('---')
    assert fm.rstrip().endswith('---')
    assert 'id: a1b2c3d4' in fm
    assert 'type: sop' in fm
    assert 'score: 92' in fm
    assert 'status: saved' in fm
    assert 'date: 2026-06-15' in fm
    assert 'updated_at: 1718400000' in fm
    assert 'wechat_exp: "obsidian-export"' in fm
    # tags rendered as YAML flow sequence
    assert '盘点' in fm and 'SOP' in fm
    # source chats as wikilinks
    assert '[[审计交流群]]' in fm


def test_render_frontmatter_quotes_special_chars():
    card = _card(title='带"引号"和:冒号')
    fm = _render_frontmatter(card, [])
    # Should not crash and must remain valid-looking YAML (quoted when needed).
    assert fm.startswith('---')


# ---------------------------------------------------------------------------
# Body rendering + wikilinks
# ---------------------------------------------------------------------------

def test_render_body_has_title_summary_and_wikilink_source():
    body = _render_card_body(_card(), ['审计交流群'])
    assert body.startswith('# 示例知识卡片')
    assert '> 这是一段摘要' in body
    assert '## 为什么有价值' in body
    assert '第一步' in body  # content_md present
    assert '## 来源' in body
    assert '[[审计交流群]]' in body
    assert '张三' in body
    assert '✓已验证' in body


def test_render_body_shows_unverified_badge():
    card = _card()
    card['sources'][0]['context'] = [{'verified': False}]
    body = _render_card_body(card, ['审计交流群'])
    assert '✗未验证' in body


def test_resolve_source_groups_falls_back_to_resolve_wxid():
    card = _card()
    card['sources'] = [{'chat_id': 'c@chatroom', 'chat_name': ''}]
    with patch('engine.services.name_resolver.resolve_wxid', return_value='解析出的群名'):
        from engine.services.obsidian_export import _resolve_source_groups
        names = _resolve_source_groups(card, decrypted_dir='some/dir')
    assert names == ['解析出的群名']


def test_resolve_source_groups_dedupes():
    card = _card()
    card['sources'] = [
        {'chat_name': '审计交流群'},
        {'chat_name': '审计交流群'},
        {'chat_name': '其他群'},
    ]
    from engine.services.obsidian_export import _resolve_source_groups
    assert _resolve_source_groups(card, decrypted_dir=None) == ['审计交流群', '其他群']


# ---------------------------------------------------------------------------
# Incremental sync
# ---------------------------------------------------------------------------

def test_sync_writes_card_into_type_subdir():
    with tempfile.TemporaryDirectory() as vault:
        result = sync_cards_to_vault([_card()], vault)
        assert result['written'] == 1
        assert result['skipped'] == 0
        path = os.path.join(vault, 'SOP流程', '示例知识卡片__a1b2c3d4.md')
        assert os.path.isfile(path)
        with open(path, encoding='utf-8') as f:
            content = f.read()
        assert content.startswith('---')
        assert '# 示例知识卡片' in content


def test_sync_is_incremental_skips_unchanged_card():
    card = _card()
    with tempfile.TemporaryDirectory() as vault:
        first = sync_cards_to_vault([card], vault)
        assert first['written'] == 1
        # Second sync with identical updated_at -> skipped.
        second = sync_cards_to_vault([card], vault)
        assert second['written'] == 0
        assert second['skipped'] == 1


def test_sync_rewrites_when_updated_at_changes():
    with tempfile.TemporaryDirectory() as vault:
        sync_cards_to_vault([_card(updated_at=1718400000)], vault)
        updated = _card(updated_at=1718400500)
        result = sync_cards_to_vault([updated], vault)
        assert result['written'] == 1
        assert result['skipped'] == 0
        # The file's stored updated_at should reflect the new value.
        path = os.path.join(vault, 'SOP流程', '示例知识卡片__a1b2c3d4.md')
        with open(path, encoding='utf-8') as f:
            assert 'updated_at: 1718400500' in f.read()


def test_sync_never_deletes_files_when_card_removed():
    with tempfile.TemporaryDirectory() as vault:
        sync_cards_to_vault([_card()], vault)
        path = os.path.join(vault, 'SOP流程', '示例知识卡片__a1b2c3d4.md')
        assert os.path.isfile(path)
        # Sync with an empty list: file must remain (only-add policy).
        sync_cards_to_vault([], vault)
        assert os.path.isfile(path)


def test_sync_returns_error_when_no_vault_path():
    result = sync_cards_to_vault([_card()], '')
    assert result['written'] == 0
    assert result['errors']


def test_sync_groups_by_type_into_separate_dirs():
    cards = [_card(type='sop'), _card(type='faq', id='f1e2d3c4-aaaa')]
    with tempfile.TemporaryDirectory() as vault:
        sync_cards_to_vault(cards, vault)
        assert os.path.isfile(os.path.join(vault, 'SOP流程', '示例知识卡片__a1b2c3d4.md'))
        assert os.path.isfile(os.path.join(vault, 'FAQ', '示例知识卡片__f1e2d3c4.md'))


def test_find_vault_files_recognises_exported_notes():
    with tempfile.TemporaryDirectory() as vault:
        sync_cards_to_vault([_card()], vault)
        found = find_vault_files(vault)
        assert len(found) == 1
        assert found[0]['updated_at'] == 1718400000


# ---------------------------------------------------------------------------
# Agent-rule sync (Task 4)
# ---------------------------------------------------------------------------

def _rule(**overrides):
    base = {
        'id': 'r1a2b3c4-aaaa-bbbb-cccc-dddddddddddd',
        'source_card_id': 'card-1',
        'derivative_id': None,
        'title': '拆分大任务为子任务',
        'category': 'engineering',
        'content_md': '当一个任务涉及超过3个文件时，先拆成子任务再执行。',
        'status': 'published',
        'target_scope': 'shared',
        'target_project': '',
        'target_path': '',
        'version': 1,
        'created_at': 1718400000,
        'updated_at': 1718400000,
        'published_at': 1718400000,
    }
    base.update(overrides)
    return base


def test_sync_published_rule_writes_to_published_dir():
    with tempfile.TemporaryDirectory() as vault:
        result = sync_agent_rules_to_vault([_rule()], vault)
        assert result['written'] == 1
        path = os.path.join(vault, '30_AI可调用', 'published', '拆分大任务为子任务__r1a2b3c4.md')
        assert os.path.isfile(path)
        with open(path, encoding='utf-8') as f:
            content = f.read()
        assert content.startswith('---')
        assert f'wechat_exp: "{RULE_EXPORT_MARKER}"' in content
        assert 'status: published' in content
        assert 'source_card_id: card-1' in content
        assert '当一个任务涉及超过3个文件' in content


def test_sync_draft_rule_writes_to_drafts_dir():
    with tempfile.TemporaryDirectory() as vault:
        sync_agent_rules_to_vault([_rule(status='draft', published_at=None)], vault)
        path = os.path.join(vault, '30_AI可调用', 'drafts', '拆分大任务为子任务__r1a2b3c4.md')
        assert os.path.isfile(path)


def test_sync_generates_index():
    with tempfile.TemporaryDirectory() as vault:
        sync_agent_rules_to_vault([_rule(), _rule(id='e2f3g4h5-aaaa', title='另一条规则')], vault)
        idx = os.path.join(vault, '30_AI可调用', 'INDEX.md')
        assert os.path.isfile(idx)
        with open(idx, encoding='utf-8') as f:
            idx_content = f.read()
        assert '拆分大任务为子任务' in idx_content
        assert '另一条规则' in idx_content


def test_sync_skips_unchanged_rule():
    rule = _rule()
    with tempfile.TemporaryDirectory() as vault:
        first = sync_agent_rules_to_vault([rule], vault)
        assert first['written'] == 1
        second = sync_agent_rules_to_vault([rule], vault)
        assert second['written'] == 0
        assert second['skipped'] == 1


def test_sync_rewrites_when_updated_at_changes():
    with tempfile.TemporaryDirectory() as vault:
        sync_agent_rules_to_vault([_rule(updated_at=1718400000)], vault)
        result = sync_agent_rules_to_vault([_rule(updated_at=1718400500, version=2)], vault)
        assert result['written'] == 1
        path = os.path.join(vault, '30_AI可调用', 'published', '拆分大任务为子任务__r1a2b3c4.md')
        with open(path, encoding='utf-8') as f:
            assert 'updated_at: 1718400500' in f.read()


def test_sync_no_vault_returns_error():
    result = sync_agent_rules_to_vault([_rule()], '')
    assert result['written'] == 0
    assert result['errors']


def test_sync_empty_list_does_not_delete_existing():
    with tempfile.TemporaryDirectory() as vault:
        sync_agent_rules_to_vault([_rule()], vault)
        path = os.path.join(vault, '30_AI可调用', 'published', '拆分大任务为子任务__r1a2b3c4.md')
        assert os.path.isfile(path)
        # Empty sync must not remove the previously written file.
        sync_agent_rules_to_vault([], vault)
        assert os.path.isfile(path)
