"""Export knowledge cards to an Obsidian-friendly vault (one card per file).

Each card becomes a Markdown note with YAML frontmatter, grouped by card type
into subdirectories. Source chat names become Obsidian ``[[wikilinks]]`` so the
vault can use Obsidian's graph and backlinks. Syncing is incremental: a card
whose ``updated_at`` matches the existing file is skipped, and files are never
deleted (the user's manual edits in Obsidian are preserved).
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

from engine.utils import safe_id as _safe_id


# Card type -> Chinese subdirectory name (aligned with the front-end labels).
TYPE_DIR_MAP: Dict[str, str] = {
    'audit_case': '审计案例',
    'sop': 'SOP流程',
    'prompt': 'Prompt',
    'faq': 'FAQ',
    'article': '文章',
    'tool': '工具',
    'risk': '风险',
    'methodology': '方法论',
    'note': '笔记',
}

# Marker written into every generated file's frontmatter. Used to recognise
# files produced by this exporter (e.g. for future cleanup scans).
EXPORT_MARKER = 'obsidian-export'

# Maximum length of the title-derived filename stem, to avoid OS path limits
# once the id suffix and extension are appended.
_MAX_TITLE_STEM = 60


def _safe_filename(title: str, card_id: str) -> str:
    """Build a collision-resistant Markdown filename for one card.

    Format: ``<safe title>__<id prefix>.md``. The id prefix guarantees
    uniqueness even when two cards share a title.
    """
    stem = _safe_id(title or '').strip('_')
    if not stem:
        stem = 'untitled'
    # Collapse runs of underscores produced by stripped punctuation.
    stem = re.sub(r'_+', '_', stem)
    if len(stem) > _MAX_TITLE_STEM:
        stem = stem[:_MAX_TITLE_STEM].rstrip('_')
    id_prefix = _safe_id(card_id or '')[:8]
    suffix = f'__{id_prefix}' if id_prefix else ''
    return f'{stem}{suffix}.md'


def _card_filename(card: dict) -> str:
    return _safe_filename(card.get('title') or '', card.get('id') or '')


def _card_dirname(card: dict) -> str:
    """Return the subdirectory name for a card based on its type."""
    card_type = card.get('type') or 'note'
    return TYPE_DIR_MAP.get(card_type, TYPE_DIR_MAP['note'])


def _resolve_source_groups(card: dict, decrypted_dir: Optional[str]) -> List[str]:
    """Return de-duplicated, displayable group names for a card's sources.

    Used both for frontmatter ``source_chats`` wikilinks and the body links.
    Falls back to ``resolve_wxid`` when a source lacks ``chat_name`` but has a
    ``chat_id``. Unknown/empty names are dropped.
    """
    names: List[str] = []
    seen = set()

    for src in (card.get('sources') or []):
        name = (src.get('chat_name') or '').strip()
        if not name and decrypted_dir and (src.get('chat_id') or '').strip():
            try:
                from engine.services.name_resolver import resolve_wxid
                name = (resolve_wxid(decrypted_dir, src.get('chat_id')) or '').strip()
            except Exception:
                name = ''
        if name and name not in seen:
            seen.add(name)
            names.append(name)

    # Also include card-level source_chat_ids that did not appear in sources.
    return names


def _yaml_escape(value: str) -> str:
    """Minimal YAML scalar escaping for safe inline values."""
    text = str(value or '')
    if text == '':
        return '""'
    # Quote if it contains characters that would confuse a YAML scalar reader.
    if re.search(r'[:#\[\]{}&,!*|>\'"%@`]', text):
        return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return text


def _render_frontmatter(card: dict, group_names: List[str]) -> str:
    """Render the YAML frontmatter block for a card."""
    tags = card.get('tags') or []
    tags_yaml = '[' + ', '.join(_yaml_escape(t) for t in tags) + ']'
    source_chats = '[' + ', '.join(f'[[{n}]]' for n in group_names) + ']'
    lines = [
        '---',
        f'id: {_yaml_escape(card.get("id") or "")}',
        f'type: {_yaml_escape(card.get("type") or "note")}',
        f'score: {int(card.get("score") or 0)}',
        f'status: {_yaml_escape(card.get("status") or "inbox")}',
        f'date: {_yaml_escape(card.get("date") or "")}',
        f'tags: {tags_yaml}',
        f'source_chats: {source_chats}',
        f'knowledge_space: {_yaml_escape(card.get("knowledge_space_name") or "")}',
        f'updated_at: {int(card.get("updated_at") or 0)}',
        f'wechat_exp: "{EXPORT_MARKER}"',
        '---',
    ]
    return '\n'.join(lines)


def _ts_to_str(ts) -> str:
    """Format a unix timestamp as ``YYYY-MM-DD HH:MM`` (empty on failure)."""
    try:
        from datetime import datetime
        return datetime.fromtimestamp(int(ts)).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return ''


def _verified_badge(src: dict) -> str:
    """Return a small verification badge for a source line."""
    context = src.get('context') or []
    verified = None
    if isinstance(context, list):
        for item in context:
            if isinstance(item, dict) and 'verified' in item:
                verified = item.get('verified')
                break
    if verified is True:
        return ' ✓已验证'
    if verified is False:
        return ' ✗未验证'
    return ''


def _render_card_body(card: dict, group_names: List[str]) -> str:
    """Render the Markdown body (everything after frontmatter) for a card."""
    title = (card.get('title') or '未命名').strip()
    parts: List[str] = [f'# {title}', '']

    summary = (card.get('summary') or '').strip()
    if summary:
        parts.append(f'> {summary}')
        parts.append('')

    why = (card.get('why_valuable') or '').strip()
    if why:
        parts.append('## 为什么有价值')
        parts.append(why)
        parts.append('')

    content = (card.get('content_md') or '').strip()
    if content:
        parts.append(content)
        parts.append('')

    sources = card.get('sources') or []
    if sources:
        parts.append('## 来源')
        for src in sources:
            name = (src.get('chat_name') or '').strip()
            if name:
                group_link = f'[[{name}]]'
            else:
                group_link = (src.get('chat_name') or '未知群聊') or '未知群聊'
            sender = (src.get('sender') or '').strip()
            ts = _ts_to_str(src.get('create_time'))
            head = group_link
            if sender:
                head += f' {sender}'
            if ts:
                head += f' ({ts})'
            quote = (src.get('quote') or '').strip()
            badge = _verified_badge(src)
            if quote:
                parts.append(f'- {head}{badge}：{quote}')
            else:
                parts.append(f'- {head}{badge}')
        parts.append('')

    return '\n'.join(parts).rstrip() + '\n'


def _read_existing_updated_at(path: str) -> Optional[int]:
    """Return the ``updated_at`` stored in an existing note, or None.

    Only inspects the frontmatter (the leading ``--- ... ---`` block) so this
    stays cheap even for large notes.
    """
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            # Only read the frontmatter; stop at the closing ``---``.
            head_lines = []
            for _ in range(50):
                line = f.readline()
                if not line:
                    break
                head_lines.append(line)
                if len(head_lines) > 1 and line.strip() == '---':
                    break
    except OSError:
        return None
    for line in head_lines:
        m = re.match(r'^updated_at:\s*(\d+)', line.strip())
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
    return None


def sync_cards_to_vault(
    cards: List[dict],
    vault_path: str,
    decrypted_dir: Optional[str] = None,
) -> dict:
    """Incrementally sync cards into an Obsidian vault.

    Each card is written as ``<vault>/<type dir>/<title>__<id>.md``. A card is
    skipped when the existing file's frontmatter ``updated_at`` equals the
    card's ``updated_at``. Files are never deleted here.

    Returns ``{'written': N, 'skipped': N, 'errors': [...]}``.
    """
    written = 0
    skipped = 0
    errors: List[str] = []

    if not vault_path:
        return {'written': 0, 'skipped': 0, 'errors': ['未配置 Obsidian vault 路径']}
    try:
        os.makedirs(vault_path, exist_ok=True)
    except OSError as e:
        return {'written': 0, 'skipped': 0, 'errors': [f'无法创建 vault 目录: {e}']}

    for card in cards or []:
        try:
            group_names = _resolve_source_groups(card, decrypted_dir)
            dirname = _card_dirname(card)
            filename = _card_filename(card)
            dir_path = os.path.join(vault_path, dirname)
            file_path = os.path.join(dir_path, filename)

            # Incremental check: skip when unchanged.
            existing = _read_existing_updated_at(file_path)
            if existing is not None and existing == int(card.get('updated_at') or 0):
                skipped += 1
                continue

            os.makedirs(dir_path, exist_ok=True)
            content = (_render_frontmatter(card, group_names) + '\n\n'
                       + _render_card_body(card, group_names))
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            written += 1
        except Exception as e:  # pragma: no cover - defensive, surfaced to UI
            errors.append(f'{card.get("id") or "?"}: {e}')

    return {'written': written, 'skipped': skipped, 'errors': errors}


def sync_cards_to_spaces(cards: List[dict], spaces: List[dict], decrypted_dir: Optional[str] = None) -> dict:
    """Sync every card only to its assigned knowledge-space vault."""
    space_map = {s.get('id'): s for s in (spaces or []) if s.get('id') and s.get('vault_path')}
    grouped, unassigned = {}, 0
    for card in cards or []:
        space = space_map.get(card.get('knowledge_space_id') or '')
        if not space:
            unassigned += 1
            continue
        item = dict(card)
        item['knowledge_space_name'] = space.get('name') or ''
        grouped.setdefault(space['id'], []).append(item)
    written = skipped = 0
    errors, results = [], []
    for sid, group in grouped.items():
        space = space_map[sid]
        result = sync_cards_to_vault(group, space['vault_path'], decrypted_dir=decrypted_dir)
        written += result.get('written', 0); skipped += result.get('skipped', 0)
        errors.extend(result.get('errors') or [])
        results.append({'space_id': sid, 'space_name': space.get('name') or '', 'vault_path': space['vault_path'], 'written': result.get('written', 0), 'skipped': result.get('skipped', 0), 'errors': result.get('errors') or []})
    return {'written': written, 'skipped': skipped, 'unassigned': unassigned, 'spaces': results, 'errors': errors}


def find_vault_files(vault_path: str) -> List[dict]:
    """Scan a vault for notes produced by this exporter.

    Returns a list of ``{'path', 'id', 'type', 'updated_at'}`` dicts. Currently
    unused by the sync (we never delete), but exposed for future cleanup tools.
    """
    results: List[dict] = []
    if not vault_path or not os.path.isdir(vault_path):
        return results
    for root, _dirs, files in os.walk(vault_path):
        for name in files:
            if not name.endswith('.md'):
                continue
            full = os.path.join(root, name)
            updated = _read_existing_updated_at(full)
            if updated is None:
                continue  # not one of ours (no updated_at frontmatter)
            results.append({'path': full, 'updated_at': updated})
    return results


# ---------------------------------------------------------------------------
# Agent-rule sync (Task 4)
# ---------------------------------------------------------------------------

RULE_EXPORT_MARKER = 'agent-rule-export'


def _atomic_write_text(path: str, content: str) -> None:
    """Write text atomically (.tmp + os.replace), creating parent dirs."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(content)
    os.replace(tmp, path)


def _rule_filename(rule: dict) -> str:
    return _safe_filename(rule.get('title') or '', rule.get('id') or '')


def _rule_subdir(rule: dict) -> str:
    """Draft rules go to drafts/, published to published/."""
    return 'published' if rule.get('status') == 'published' else 'drafts'


def _render_rule_frontmatter(rule: dict) -> str:
    """YAML frontmatter for an agent-rule file."""
    published_at = rule.get('published_at')
    lines = [
        '---',
        f'id: {_yaml_escape(rule.get("id") or "")}',
        f'category: {_yaml_escape(rule.get("category") or "general")}',
        f'status: {_yaml_escape(rule.get("status") or "draft")}',
        f'source_card_id: {_yaml_escape(rule.get("source_card_id") or "")}',
        f'target_scope: {_yaml_escape(rule.get("target_scope") or "shared")}',
        f'version: {int(rule.get("version") or 1)}',
        f'updated_at: {int(rule.get("updated_at") or 0)}',
        f'published_at: {int(published_at) if published_at else 0}',
        f'wechat_exp: "{RULE_EXPORT_MARKER}"',
        '---',
    ]
    return '\n'.join(lines)


def _render_rule_body(rule: dict) -> str:
    """Markdown body for an agent-rule file."""
    title = (rule.get('title') or '未命名规则').strip()
    parts = [f'# {title}', '']
    meta = []
    if rule.get('category'):
        meta.append(f'**分类:** {rule["category"]}')
    if rule.get('version'):
        meta.append(f'**版本:** v{rule["version"]}')
    if rule.get('source_card_id'):
        meta.append(f'**来源卡片:** `{rule["source_card_id"]}`')
    if meta:
        parts.append(' | '.join(meta))
        parts.append('')
    parts.append(rule.get('content_md') or '（规则正文为空）')
    parts.append('')
    return '\n'.join(parts).rstrip() + '\n'


def _render_rules_index(rules: list) -> str:
    """Generate INDEX.md listing all synced rules."""
    lines = ['# AI 可调用规则索引', '',
             '> 由 WeChat EXP 知识沉淀雷达自动生成。本文件是导航文档，'
             '非真源——规则的真源在 knowledge.db。', '',
             '在其他项目的 AGENTS.md 里引用本目录，或复制相关段落。', '']
    published = [r for r in rules if r.get('status') == 'published']
    drafts = [r for r in rules if r.get('status') != 'published']
    if published:
        lines.append('## 已发布规则')
        for r in published:
            lines.append(f"- [{r.get('title') or '未命名'}](published/{_rule_filename(r)}) — {r.get('category', 'general')}")
        lines.append('')
    if drafts:
        lines.append('## 草案（未发布，仅供参考）')
        for r in drafts:
            lines.append(f"- [{r.get('title') or '未命名'}](drafts/{_rule_filename(r)}) — {r.get('category', 'general')}")
        lines.append('')
    return '\n'.join(lines)


def sync_agent_rules_to_vault(rules: list, vault_path: str) -> dict:
    """Incrementally sync agent rules into vault/30_AI可调用/.

    Published rules -> published/, others -> drafts/. Uses updated_at to skip
    unchanged files. Regenerates INDEX.md each run. Never deletes files.
    Returns {'written': N, 'skipped': N, 'errors': [...]}.
    """
    written = 0
    skipped = 0
    errors: List[str] = []

    if not vault_path:
        return {'written': 0, 'skipped': 0, 'errors': ['未配置 Obsidian vault 路径']}
    base_dir = os.path.join(vault_path, '30_AI可调用')
    try:
        os.makedirs(base_dir, exist_ok=True)
    except OSError as e:
        return {'written': 0, 'skipped': 0, 'errors': [f'无法创建 vault 目录: {e}']}

    for rule in rules or []:
        try:
            subdir = _rule_subdir(rule)
            filename = _rule_filename(rule)
            dir_path = os.path.join(base_dir, subdir)
            file_path = os.path.join(dir_path, filename)

            existing = _read_existing_updated_at(file_path)
            if existing is not None and existing == int(rule.get('updated_at') or 0):
                skipped += 1
                continue

            content = (_render_rule_frontmatter(rule) + '\n\n'
                       + _render_rule_body(rule))
            _atomic_write_text(file_path, content)
            written += 1
        except Exception as e:  # pragma: no cover - defensive
            errors.append(f'{rule.get("id") or "?"}: {e}')

    # Always regenerate the navigation index.
    try:
        _atomic_write_text(os.path.join(base_dir, 'INDEX.md'), _render_rules_index(rules or []))
    except Exception as e:  # pragma: no cover - defensive
        errors.append(f'INDEX.md: {e}')

    return {'written': written, 'skipped': skipped, 'errors': errors}
