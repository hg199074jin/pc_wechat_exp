"""Structured artifact helpers for AI group analysis."""
from __future__ import annotations

import json
import os
import re
from typing import Dict, Optional


def _safe_id(chat_id: str) -> str:
    return ''.join(c if c.isalnum() or c in '_@' else '_' for c in (chat_id or ''))


def artifact_path(storage_dir: str, chat_id: str, date: str) -> str:
    """Return absolute path to the artifact sidecar for (chat_id, date)."""
    return os.path.join(storage_dir, _safe_id(chat_id), f'{date}.artifact.json')


def extract_json_object(raw: str) -> str:
    """Best-effort extract one JSON object from LLM output."""
    text = (raw or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*```$', '', text)
    start = text.find('{')
    end = text.rfind('}')
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def parse_artifact_json(raw: str) -> dict:
    """Parse an LLM artifact JSON response."""
    return json.loads(extract_json_object(raw))


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _normalize_evidence(item: dict) -> dict:
    if not isinstance(item, dict):
        item = {}
    return {
        'msg_id': item.get('msg_id'),
        'time': item.get('time') or '',
        'sender': item.get('sender') or '',
        'quote': item.get('quote') or '',
        'verified': bool(item.get('verified', False)),
    }


def _normalize_candidate(item: dict) -> dict:
    if not isinstance(item, dict):
        item = {}
    try:
        score = int(item.get('score') or 0)
    except (TypeError, ValueError):
        score = 0
    return {
        'title': item.get('title') or '',
        'type': item.get('type') or 'note',
        'score': max(0, min(100, score)),
        'summary': item.get('summary') or '',
        'why_valuable': item.get('why_valuable') or '',
        'content_md': item.get('content_md') or '',
        'tags': _as_list(item.get('tags')),
        'source_msg_ids': _as_list(item.get('source_msg_ids')),
    }


def _normalize_topic(item: dict, idx: int) -> dict:
    if not isinstance(item, dict):
        item = {}
    return {
        'id': item.get('id') or f'topic_{idx}',
        'title': item.get('title') or f'话题 {idx}',
        'summary': item.get('summary') or '',
        'participants': _as_list(item.get('participants')),
        'evidence': [_normalize_evidence(e) for e in _as_list(item.get('evidence'))],
        'knowledge_candidates': [
            _normalize_candidate(c) for c in _as_list(item.get('knowledge_candidates'))
        ],
    }


def normalize_artifact(data: dict, *, chat_id: str, group_name: str,
                       date: str, stats: dict) -> dict:
    """Normalize an artifact dict and fill required fields."""
    if not isinstance(data, dict):
        data = {}
    topics = [
        _normalize_topic(topic, idx)
        for idx, topic in enumerate(_as_list(data.get('topics')), 1)
    ]
    verify = data.get('verify') if isinstance(data.get('verify'), dict) else {}
    return {
        'version': int(data.get('version') or 1),
        'chat_id': data.get('chat_id') or chat_id,
        'group_name': data.get('group_name') or group_name,
        'date': data.get('date') or date,
        'stats': {**(stats or {}), **(data.get('stats') if isinstance(data.get('stats'), dict) else {})},
        'summary': data.get('summary') or '',
        'topics': topics,
        'followups': _as_list(data.get('followups')),
        'verify': {
            'status': verify.get('status') or 'unverified',
            'total_evidence': int(verify.get('total_evidence') or 0),
            'passed': int(verify.get('passed') or 0),
            'failed': int(verify.get('failed') or 0),
            'warnings': _as_list(verify.get('warnings')),
        },
    }


def save_artifact(storage_dir: str, artifact: dict) -> str:
    """Save artifact to disk and return path."""
    path = artifact_path(storage_dir, artifact.get('chat_id', ''), artifact.get('date', ''))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(artifact, f, ensure_ascii=False, indent=2)
    return path


def load_artifact(storage_dir: str, chat_id: str, date: str) -> Optional[dict]:
    """Load an artifact, returning None when missing or invalid."""
    path = artifact_path(storage_dir, chat_id, date)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _quote_line(evidence: dict) -> str:
    sender = evidence.get('sender') or ''
    quote = evidence.get('quote') or ''
    if sender and quote:
        return f'> {sender}：{quote}'
    if quote:
        return f'> {quote}'
    return ''


def render_markdown_report(artifact: dict) -> str:
    """Render artifact to the current Markdown report format."""
    group_name = artifact.get('group_name') or artifact.get('chat_id') or '未知群聊'
    lines = [f'# 群聊分析报告：{group_name}', '', '## 总体摘要']
    summary = (artifact.get('summary') or '').strip()
    lines.append(summary or '暂无明确摘要。')
    lines.extend(['', '## 关键话题'])

    topics = artifact.get('topics') or []
    if not topics:
        lines.append('无明确关键话题。')
    for idx, topic in enumerate(topics, 1):
        lines.extend(['', f'### {idx}. {topic.get("title") or "未命名话题"}'])
        lines.append(f'- 核心内容：{topic.get("summary") or "暂无摘要。"}')
        participants = topic.get('participants') or []
        if participants:
            lines.append(f'- 关键发言人：{"、".join(str(p) for p in participants if p)}')
        evidence = [q for q in (_quote_line(e) for e in topic.get('evidence', [])) if q]
        if evidence:
            lines.append('- 代表原文：')
            lines.extend(evidence[:2])
        candidates = topic.get('knowledge_candidates') or []
        if candidates:
            titles = [c.get('title') for c in candidates if c.get('title')]
            if titles:
                lines.append(f'- 知识候选：{"；".join(titles[:3])}')

    lines.extend(['', '## 待跟进事项'])
    followups = artifact.get('followups') or []
    if not followups:
        lines.append('无明确待跟进事项。')
    else:
        for item in followups:
            if isinstance(item, dict):
                title = item.get('title') or item.get('content') or ''
            else:
                title = str(item)
            if title:
                lines.append(f'- {title}')

    return '\n'.join(lines).strip() + '\n'
