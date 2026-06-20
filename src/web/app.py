"""Flask application factory for the chat viewer."""
import logging
import os
import sys
import threading
import webbrowser
from flask import Flask, render_template, jsonify
from engine.version import VERSION as __version__

logger = logging.getLogger(__name__)

# Configure logging on first import if not already configured
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        datefmt='%H:%M:%S',
    )


def _resolve_path(relative_path: str) -> str:
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, 'src', 'web', relative_path)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, relative_path)


def create_app(decrypted_dir: str, wxid: str = None, db_dir: str = None) -> Flask:
    app = Flask(__name__,
        template_folder=_resolve_path('templates'),
        static_folder=_resolve_path('static'),
    )
    from engine.services.media import _detect_wxid
    app.config['DECRYPTED_DIR'] = decrypted_dir
    app.config['WXID'] = wxid or _detect_wxid(decrypted_dir)
    app.config['DB_DIR'] = db_dir
    app.config['APP_VERSION'] = __version__
    app.json.ensure_ascii = False

    # CSRF protection: require X-Requested-With on state-changing requests
    # to prevent drive-by attacks from malicious websites targeting localhost.
    @app.before_request
    def _check_csrf():
        from flask import request, jsonify
        from urllib.parse import urlparse
        if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            # Allow same-origin requests with X-Requested-With header
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return None
            # Validate Origin/Referer by parsing netloc (not substring match)
            host = request.host
            origin = request.headers.get('Origin', '')
            referer = request.headers.get('Referer', '')
            if origin:
                if urlparse(origin).netloc != host:
                    return jsonify({'error': 'CSRF validation failed'}), 403
            elif referer:
                if urlparse(referer).netloc != host:
                    return jsonify({'error': 'CSRF validation failed'}), 403
        return None

    # Rate limiting: max 30 state-changing requests per minute per IP
    _rate_limit_store = {}  # {ip: [timestamps]}
    _RATE_LIMIT = 30
    _RATE_WINDOW = 60  # seconds

    @app.before_request
    def _rate_limit():
        from flask import request, jsonify
        import time
        if request.method not in ('POST', 'PUT', 'DELETE', 'PATCH'):
            return None
        ip = request.remote_addr or '127.0.0.1'
        now = time.time()
        # Clean old entries
        if ip in _rate_limit_store:
            _rate_limit_store[ip] = [t for t in _rate_limit_store[ip] if now - t < _RATE_WINDOW]
        else:
            _rate_limit_store[ip] = []
        if len(_rate_limit_store[ip]) >= _RATE_LIMIT:
            return jsonify({'error': '请求过于频繁，请稍后再试'}), 429
        _rate_limit_store[ip].append(now)
        return None

    # Security headers
    @app.after_request
    def _security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        # CSP: allow inline styles (needed for dynamic HTML), CDN for marked.js
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: http: https:; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "object-src 'none'; "
            "frame-ancestors 'none'"
        )
        return response

    # Inject version into all template contexts
    @app.context_processor
    def _inject_version():
        return {'app_version': __version__}

    # Existing API
    try:
        from .routes.api import api_bp
        app.register_blueprint(api_bp, url_prefix='/api')
    except ImportError as e:
        print(f"[WARN] 无法加载 API 蓝图 (routes.api): {e}")

    # Existing reports
    try:
        from .reports import reports_bp
        app.register_blueprint(reports_bp)
    except ImportError as e:
        print(f"[WARN] 无法加载报告蓝图 (reports): {e}")

    # New: Backup API (SSE endpoints)
    try:
        from .routes.backup_api import backup_bp
        app.register_blueprint(backup_bp)
    except ImportError as e:
        print(f"[WARN] 无法加载备份蓝图 (routes.backup_api): {e}")

    # New: Export API (SSE endpoints)
    try:
        from .routes.export_api import export_bp
        app.register_blueprint(export_bp)
    except ImportError as e:
        print(f"[WARN] 无法加载导出蓝图 (routes.export_api): {e}")

    # Avatar API
    try:
        from .routes.avatar_api import avatar_bp
        app.register_blueprint(avatar_bp)
    except ImportError as e:
        print(f"[WARN] 无法加载头像蓝图 (routes.avatar_api): {e}")

    # Cleanup API
    try:
        from .routes.cleanup_api import cleanup_bp
        app.register_blueprint(cleanup_bp, url_prefix='/api')
    except ImportError as e:
        print(f"[WARN] 无法加载清理蓝图 (routes.cleanup_api): {e}")

    # AI Analysis API
    try:
        from .routes.analysis_api import analysis_bp
        app.register_blueprint(analysis_bp)
    except ImportError as e:
        print(f"[WARN] 无法加载分析蓝图 (routes.analysis_api): {e}")

    # Knowledge Radar API
    try:
        from .routes.knowledge_api import knowledge_bp
        app.register_blueprint(knowledge_bp)
    except ImportError as e:
        print(f"[WARN] 无法加载知识沉淀蓝图 (routes.knowledge_api): {e}")

    # JSON error handlers — prevent Flask HTML pages for API routes
    @app.errorhandler(404)
    def _json_404(e):
        return jsonify({'error': 'not found'}), 404

    @app.errorhandler(500)
    def _json_500(e):
        return jsonify({'error': 'internal server error'}), 500

    # Dashboard (new home page)
    @app.route('/')
    def dashboard():
        return render_template('dashboard.html')

    # Chat viewer
    @app.route('/chat')
    def chat():
        return render_template('index.html')

    # Wizard pages
    @app.route('/backup')
    def backup_page():
        return render_template('backup.html')

    @app.route('/keyscan')
    def keyscan_page():
        return render_template('keyscan.html')

    @app.route('/decrypt')
    def decrypt_page():
        return render_template('decrypt.html')

    @app.route('/settings')
    def settings_page():
        return render_template('settings.html')

    # Export pages
    @app.route('/export')
    def export_page():
        return render_template('export.html')

    @app.route('/report')
    def report_page():
        return render_template('report.html')

    @app.route('/employee')
    def employee_page():
        return render_template('employee.html')

    @app.route('/contacts')
    def contacts_page():
        return render_template('contacts.html')

    @app.route('/cleanup')
    def cleanup_page():
        return render_template('cleanup.html')

    @app.route('/manual')
    def manual_page():
        try:
            return render_template('manual.html')
        except Exception:
            return "<html><body style='background:#0d1117;color:#c9d1d9;padding:40px;font-family:sans-serif;'><p>手册尚未生成。请运行 <code>python scripts/build_readme_html.py</code> 生成手册。</p></body></html>", 404

    @app.route('/groups')
    def groups_page():
        return render_template('groups.html')

    @app.route('/analysis')
    def analysis_page():
        return render_template('analysis.html')

    @app.route('/knowledge')
    def knowledge_page():
        return render_template('knowledge.html')

    # Serve WeChat built-in expression assets (dev mode only — not bundled in PyInstaller)
    _WXEMOJI_DIR = None
    if not getattr(sys, 'frozen', False):
        from pathlib import Path as _Path
        _WXEMOJI_DIR = _Path(__file__).resolve().parents[4] / 'tempWeChatDataAnalysis' / 'frontend' / 'public' / 'wxemoji'

    @app.route('/wxemoji/<path:filename>')
    def wxemoji(filename):
        from flask import send_from_directory, abort as _abort
        if _WXEMOJI_DIR is None or not os.path.isdir(_WXEMOJI_DIR):
            _abort(404)
        # Normalize to basename to prevent path traversal; send_from_directory
        # is already safe but this avoids rejecting filenames like 'good..bad.png'.
        safe_name = os.path.basename(filename.replace('\\', '/'))
        if not safe_name:
            _abort(404)
        return send_from_directory(str(_WXEMOJI_DIR), safe_name)

    return app


def run_server(decrypted_dir: str, wxid: str = None, db_dir: str = None,
               host: str = '127.0.0.1', port: int = 5051, open_url: str = None):
    app = create_app(decrypted_dir, wxid=wxid, db_dir=db_dir)

    # Start AI analysis scheduler
    try:
        from engine.services import ai_scheduler
        from engine.services.ai_analyzer import config_path_for
        ai_scheduler.start_scheduler(config_path_for(decrypted_dir), decrypted_dir)
    except Exception as e:
        logger.warning("启动 AI 分析调度器失败: %s", e)

    # Start Knowledge Radar scheduler
    try:
        from engine.services import knowledge_scheduler
        from engine.services.ai_analyzer import config_path_for
        knowledge_scheduler.start_scheduler(config_path_for(decrypted_dir), decrypted_dir)
    except Exception as e:
        logger.warning("启动知识雷达调度器失败: %s", e)

    url = open_url or f'http://{host}:{port}'
    timer = threading.Timer(1.0, lambda: webbrowser.open(url))
    timer.daemon = True
    timer.start()
    app.run(host=host, port=port, debug=False, use_reloader=False)
