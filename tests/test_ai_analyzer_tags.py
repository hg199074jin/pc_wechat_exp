"""Tests for tag tree CRUD in ai_analyzer."""
import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from engine.services import ai_analyzer


def test_save_and_load_tags():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'config.json')
        tags = [
            {'name': '工作', 'children': [
                {'name': '项目A', 'chat_ids': ['wxid_1@chatroom']}
            ]},
            {'name': '技术', 'chat_ids': ['wxid_2@chatroom']},
        ]
        ai_analyzer.save_tags(tags, path)
        loaded = ai_analyzer.load_tags(path)
        assert loaded == tags


def test_load_tags_empty():
    with tempfile.TemporaryDirectory() as tmp:
        assert ai_analyzer.load_tags(os.path.join(tmp, 'missing.json')) == []


def test_save_tags_preserves_llm_config():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'config.json')
        ai_analyzer.save_llm_config({'base_url': 'x', 'api_key': 'k', 'model': 'm',
                                     'temperature': 0.3, 'max_tokens': 100}, path)
        ai_analyzer.save_tags([{'name': '工作', 'chat_ids': []}], path)
        assert ai_analyzer.load_llm_config(path)['model'] == 'm'


def test_collect_tagged_chat_ids():
    tags = [
        {'name': '工作', 'children': [
            {'name': '项目A', 'chat_ids': ['wxid_1', 'wxid_2']},
            {'name': '项目B', 'chat_ids': ['wxid_3']}
        ]},
        {'name': '技术', 'chat_ids': ['wxid_4', 'wxid_5']},
    ]
    tagged = ai_analyzer.collect_tagged_chat_ids(tags)
    assert set(tagged) == {'wxid_1', 'wxid_2', 'wxid_3', 'wxid_4', 'wxid_5'}


def test_compute_untagged_chat_ids():
    tags = [{'name': '工作', 'chat_ids': ['wxid_1']}]
    all_groups = ['wxid_1', 'wxid_2', 'wxid_3']
    untagged = ai_analyzer.compute_untagged_chat_ids(tags, all_groups)
    assert set(untagged) == {'wxid_2', 'wxid_3'}


def test_flatten_tag_tree():
    tags = [
        {'name': '工作', 'children': [
            {'name': '项目A', 'chat_ids': ['wxid_1@chatroom']}
        ]},
        {'name': '技术', 'chat_ids': ['wxid_2@chatroom']},
    ]
    flat = ai_analyzer.flatten_tag_tree(tags)
    assert isinstance(flat, list)
    assert {'wxid_1@chatroom', 'wxid_2@chatroom'} == {c for tag in flat for c in tag['chat_ids']}
