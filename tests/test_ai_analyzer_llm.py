"""Tests for LLM call in ai_analyzer."""
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from engine.services.ai_analyzer import call_llm, _get_config


def _mock_response(content='test reply', status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {
        'choices': [{'message': {'content': content}}],
    }
    return resp


def test_call_llm_returns_content():
    with patch('requests.post', return_value=_mock_response('hello world')) as mock_post:
        result = call_llm(
            system='sys', user='usr',
            base_url='https://api.example.com/v1', api_key='sk-test',
            model='gpt-4o', temperature=0.3, max_tokens=4096,
        )
    assert result == 'hello world'
    args, kwargs = mock_post.call_args
    assert 'https://api.example.com/v1/chat/completions' in args[0]
    assert kwargs['headers']['Authorization'] == 'Bearer sk-test'


def test_call_llm_raises_on_http_error():
    with patch('requests.post', return_value=_mock_response(status=401)):
        try:
            call_llm(system='s', user='u', base_url='https://x.com/v1',
                     api_key='k', model='m', temperature=0.3, max_tokens=100)
        except Exception as e:
            assert '401' in str(e) or 'LLM' in str(e)
            return
        raise AssertionError('expected exception')


def test_call_llm_retries_on_timeout():
    import requests as req
    with patch('requests.post', side_effect=[
        req.Timeout('timeout 1'),
        _mock_response('ok after retry'),
    ]) as mock_post:
        result = call_llm(system='s', user='u', base_url='https://x.com/v1',
                          api_key='k', model='m', temperature=0.3, max_tokens=100)
    assert result == 'ok after retry'
    assert mock_post.call_count == 2


def test_get_config_missing_file():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        assert _get_config(os.path.join(tmp, 'missing.json')) == {}
