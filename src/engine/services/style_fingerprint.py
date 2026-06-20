"""Group-level style fingerprint storage for AI analysis.

Only group-level topics, jargon, output preferences, and taboos are stored.
Do not store personal profiles or long-term labels for individual members.
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict

from engine.utils import safe_id as _safe_id


def _style_dir(decrypted_dir: str) -> str:
    return os.path.join(os.path.dirname(decrypted_dir), 'ai_analysis', 'styles')


def style_path(decrypted_dir: str, chat_id: str) -> str:
    return os.path.join(_style_dir(decrypted_dir), f'{_safe_id(chat_id)}.json')


def _default_style(chat_id: str, group_name: str = '') -> dict:
    return {
        'version': 1,
        'chat_id': chat_id,
        'group_name': group_name or chat_id,
        'positioning': '',
        'common_topics': [],
        'jargon': [],
        'preferred_output': '',
        'taboos': [],
        'updated_at': 0,
        'sample_count': 0,
    }


def ensure_style(decrypted_dir: str, chat_id: str, group_name: str = '') -> dict:
    """Create a style file when missing and return the style dict."""
    existing = load_style(decrypted_dir, chat_id)
    if existing:
        return existing
    style = _default_style(chat_id, group_name)
    save_style(decrypted_dir, style)
    return style


def load_style(decrypted_dir: str, chat_id: str) -> dict:
    """Load style fingerprint or return {} when missing/invalid."""
    path = style_path(decrypted_dir, chat_id)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {**_default_style(chat_id, data.get('group_name') or chat_id), **data}
    except (OSError, ValueError):
        return {}
    return {}


def save_style(decrypted_dir: str, style: dict) -> None:
    """Save style fingerprint as UTF-8 JSON."""
    chat_id = style.get('chat_id') or ''
    if not chat_id:
        return
    style = {**_default_style(chat_id, style.get('group_name') or chat_id), **style}
    style['updated_at'] = int(time.time())
    path = style_path(decrypted_dir, chat_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(style, f, ensure_ascii=False, indent=2)


def style_to_prompt(style: dict) -> str:
    """Convert group style to a compact prompt section."""
    if not style:
        return ''
    parts = [
        '以下是该群的长期风格指纹，仅用于理解群级黑话、主题和输出偏好。不要把它当作个人画像。',
    ]
    if style.get('positioning'):
        parts.append(f'群定位：{style["positioning"]}')
    if style.get('common_topics'):
        parts.append(f'常见主题：{"、".join(style["common_topics"])}')
    if style.get('jargon'):
        parts.append(f'内部黑话：{"、".join(style["jargon"])}')
    if style.get('preferred_output'):
        parts.append(f'偏好输出：{style["preferred_output"]}')
    if style.get('taboos'):
        parts.append(f'禁忌：{"、".join(style["taboos"])}')
    return '\n'.join(parts)
