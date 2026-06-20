"""Tests for Flask web API endpoints — routes, CSRF, rate limiting."""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from web.app import create_app


@pytest.fixture
def app(tmp_path):
    """Create a Flask app with a temp decrypted_dir."""
    decrypted_dir = str(tmp_path / 'decrypted')
    os.makedirs(decrypted_dir, exist_ok=True)
    app = create_app(decrypted_dir)
    app.config['TESTING'] = True
    return app


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


# ---------------------------------------------------------------------------
# Page loads
# ---------------------------------------------------------------------------

class TestPageLoads:
    def test_dashboard_loads(self, client):
        """GET / returns 200."""
        resp = client.get('/')
        assert resp.status_code == 200

    def test_chat_page_loads(self, client):
        """GET /chat returns 200."""
        resp = client.get('/chat')
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# JSON API endpoints
# ---------------------------------------------------------------------------

class TestApiJson:
    def test_api_contacts_returns_json(self, client):
        """GET /api/contacts returns JSON with contacts key."""
        resp = client.get('/api/contacts')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'contacts' in data
        assert isinstance(data['contacts'], list)

    def test_api_system_status(self, client):
        """GET /api/system/status returns JSON with expected keys."""
        resp = client.get('/api/system/status')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        expected_keys = {'version', 'wxid', 'has_data', 'db_count',
                         'has_contacts', 'has_media'}
        assert expected_keys.issubset(set(data.keys()))


# ---------------------------------------------------------------------------
# CSRF protection
# ---------------------------------------------------------------------------

class TestCsrf:
    def test_csrf_blocks_cross_origin(self, client):
        """POST with cross-origin Origin header (no X-Requested-With) returns 403."""
        resp = client.post(
            '/api/address-book/groups/blacklist',
            json={'groups': []},
            headers={'Origin': 'http://evil.com'},
        )
        assert resp.status_code == 403
        data = json.loads(resp.data)
        assert 'CSRF' in data.get('error', '')

    def test_csrf_allows_same_origin(self, client):
        """POST with X-Requested-With header is not blocked by CSRF."""
        resp = client.post(
            '/api/address-book/groups/blacklist',
            json={'groups': []},
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        # Should NOT be 403 — the endpoint processes the request normally
        assert resp.status_code != 403

    def test_csrf_blocks_cross_origin_referer(self, client):
        """POST with cross-origin Referer header (no X-Requested-With) returns 403."""
        resp = client.post(
            '/api/address-book/groups/blacklist',
            json={'groups': []},
            headers={'Referer': 'http://evil.com/attack'},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class TestRateLimit:
    def test_rate_limit(self, client):
        """POST 31 times quickly returns 429 (limit is 30/minute)."""
        url = '/api/address-book/groups/blacklist'
        headers = {'X-Requested-With': 'XMLHttpRequest'}

        for i in range(31):
            resp = client.post(url, json={'groups': []}, headers=headers)
            if resp.status_code == 429:
                data = json.loads(resp.data)
                assert 'error' in data
                return  # success — rate limit triggered

        pytest.fail("Rate limit was not triggered after 31 POST requests")


# ---------------------------------------------------------------------------
# Emoji URL validation
# ---------------------------------------------------------------------------

class TestEmoji:
    def test_emoji_rejects_bad_url(self, client):
        """GET /api/emoji with non-allowed domain returns 404."""
        md5 = 'a' * 32
        resp = client.get(f'/api/emoji?md5={md5}&emoji_url=https://evil.com/img.png')
        assert resp.status_code == 404

    def test_emoji_accepts_allowed_domain(self, client):
        """GET /api/emoji with allowed CDN domain does not return 404 for bad md5 path.

        The endpoint redirects to the CDN URL when no local file is found
        and the URL is from an allowed domain.
        """
        md5 = 'a' * 32
        resp = client.get(
            f'/api/emoji?md5={md5}&emoji_url=https://wx.qlogo.cn/emoji.png'
        )
        # Allowed domain: should redirect (302) or serve, not 404
        assert resp.status_code in (200, 302, 301, 307, 308)

    def test_emoji_requires_valid_md5(self, client):
        """GET /api/emoji without valid 32-char md5 returns 404."""
        resp = client.get('/api/emoji?md5=short&emoji_url=https://evil.com')
        assert resp.status_code == 404

    def test_emoji_empty_md5(self, client):
        """GET /api/emoji with empty md5 returns 404."""
        resp = client.get('/api/emoji?md5=&emoji_url=https://evil.com')
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


class TestSecurityHeaders:
    def test_csp_header_present(self, client):
        """Verify Content-Security-Policy header in responses."""
        resp = client.get('/')
        csp = resp.headers.get('Content-Security-Policy', '')
        assert "default-src 'self'" in csp
        assert "script-src" in csp
        assert "frame-ancestors 'none'" in csp

    def test_x_content_type_options(self, client):
        """Verify X-Content-Type-Options header."""
        resp = client.get('/')
        assert resp.headers.get('X-Content-Type-Options') == 'nosniff'

    def test_x_frame_options(self, client):
        """Verify X-Frame-Options header."""
        resp = client.get('/')
        assert resp.headers.get('X-Frame-Options') == 'DENY'


# ---------------------------------------------------------------------------
# System status with mock data
# ---------------------------------------------------------------------------


class TestSystemStatusWithData:
    def test_system_status_with_data(self, app, client, tmp_path):
        """Create mock data and verify status reflects it."""
        decrypted_dir = app.config['DECRYPTED_DIR']

        # Create a message directory with a fake message DB
        msg_dir = os.path.join(decrypted_dir, 'message')
        os.makedirs(msg_dir, exist_ok=True)
        # Create a minimal SQLite file that looks like a message DB
        import sqlite3
        db_path = os.path.join(msg_dir, 'message_0.db')
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE _stub (id INTEGER)")
        conn.close()

        # Create contact directory
        contact_dir = os.path.join(decrypted_dir, 'contact')
        os.makedirs(contact_dir, exist_ok=True)
        contact_db = os.path.join(contact_dir, 'contact.db')
        conn = sqlite3.connect(contact_db)
        conn.execute("CREATE TABLE _stub (id INTEGER)")
        conn.close()

        # Create media directory
        media_dir = os.path.join(decrypted_dir, 'media')
        os.makedirs(media_dir, exist_ok=True)

        resp = client.get('/api/system/status')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data['has_data'] is True
        assert data['db_count'] >= 1
        assert data['has_contacts'] is True
        assert data['has_media'] is True
