"""Tests for config persistence and schedule management."""
import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from engine.services import ai_analyzer


def test_save_and_load_llm_config():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'config.json')
        cfg = {'base_url': 'https://api.openai.com/v1', 'api_key': 'sk-xxx',
               'model': 'gpt-4o', 'temperature': 0.5, 'max_tokens': 2048}
        ai_analyzer.save_llm_config(cfg, path)
        loaded = ai_analyzer.load_llm_config(path)
        assert loaded == cfg


def test_load_llm_config_missing_file():
    with tempfile.TemporaryDirectory() as tmp:
        assert ai_analyzer.load_llm_config(os.path.join(tmp, 'missing.json')) == {}


def test_load_llm_config_mask_api_key():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'config.json')
        ai_analyzer.save_llm_config(
            {'base_url': 'x', 'api_key': 'sk-1234567890abcdef', 'model': 'm',
             'temperature': 0.3, 'max_tokens': 1000}, path)
        masked = ai_analyzer.load_llm_config_masked(path)
        assert '1234567890' not in masked['api_key']
        assert masked['api_key'].startswith('sk-1')


def test_schedule_crud():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'config.json')
        s = {'name': '每天早8点', 'chat_ids': ['wxid_1', 'wxid_2'],
             'time': '08:00', 'enabled': True}
        sid = ai_analyzer.add_schedule(s, path)
        assert isinstance(sid, str) and len(sid) > 0

        schedules = ai_analyzer.list_schedules(path)
        assert len(schedules) == 1
        assert schedules[0]['name'] == '每天早8点'
        assert schedules[0]['id'] == sid

        ai_analyzer.update_schedule(sid, {'time': '09:30'}, path)
        assert ai_analyzer.list_schedules(path)[0]['time'] == '09:30'

        ai_analyzer.delete_schedule(sid, path)
        assert ai_analyzer.list_schedules(path) == []


def test_mask_api_key_formatting():
    assert ai_analyzer._mask_api_key('sk-1234567890abcdef').startswith('sk-1')
    assert ai_analyzer._mask_api_key('12345') == '12345'
    assert '*' in ai_analyzer._mask_api_key('12345678901234567890')
