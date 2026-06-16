"""Tests for group style fingerprint storage."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from engine.services.style_fingerprint import ensure_style, load_style, save_style, style_path, style_to_prompt


def test_ensure_style_creates_json(tmp_path):
    decrypted = tmp_path / 'backup'
    decrypted.mkdir()
    style = ensure_style(str(decrypted), 'abc/@chatroom', '测试群')
    assert style['chat_id'] == 'abc/@chatroom'
    assert os.path.isfile(style_path(str(decrypted), 'abc/@chatroom'))


def test_save_and_load_style(tmp_path):
    decrypted = tmp_path / 'backup'
    decrypted.mkdir()
    style = ensure_style(str(decrypted), 'c', '群')
    style['common_topics'] = ['AI审计']
    save_style(str(decrypted), style)
    loaded = load_style(str(decrypted), 'c')
    assert loaded['common_topics'] == ['AI审计']


def test_style_to_prompt_omits_empty_and_mentions_no_person_profile(tmp_path):
    style = {
        'group_name': '群',
        'common_topics': ['AI审计'],
        'jargon': ['底稿'],
        'taboos': ['不要营销文'],
    }
    prompt = style_to_prompt(style)
    assert 'AI审计' in prompt
    assert '底稿' in prompt
    assert '不要把它当作个人画像' in prompt
