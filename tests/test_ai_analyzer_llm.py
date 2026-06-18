"""Tests for LLM call in ai_analyzer."""
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from engine.services.ai_analyzer import call_llm, _get_config, _llm_timeout, auto_classify_groups


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


def test_llm_timeout_defaults_and_bounds():
    assert _llm_timeout({}) == 120
    assert _llm_timeout({'timeout': '3'}) == 15
    assert _llm_timeout({'timeout': '9999'}) == 600
    assert _llm_timeout({'timeout': '180'}) == 180


def test_call_llm_no_proxy_passes_empty_proxies():
    """proxy='none' must send empty proxies so env-var proxies are bypassed."""
    with patch('requests.post', return_value=_mock_response('ok')) as mock_post:
        call_llm(system='s', user='u', base_url='https://x.com/v1',
                 api_key='k', model='m', temperature=0.3, max_tokens=100,
                 proxy='none')
    kwargs = mock_post.call_args.kwargs
    assert kwargs['proxies'] == {'http': None, 'https': None}


def test_call_llm_custom_proxy_is_forwarded():
    with patch('requests.post', return_value=_mock_response('ok')) as mock_post:
        call_llm(system='s', user='u', base_url='https://x.com/v1',
                 api_key='k', model='m', temperature=0.3, max_tokens=100,
                 proxy='http://127.0.0.1:7890')
    kwargs = mock_post.call_args.kwargs
    assert kwargs['proxies'] == {'http': 'http://127.0.0.1:7890',
                                 'https': 'http://127.0.0.1:7890'}


def test_call_llm_auto_proxy_does_not_override():
    """proxy='auto' must leave proxies as None so env vars are honoured."""
    with patch('requests.post', return_value=_mock_response('ok')) as mock_post:
        call_llm(system='s', user='u', base_url='https://x.com/v1',
                 api_key='k', model='m', temperature=0.3, max_tokens=100,
                 proxy='auto')
    assert mock_post.call_args.kwargs['proxies'] is None


def test_call_llm_retries_on_connection_error():
    """ProxyError / ConnectionError are transient and should be retried."""
    import requests as req
    with patch('requests.post', side_effect=[
        req.exceptions.ProxyError('proxy dropped'),
        _mock_response('recovered'),
    ]) as mock_post:
        result = call_llm(system='s', user='u', base_url='https://x.com/v1',
                          api_key='k', model='m', temperature=0.3, max_tokens=100)
    assert result == 'recovered'
    assert mock_post.call_count == 2


def test_call_llm_retries_on_5xx():
    with patch('requests.post', side_effect=[
        _mock_response(status=502),
        _mock_response('ok after 502'),
    ]) as mock_post:
        result = call_llm(system='s', user='u', base_url='https://x.com/v1',
                          api_key='k', model='m', temperature=0.3, max_tokens=100)
    assert result == 'ok after 502'
    assert mock_post.call_count == 2


def test_auto_classify_groups_uses_configured_timeout():
    with patch('engine.services.ai_analyzer.load_llm_config', return_value={
        'base_url': 'https://api.example.com/v1',
        'api_key': 'sk',
        'model': 'm',
        'timeout': 600,
    }), patch('engine.services.ai_analyzer.call_llm', return_value='{"审计": ["群A"]}') as mock_call:
        auto_classify_groups(['群A'], config_path='dummy')
    assert mock_call.call_args.kwargs['timeout'] == 600
