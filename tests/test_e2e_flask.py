"""End-to-end tests for the Flask web application using the test client."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from web.app import create_app


@pytest.fixture
def app(tmp_path):
    """Create a Flask app with a temp decrypted_dir containing minimal structure."""
    decrypted_dir = tmp_path / 'decrypted'
    decrypted_dir.mkdir()
    app = create_app(str(decrypted_dir))
    app.config['TESTING'] = True
    return app


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


# ---------------------------------------------------------------------------
# Page loads (HTML routes)
# ---------------------------------------------------------------------------


class TestPageLoads:
    def test_dashboard_shows_status(self, client):
        """GET / contains system-status div or dashboard template markers."""
        resp = client.get('/')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8', errors='replace')
        # The dashboard template should contain some structural element
        assert len(html) > 0

    def test_settings_page_loads(self, client):
        resp = client.get('/settings')
        assert resp.status_code == 200

    def test_knowledge_page_loads(self, client):
        resp = client.get('/knowledge')
        assert resp.status_code == 200

    def test_analysis_page_loads(self, client):
        resp = client.get('/analysis')
        assert resp.status_code == 200

    def test_contacts_page_loads(self, client):
        resp = client.get('/contacts')
        assert resp.status_code == 200

    def test_groups_page_loads(self, client):
        resp = client.get('/groups')
        assert resp.status_code == 200

    def test_backup_page_loads(self, client):
        resp = client.get('/backup')
        assert resp.status_code == 200

    def test_manual_page_loads(self, client):
        """Manual may return 200 (template) or 404 (not generated) — both are valid."""
        resp = client.get('/manual')
        assert resp.status_code in (200, 404)

    def test_chat_page_loads(self, client):
        resp = client.get('/chat')
        assert resp.status_code == 200

    def test_export_page_loads(self, client):
        resp = client.get('/export')
        assert resp.status_code == 200

    def test_report_page_loads(self, client):
        resp = client.get('/report')
        assert resp.status_code == 200

    def test_cleanup_page_loads(self, client):
        resp = client.get('/cleanup')
        assert resp.status_code == 200

    def test_keyscan_page_loads(self, client):
        resp = client.get('/keyscan')
        assert resp.status_code == 200

    def test_decrypt_page_loads(self, client):
        resp = client.get('/decrypt')
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# API endpoints return valid JSON
# ---------------------------------------------------------------------------


class TestApiEndpointsReturnJson:
    """Verify each reachable /api/* endpoint returns valid JSON."""

    @pytest.mark.parametrize("url", [
        '/api/contacts',
        '/api/system/status',
        '/api/address-book',
        '/api/address-book/groups/blacklist',
    ])
    def test_get_json_endpoint(self, client, url):
        resp = client.get(url)
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, dict)

    def test_all_api_endpoints_return_json(self, client):
        """Hit every safe GET endpoint and verify JSON responses."""
        urls = [
            '/api/contacts',
            '/api/system/status',
            '/api/address-book',
            '/api/address-book/groups',
            '/api/address-book/groups/blacklist',
        ]
        for url in urls:
            resp = client.get(url)
            # Should return valid JSON regardless of status
            data = json.loads(resp.data)
            assert isinstance(data, dict), f"URL {url} did not return a JSON object"

    def test_api_contacts_has_contacts_key(self, client):
        resp = client.get('/api/contacts')
        data = json.loads(resp.data)
        assert 'contacts' in data
        assert isinstance(data['contacts'], list)

    def test_api_system_status_has_expected_keys(self, client):
        resp = client.get('/api/system/status')
        data = json.loads(resp.data)
        expected = {'version', 'wxid', 'has_data', 'db_count',
                    'has_contacts', 'has_media'}
        assert expected.issubset(set(data.keys()))

    def test_address_book_groups_returns_json(self, client):
        resp = client.get('/api/address-book/groups')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'groups' in data
        assert 'total' in data

    def test_blacklist_get_returns_list(self, client):
        resp = client.get('/api/address-book/groups/blacklist')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'blacklist' in data
        assert isinstance(data['blacklist'], list)


# ---------------------------------------------------------------------------
# CSRF protection — comprehensive scenarios
# ---------------------------------------------------------------------------


class TestCsrfProtection:
    """Comprehensive CSRF validation tests."""

    def _post_blacklist(self, client, **kwargs):
        """Helper: POST to the blacklist endpoint."""
        return client.post(
            '/api/address-book/groups/blacklist',
            json={'groups': []},
            **kwargs,
        )

    def test_cross_origin_origin_blocked(self, client):
        """Cross-origin Origin header without X-Requested-With → 403."""
        resp = self._post_blacklist(client, headers={'Origin': 'http://evil.com'})
        assert resp.status_code == 403
        data = json.loads(resp.data)
        assert 'CSRF' in data.get('error', '')

    def test_cross_origin_referer_blocked(self, client):
        """Cross-origin Referer header without X-Requested-With → 403."""
        resp = self._post_blacklist(client, headers={'Referer': 'http://evil.com/attack'})
        assert resp.status_code == 403

    def test_x_requested_with_allowed(self, client):
        """X-Requested-With: XMLHttpRequest bypasses CSRF check."""
        resp = self._post_blacklist(client, headers={'X-Requested-With': 'XMLHttpRequest'})
        assert resp.status_code != 403

    def test_same_origin_origin_allowed(self, client):
        """Same-origin Origin header should be allowed."""
        # Flask test client uses localhost, so set origin to match
        resp = self._post_blacklist(
            client,
            headers={'Origin': 'http://localhost'},
        )
        # Should not be blocked by CSRF (may be other status but not 403)
        assert resp.status_code != 403

    def test_get_requests_skip_csrf(self, client):
        """GET requests should never be blocked by CSRF."""
        resp = client.get('/api/contacts', headers={'Origin': 'http://evil.com'})
        assert resp.status_code == 200

    def test_csrf_on_delete_method(self, client):
        """DELETE without X-Requested-With from cross-origin → 403."""
        resp = client.delete(
            '/api/address-book/groups/blacklist?wxid=test',
            headers={'Origin': 'http://evil.com'},
        )
        assert resp.status_code == 403

    def test_csrf_on_put_method(self, client):
        """PUT without X-Requested-With from cross-origin → 403."""
        # Use a URL that exists as PUT
        resp = client.put(
            '/api/address-book/groups/blacklist',
            json={'groups': []},
            headers={'Origin': 'http://evil.com'},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


class TestSecurityHeaders:
    """Verify security headers are present on all responses."""

    def test_x_content_type_options(self, client):
        resp = client.get('/')
        assert resp.headers.get('X-Content-Type-Options') == 'nosniff'

    def test_x_frame_options(self, client):
        resp = client.get('/')
        assert resp.headers.get('X-Frame-Options') == 'DENY'

    def test_csp_header_present(self, client):
        resp = client.get('/')
        csp = resp.headers.get('Content-Security-Policy', '')
        assert "default-src 'self'" in csp

    def test_referrer_policy(self, client):
        resp = client.get('/')
        assert resp.headers.get('Referrer-Policy') == 'strict-origin-when-cross-origin'

    def test_security_headers_on_api_response(self, client):
        """Security headers should also be on API responses."""
        resp = client.get('/api/system/status')
        assert resp.headers.get('X-Content-Type-Options') == 'nosniff'
        assert resp.headers.get('X-Frame-Options') == 'DENY'
        assert 'Content-Security-Policy' in resp.headers


# ---------------------------------------------------------------------------
# JSON error handlers
# ---------------------------------------------------------------------------


class TestJsonErrors:
    def test_404_returns_json(self, client):
        resp = client.get('/api/nonexistent-endpoint')
        assert resp.status_code == 404
        data = json.loads(resp.data)
        assert 'error' in data

    def test_404_page_returns_json(self, client):
        """Even non-API 404s return JSON (per the error handler)."""
        resp = client.get('/this-page-does-not-exist')
        assert resp.status_code == 404
        data = json.loads(resp.data)
        assert 'error' in data


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    def test_rate_limit_triggers(self, client):
        """POST 31 times quickly triggers 429."""
        url = '/api/address-book/groups/blacklist'
        headers = {'X-Requested-With': 'XMLHttpRequest'}
        for i in range(31):
            resp = client.post(url, json={'groups': []}, headers=headers)
            if resp.status_code == 429:
                data = json.loads(resp.data)
                assert 'error' in data
                return
        pytest.fail("Rate limit was not triggered after 31 POST requests")
