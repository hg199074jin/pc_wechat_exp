"""Tests for analysis artifact parsing, storage, and rendering."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from engine.services.analysis_artifact import (
    artifact_path,
    load_artifact,
    normalize_artifact,
    parse_artifact_json,
    render_markdown_report,
    save_artifact,
)


def test_parse_artifact_json_strips_fence():
    raw = '```json\n{"summary":"测试","topics":[]}\n```'
    data = parse_artifact_json(raw)
    assert data['summary'] == '测试'
    assert data['topics'] == []


def test_normalize_artifact_fills_defaults():
    data = normalize_artifact(
        {'summary': '摘要'},
        chat_id='123@chatroom',
        group_name='测试群',
        date='2026-06-15',
        stats={'message_count': 3},
    )
    assert data['version'] == 1
    assert data['chat_id'] == '123@chatroom'
    assert data['group_name'] == '测试群'
    assert data['date'] == '2026-06-15'
    assert data['stats']['message_count'] == 3
    assert data['topics'] == []
    assert data['verify']['status'] == 'unverified'


def test_render_markdown_report_from_artifact():
    artifact = normalize_artifact({
        'summary': '今天讨论了 Codex 安装。',
        'topics': [{
            'title': 'Codex 安装',
            'summary': '多人讨论安装路径。',
            'participants': ['张三'],
            'evidence': [{'sender': '张三', 'quote': '这个可以做成SOP'}],
            'knowledge_candidates': [],
        }],
        'followups': [{'title': '整理安装步骤'}],
    }, chat_id='c', group_name='测试群', date='2026-06-15', stats={})
    md = render_markdown_report(artifact)
    assert md.startswith('# 群聊分析报告：测试群')
    assert '## 总体摘要' in md
    assert '### 1. Codex 安装' in md
    assert '> 张三：这个可以做成SOP' in md
    assert '整理安装步骤' in md


def test_save_and_load_artifact(tmp_path):
    artifact = normalize_artifact(
        {'summary': '摘要'},
        chat_id='abc/@chatroom',
        group_name='群',
        date='2026-06-15',
        stats={},
    )
    path = save_artifact(str(tmp_path), artifact)
    assert path == artifact_path(str(tmp_path), 'abc/@chatroom', '2026-06-15')
    loaded = load_artifact(str(tmp_path), 'abc/@chatroom', '2026-06-15')
    assert loaded['summary'] == '摘要'
    assert os.path.isfile(path)
