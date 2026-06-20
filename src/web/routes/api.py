"""REST API endpoints."""
import os
import sys
from flask import Blueprint, request, jsonify, current_app

# Ensure src/ is on path for engine imports
_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from engine.services.chat import get_contacts
from engine.services.message import query_messages, query_message_detail, get_chat_stats, get_chat_dates
from engine.services.media import serve_media, serve_hardlink_media, serve_voice, transcribe_voice, decrypt_emoticon_aes_cbc
from engine.services.address_book import get_all_contacts, get_all_groups
from engine.config_file import get_group_blacklist, set_group_blacklist
import csv
import io
import json
import sqlite3
import time
from datetime import datetime as _dt

api_bp = Blueprint('api', __name__)

_GROUP_NAMES_CACHE_TTL = 7 * 24 * 3600


def _cfg():
    return (current_app.config.get('DECRYPTED_DIR', ''),
            current_app.config.get('WXID'),
            current_app.config.get('DB_DIR'))


def _group_names_cache_path(decrypted_dir: str) -> str:
    return os.path.join(os.path.dirname(decrypted_dir), 'ai_analysis', 'group_names_cache.json')


def _load_group_names_cache(decrypted_dir: str, max_age: int = _GROUP_NAMES_CACHE_TTL) -> list:
    path = _group_names_cache_path(decrypted_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if time.time() - float(data.get('ts', 0)) > max_age:
            return None
        groups = data.get('groups')
        return groups if isinstance(groups, list) else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _save_group_names_cache(decrypted_dir: str, groups: list) -> None:
    path = _group_names_cache_path(decrypted_dir)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'ts': time.time(), 'groups': groups}, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _group_blacklist_ids() -> set:
    return {
        item.get('wxid')
        for item in get_group_blacklist()
        if isinstance(item, dict) and item.get('wxid')
    }


def _filter_blacklisted_groups(groups: list) -> list:
    blocked = _group_blacklist_ids()
    if not blocked:
        return groups or []
    return [g for g in groups or [] if g.get('wxid') not in blocked]


def _resolve_group_names(decrypted_dir: str, progress_cb=None) -> list:
    groups = _filter_blacklisted_groups(get_all_groups(decrypted_dir))
    total = len(groups)
    if progress_cb:
        progress_cb(0, total, '准备解析群名称')

    needs_resolve = any(
        (g.get('display_name') or '') == (g.get('wxid') or '')
        for g in groups[:10]
    )
    if needs_resolve:
        from engine.services.chat import _load_contacts, _load_sessions, _load_room_owners, _find_file, _resolve_display
        contact_db = _find_file(decrypted_dir, "contact/contact.db", "contact.db")
        session_db = _find_file(decrypted_dir, "session/session.db", "session.db")
        id_to_name, name_to_id, _ = _load_contacts(contact_db)
        session_summaries = _load_sessions(session_db)
        room_owners = _load_room_owners(contact_db)
        for idx, g in enumerate(groups, 1):
            uname = g['wxid']
            g['display_name'] = _resolve_display(
                uname, is_group=True, decrypted_dir=decrypted_dir,
                id_to_name=id_to_name, name_to_id=name_to_id,
                session_summaries=session_summaries, room_owners=room_owners,
            )
            if progress_cb:
                progress_cb(idx, total, f'已解码 {idx}/{total} 个群名称')
    else:
        for idx, _ in enumerate(groups, 1):
            if progress_cb:
                progress_cb(idx, total, f'已读取 {idx}/{total} 个群名称')

    _save_group_names_cache(decrypted_dir, groups)
    return groups


def _recent_group_activity(decrypted_dir: str, chat_ids: list, days: int = 3) -> dict:
    """Count messages per group in the recent N days.

    Batches queries by DB file to minimize connection overhead.
    """
    try:
        from engine.services.message import _find_all_chat_dbs
    except ImportError:
        return {}

    cutoff = int(time.time()) - max(1, int(days or 3)) * 86400
    counts = {cid: 0 for cid in (chat_ids or [])}

    # Group queries by db_path to reuse connections
    db_queries = {}  # {db_path: [(chat_id, table_name), ...]}
    for chat_id in chat_ids or []:
        try:
            for db_path, table_name in _find_all_chat_dbs(decrypted_dir, chat_id):
                db_queries.setdefault(db_path, []).append((chat_id, table_name))
        except Exception:
            continue

    for db_path, queries in db_queries.items():
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            for chat_id, table_name in queries:
                try:
                    row = conn.execute(
                        f"SELECT COUNT(*) FROM [{table_name}] WHERE create_time >= ?",
                        (cutoff,),
                    ).fetchone()
                    counts[chat_id] = counts.get(chat_id, 0) + (int(row[0] or 0) if row else 0)
                except sqlite3.Error:
                    continue
        except sqlite3.Error:
            pass
        finally:
            if conn:
                conn.close()

    return counts


def _enrich_group_stats(decrypted_dir: str, groups: list, include_activity: bool = False) -> list:
    """Attach message_count, last_msg_time, and optional recent activity."""
    stats_by_id = {}
    try:
        for g in get_all_groups(decrypted_dir):
            wxid = g.get('wxid')
            if wxid:
                stats_by_id[wxid] = g
    except Exception:
        stats_by_id = {}

    for g in groups or []:
        src = stats_by_id.get(g.get('wxid'), {})
        g['msg_count'] = int(g.get('msg_count') or src.get('msg_count') or 0)
        g['last_msg_time'] = g.get('last_msg_time') or src.get('last_msg_time')

    if include_activity:
        activity = _recent_group_activity(decrypted_dir, [g.get('wxid') for g in groups or []])
        for g in groups or []:
            g['active_3d'] = int(activity.get(g.get('wxid'), 0) or 0)

    return groups or []


def _parse_group_time(value) -> int:
    if not value:
        return 0
    if isinstance(value, (int, float)):
        return int(value / 1000) if value > 1000000000000 else int(value)
    try:
        return int(value)
    except (ValueError, TypeError):
        pass
    try:
        return int(_dt.fromisoformat(str(value).replace('Z', '+00:00')).timestamp())
    except (ValueError, TypeError):
        return 0


def _recent_days_arg() -> int | None:
    raw = request.args.get('recent_days')
    if raw in (None, ''):
        return None
    try:
        days = int(raw)
    except (ValueError, TypeError):
        return None
    return max(0, days)


def _filter_recent_groups(groups: list, days: int | None) -> list:
    if days is None or days <= 0:
        return groups or []
    cutoff = int(time.time()) - days * 86400
    return [g for g in groups or [] if _parse_group_time(g.get('last_msg_time')) >= cutoff]


@api_bp.route('/contacts')
def contacts():
    decrypted_dir, wxid, _ = _cfg()
    q = request.args.get('q', '').strip().lower()
    all_contacts = get_contacts(decrypted_dir, wxid)
    if q:
        all_contacts = [c for c in all_contacts
                        if q in c['name'].lower() or q in c['id'].lower()]
    return jsonify({'contacts': all_contacts, 'total': len(all_contacts)})


@api_bp.route('/messages')
def messages():
    decrypted_dir, wxid, db_dir = _cfg()
    chat_id = request.args.get('chat_id', '')
    if not chat_id:
        return jsonify({'error': 'chat_id required'}), 400
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    if page < 1:
        return jsonify({'error': 'page must be >= 1'}), 400
    if per_page < 1 or per_page > 200:
        return jsonify({'error': 'per_page must be between 1 and 200'}), 400
    try:
        result = query_messages(
            decrypted_dir, chat_id, wxid=wxid,
            page=page, per_page=per_page,
            start_date=request.args.get('start_date'),
            end_date=request.args.get('end_date'),
            msg_types=request.args.get('type'),
            sender=request.args.get('sender'),
            keyword=request.args.get('keyword'),
        )
    except FileNotFoundError as e:
        return jsonify({'error': str(e), 'messages': [], 'pagination': {'page': 1, 'per_page': 50, 'total': 0, 'total_pages': 1}}), 404
    return jsonify(result)


@api_bp.route('/messages/<int:msg_id>')
def message_detail(msg_id):
    decrypted_dir, _, _ = _cfg()
    chat_id = request.args.get('chat_id', '')
    detail = query_message_detail(decrypted_dir, msg_id, chat_id=chat_id)
    if detail is None:
        return jsonify({'error': 'message not found'}), 404
    return jsonify(detail)


@api_bp.route('/chat/<chat_id>/stats')
def chat_stats(chat_id):
    decrypted_dir, wxid, _ = _cfg()
    try:
        return jsonify(get_chat_stats(decrypted_dir, chat_id, wxid=wxid))
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404


@api_bp.route('/chat/<chat_id>/dates')
def chat_dates(chat_id):
    decrypted_dir, _, _ = _cfg()
    try:
        return jsonify(get_chat_dates(decrypted_dir, chat_id))
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404


@api_bp.route('/chat/<chat_id>/group-info')
def group_info(chat_id):
    decrypted_dir, _, _ = _cfg()
    from engine.services.chat import get_group_info
    try:
        info = get_group_info(decrypted_dir, chat_id)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    if info is None:
        return jsonify({'error': 'not a group chat'}), 400
    return jsonify(info)


@api_bp.route('/media')
def media():
    _, _, db_dir = _cfg()
    path = request.args.get('path', '')
    if not path:
        return jsonify({'error': 'path required'}), 400
    return serve_media(db_dir, path)


@api_bp.route('/hardlink-media')
def hardlink_media():
    """Serve media file resolved via HardLink DB protobuf data.
    Query params: md5, path (local_path from media_info), type (3=image, 43=video, 6=file).
    """
    decrypted_dir, wxid, _ = _cfg()
    media_info = {
        'md5': request.args.get('md5', ''),
        'local_path': request.args.get('path', ''),
        'media_type': request.args.get('type', 0, type=int),
        'file_name': request.args.get('file_name', ''),
        'local_id': request.args.get('local_id', 0, type=int),
    }
    return serve_hardlink_media(decrypted_dir, media_info, wxid)


@api_bp.route('/voice')
def voice():
    """Serve voice file (SILK format, converted to WAV if possible).
    Query params: path (voice_path), create_time (optional int), local_id (optional int).
    When path alone can't find the file, create_time+local_id are used to
    extract voice data from VoiceInfo table on-the-fly.
    """
    decrypted_dir, _, db_dir = _cfg()
    path = request.args.get('path', '')
    if not path:
        return jsonify({'error': 'path required'}), 400
    create_time = request.args.get('create_time', type=int)
    local_id = request.args.get('local_id', type=int)
    return serve_voice(decrypted_dir, path,
                       create_time=create_time, local_id=local_id,
                       db_dir=db_dir)


@api_bp.route('/voice/transcribe')
def voice_transcribe():
    """Transcribe voice to text.
    Query params: path (voice_path from media_info).
    """
    decrypted_dir, _, _ = _cfg()
    path = request.args.get('path', '')
    if not path:
        return jsonify({'error': 'path required'}), 400
    try:
        text = transcribe_voice(decrypted_dir, path)
        return jsonify({'text': text})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/emoji')
def emoji():
    """Serve emoji/sticker image by MD5, with remote CDN fallback.

    Query params: account (wxid), md5, emoji_url (optional remote CDN URL).
    """
    from flask import redirect, abort
    from urllib.parse import urlparse

    md5_val = request.args.get('md5', '').strip().lower()
    emoji_url = request.args.get('emoji_url', '').strip()
    account = request.args.get('account', '').strip()

    # Validate emoji_url is from known WeChat CDN domains
    _ALLOWED_EMOJI_DOMAINS = {
        'wx.qlogo.cn', 'wx.gtimg.cn', 'emoji.qpic.cn',
        'res.wx.qq.com', 'mmbiz.qpic.cn', 'mmbiz.qlogo.cn',
    }
    def _is_safe_emoji_url(url):
        if not url:
            return False
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ('http', 'https'):
                return False
            host = parsed.netloc.split(':')[0]
            return any(host == d or host.endswith('.' + d) for d in _ALLOWED_EMOJI_DOMAINS)
        except Exception:
            return False

    if not md5_val or len(md5_val) != 32:
        abort(404)

    decrypted_dir, _, _ = _cfg()
    if not os.path.isdir(decrypted_dir):
        if _is_safe_emoji_url(emoji_url):
            return redirect(emoji_url)
        abort(404)

    # 1. Search local filesystem for emoji by MD5
    search_dirs = [
        os.path.join(decrypted_dir, 'emoticon'),
        os.path.join(decrypted_dir, 'Emoticon'),
        os.path.join(decrypted_dir, 'sticker'),
        os.path.join(decrypted_dir, 'Sticker'),
        os.path.join(decrypted_dir, 'msg', 'attach'),
    ]
    if account:
        search_dirs.insert(0, os.path.join(os.path.dirname(decrypted_dir), account, 'emoticon'))

    variants = [md5_val, f'{md5_val}_t', f'{md5_val}_h',
                f'{md5_val}.jpg', f'{md5_val}.png', f'{md5_val}.gif', f'{md5_val}.webp',
                f'{md5_val}_t.jpg', f'{md5_val}_t.png',
                f'{md5_val}.dat', f'{md5_val}_h.dat', f'{md5_val}_t.dat']

    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for v in variants:
            path = os.path.join(d, v)
            if os.path.isfile(path):
                resolved = path
                # Handle .dat encrypted files
                if resolved.lower().endswith('.dat'):
                    try:
                        with open(resolved, 'rb') as f:
                            raw = f.read()
                        dec = decrypt_emoticon_aes_cbc(raw, md5_val)
                        if dec:
                            from flask import send_file
                            from io import BytesIO
                            return send_file(BytesIO(dec), mimetype='image/png')
                    except Exception:
                        pass
                    continue
                from flask import send_file
                mime, _ = __import__('mimetypes').guess_type(resolved)
                return send_file(resolved, mimetype=mime or 'image/png')

    # 2. Fallback: proxy from remote CDN URL (only allowed domains)
    if _is_safe_emoji_url(emoji_url):
        return redirect(emoji_url)

    abort(404)


@api_bp.route('/harvest-keys/status')
def harvest_keys_status():
    """Get V2 key cache status: how many keys cached vs total V2 files."""
    import json as _json
    decrypted_dir, wxid, _ = _cfg()
    if not os.path.isdir(decrypted_dir):
        return jsonify({'error': 'decrypted_dir not configured'}), 400

    keys_file = os.path.join(decrypted_dir, '_media_keys.json')
    cached = 0
    try:
        if os.path.isfile(keys_file) and os.path.getsize(keys_file) > 0:
            with open(keys_file, 'r', encoding='utf-8') as f:
                data = _json.load(f)
            cached = len(data.get('md5_keys', {}))
    except Exception:
        pass

    # Count V2 files in media/images/
    v2_total = 0
    img_dir = os.path.join(decrypted_dir, 'media', 'images')
    if os.path.isdir(img_dir):
        for fname in os.listdir(img_dir):
            if fname.lower().endswith('.dat'):
                fpath = os.path.join(img_dir, fname)
                try:
                    with open(fpath, 'rb') as f:
                        header = f.read(6)
                    if header == b'\x07\x08V2\x08\x07':
                        v2_total += 1
                except OSError:
                    pass

    from engine.services.v2_key_extract import is_wechat_running as _wx_running
    return jsonify({
        'cached': cached,
        'v2_total': v2_total,
        'pending': max(0, v2_total - cached),
        'wechat_running': _wx_running(),
    })


@api_bp.route('/harvest-keys/run', methods=['POST'])
def harvest_keys_run():
    """Run one round of V2 key harvesting from WeChat memory."""
    import json as _json
    from engine.services.v2_key_extract import harvest_v2_keys, is_wechat_running as _wx_running

    decrypted_dir, wxid, _ = _cfg()
    if not os.path.isdir(decrypted_dir):
        return jsonify({'error': 'decrypted_dir not configured'}), 400

    if not _wx_running():
        return jsonify({'error': '微信未运行，请先启动微信并浏览包含图片的聊天记录'}), 400

    # Run a single scan round
    found = harvest_v2_keys(
        decrypted_dir, wxid=wxid,
        interval=0, max_rounds=1,
        print_fn=lambda *a, **kw: None
    )

    return jsonify({
        'found': len(found),
        'keys': {md5: key.hex() for md5, key in found.items()},
    })


@api_bp.route('/address-book')
def address_book():
    """Return contacts from contact.db with message stats, paginated.

    Query params:
        q        — search keyword (matches display_name, remark, nick_name, alias, wxid, phone, description)
        sort     — 'name' (default), 'msg_count', 'last_time'
        has_chat — '1' (only with chats), '0' (only without)
        letter   — filter by first letter of display_name
        page     — page number (default 1)
        per_page — items per page (default 100, max 500)
    """
    decrypted_dir, wxid, db_dir = _cfg()
    contacts = get_all_contacts(decrypted_dir)

    q = request.args.get('q', '').strip().lower()
    sort = request.args.get('sort', 'name')
    has_chat = request.args.get('has_chat')
    letter = request.args.get('letter', '').strip().upper()

    if q:
        def _match(c):
            if q in c['display_name'].lower():
                return True
            if q in c['remark'].lower():
                return True
            if q in c['nick_name'].lower():
                return True
            if q in c['alias'].lower():
                return True
            if q in c['wxid'].lower():
                return True
            if c.get('phone') and q in c['phone']:
                return True
            if c.get('description') and q in c['description'].lower():
                return True
            return False
        contacts = [c for c in contacts if _match(c)]
    if has_chat == '1':
        contacts = [c for c in contacts if c['msg_count'] > 0]
    elif has_chat == '0':
        contacts = [c for c in contacts if c['msg_count'] == 0]
    if letter:
        contacts = [c for c in contacts
                    if (c['display_name'] or c['wxid'])[:1].upper() == letter]

    if sort == 'msg_count':
        contacts.sort(key=lambda c: c['msg_count'], reverse=True)
    elif sort == 'last_time':
        contacts.sort(key=lambda c: c['last_msg_time'] or 0, reverse=True)

    # Pagination
    total = len(contacts)
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        per_page = max(1, min(500, int(request.args.get('per_page', 100))))
    except (ValueError, TypeError):
        per_page = 100

    start = (page - 1) * per_page
    end = start + per_page
    page_contacts = contacts[start:end]
    total_pages = max(1, (total + per_page - 1) // per_page)

    return jsonify({
        'contacts': page_contacts,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': total_pages,
    })


@api_bp.route('/address-book/<wxid>')
def address_book_detail(wxid):
    """Return single contact detail."""
    decrypted_dir, _, _ = _cfg()
    contacts = get_all_contacts(decrypted_dir)
    for c in contacts:
        if c['wxid'] == wxid:
            return jsonify(c)
    return jsonify({'error': 'contact not found'}), 404


@api_bp.route('/address-book/groups')
def address_book_groups():
    """Return group chat list with resolved display names.

    Fast path: groups from chats.db index already have pre-computed display_name.
    Slow path: groups from contact.db need _resolve_display lookup.
    """
    decrypted_dir, wxid, db_dir = _cfg()
    force = request.args.get('force') == '1'
    include_activity = request.args.get('activity') == '1'
    recent_days = _recent_days_arg()
    groups = None if force else _load_group_names_cache(decrypted_dir)
    source = 'cache'
    if groups is None:
        groups = _resolve_group_names(decrypted_dir)
        source = 'resolved'
    else:
        groups = _filter_blacklisted_groups(groups)
    groups = _enrich_group_stats(decrypted_dir, groups, include_activity=include_activity)
    groups = _filter_recent_groups(groups, recent_days)

    return jsonify({
        'groups': groups,
        'total': len(groups),
        'source': source,
        'activity': include_activity,
        'recent_days': recent_days,
    })


@api_bp.route('/address-book/groups/blacklist', methods=['GET'])
def address_book_groups_blacklist_get():
    """Return blacklisted groups excluded from group loading."""
    items = get_group_blacklist()
    return jsonify({'blacklist': items, 'total': len(items)})


@api_bp.route('/address-book/groups/blacklist', methods=['POST'])
def address_book_groups_blacklist_add():
    """Add groups to the loading blacklist."""
    data = request.get_json(silent=True) or {}
    incoming = data.get('groups') or []
    if isinstance(incoming, dict):
        incoming = [incoming]
    if not isinstance(incoming, list):
        return jsonify({'error': 'groups must be a list'}), 400

    now = _dt.now().isoformat(timespec='seconds')
    existing = get_group_blacklist()
    by_id = {
        item.get('wxid'): item
        for item in existing
        if isinstance(item, dict) and item.get('wxid')
    }
    for item in incoming:
        if isinstance(item, str):
            wxid = item.strip()
            display_name = wxid
        elif isinstance(item, dict):
            wxid = str(item.get('wxid', '')).strip()
            display_name = str(item.get('display_name') or wxid)
        else:
            continue
        if not wxid:
            continue
        by_id[wxid] = {
            'wxid': wxid,
            'display_name': display_name,
            'added_at': by_id.get(wxid, {}).get('added_at') or now,
        }
    items = list(by_id.values())
    set_group_blacklist(items)
    return jsonify({'blacklist': items, 'total': len(items)})


@api_bp.route('/address-book/groups/blacklist', methods=['DELETE'])
def address_book_groups_blacklist_delete():
    """Remove one or more groups from the loading blacklist."""
    wxids = request.args.getlist('wxid')
    data = request.get_json(silent=True) or {}
    body_ids = data.get('wxids') or data.get('groups') or []
    if isinstance(body_ids, str):
        body_ids = [body_ids]
    for item in body_ids if isinstance(body_ids, list) else []:
        if isinstance(item, dict):
            wxids.append(str(item.get('wxid', '')).strip())
        else:
            wxids.append(str(item).strip())
    remove_ids = {w for w in wxids if w}
    if not remove_ids:
        return jsonify({'error': 'wxid required'}), 400
    items = [
        item for item in get_group_blacklist()
        if item.get('wxid') not in remove_ids
    ]
    set_group_blacklist(items)
    return jsonify({'blacklist': items, 'total': len(items)})


@api_bp.route('/address-book/groups/stream')
def address_book_groups_stream():
    """Stream group-name loading progress as SSE, then return full groups."""
    from flask import Response, stream_with_context

    decrypted_dir, _, _ = _cfg()
    force = request.args.get('force') == '1'
    include_activity = request.args.get('activity') == '1'
    recent_days = _recent_days_arg()

    def _event(payload: dict) -> str:
        return 'data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n'

    @stream_with_context
    def _gen():
        cached = None if force else _load_group_names_cache(decrypted_dir)
        if cached is not None:
            cached = _filter_blacklisted_groups(cached)
            cached = _enrich_group_stats(decrypted_dir, cached, include_activity=include_activity)
            visible = _filter_recent_groups(cached, recent_days)
            total = len(visible)
            yield _event({
                'stage': 'progress',
                'done': total,
                'total': total,
                'progress': 1,
                'detail': f'已从缓存读取 {total}/{total} 个群名称',
                'source': 'cache',
            })
            yield _event({'stage': 'done', 'groups': visible, 'total': total, 'source': 'cache', 'recent_days': recent_days})
            return

        try:
            groups = _filter_blacklisted_groups(get_all_groups(decrypted_dir))
            total = len(groups)
            yield _event({
                'stage': 'progress',
                'done': 0,
                'total': total,
                'progress': 0,
                'detail': f'准备解析 0/{total} 个群名称',
                'source': 'resolved',
            })

            needs_resolve = any(
                (g.get('display_name') or '') == (g.get('wxid') or '')
                for g in groups[:10]
            )
            if needs_resolve:
                from engine.services.chat import _load_contacts, _load_sessions, _load_room_owners, _find_file, _resolve_display
                contact_db = _find_file(decrypted_dir, "contact/contact.db", "contact.db")
                session_db = _find_file(decrypted_dir, "session/session.db", "session.db")
                id_to_name, name_to_id, _ = _load_contacts(contact_db)
                session_summaries = _load_sessions(session_db)
                room_owners = _load_room_owners(contact_db)
                for idx, g in enumerate(groups, 1):
                    uname = g['wxid']
                    g['display_name'] = _resolve_display(
                        uname, is_group=True, decrypted_dir=decrypted_dir,
                        id_to_name=id_to_name, name_to_id=name_to_id,
                        session_summaries=session_summaries, room_owners=room_owners,
                    )
                    yield _event({
                        'stage': 'progress',
                        'done': idx,
                        'total': total,
                        'progress': idx / max(total, 1),
                        'detail': f'已解码 {idx}/{total} 个群名称',
                        'source': 'resolved',
                    })
            else:
                for idx, _ in enumerate(groups, 1):
                    yield _event({
                        'stage': 'progress',
                        'done': idx,
                        'total': total,
                        'progress': idx / max(total, 1),
                        'detail': f'已读取 {idx}/{total} 个群名称',
                        'source': 'resolved',
                    })

            _save_group_names_cache(decrypted_dir, groups)
            groups = _enrich_group_stats(decrypted_dir, groups, include_activity=include_activity)
            if include_activity:
                _save_group_names_cache(decrypted_dir, groups)
            visible = _filter_recent_groups(groups, recent_days)
            visible_total = len(visible)
            yield _event({
                'stage': 'progress',
                'done': visible_total,
                'total': visible_total,
                'progress': 1,
                'detail': f'群名称加载完成 {visible_total}/{visible_total}',
                'source': 'resolved',
            })
            yield _event({'stage': 'done', 'groups': visible, 'total': visible_total, 'source': 'resolved', 'recent_days': recent_days})
        except Exception as e:
            yield _event({'stage': 'error', 'message': str(e)})

    return Response(_gen(), mimetype='text/event-stream')


@api_bp.route('/address-book/export')
def address_book_export():
    """Export contacts as CSV."""
    decrypted_dir, _, _ = _cfg()
    contacts = get_all_contacts(decrypted_dir)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['wxid', 'display_name', 'remark', 'nick_name', 'alias',
                     'phone', 'sex', 'region', 'signature', 'description',
                     'msg_count', 'last_msg_time', 'is_group'])
    SEX_MAP = {0: '', 1: '男', 2: '女'}
    for c in contacts:
        sex_label = SEX_MAP.get(c.get('sex'), '')
        region = ' '.join(filter(None, [c.get('country', ''), c.get('province', ''), c.get('city', '')]))
        writer.writerow([
            c['wxid'], c['display_name'], c['remark'], c['nick_name'],
            c['alias'],
            c.get('phone', ''),
            sex_label,
            region,
            c.get('signature', ''),
            c.get('description', ''),
            c['msg_count'],
            c['last_msg_time'] or '',
            'Y' if c['is_group'] else 'N',
        ])

    from flask import Response
    csv_str = output.getvalue()
    output.close()
    return Response(
        csv_str,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=address_book.csv'}
    )


@api_bp.route('/system/status')
def system_status():
    """Return system status for the dashboard."""
    import glob as _glob
    from engine.version import VERSION
    from engine.config_file import get_backup_data_dir

    decrypted_dir = current_app.config.get('DECRYPTED_DIR', '')
    wxid = current_app.config.get('WXID', '')

    # Check if decrypted data exists
    has_data = False
    db_count = 0
    total_size = 0
    msg_dir = os.path.join(decrypted_dir, 'message') if decrypted_dir else ''
    if msg_dir and os.path.isdir(msg_dir):
        for f in os.listdir(msg_dir):
            if f.startswith('message_') and f.endswith('.db'):
                db_count += 1
                total_size += os.path.getsize(os.path.join(msg_dir, f))
        has_data = db_count > 0

    # Check other data dirs
    contact_db = os.path.join(decrypted_dir, 'contact', 'contact.db') if decrypted_dir else ''
    has_contacts = os.path.isfile(contact_db)
    media_dir = os.path.join(decrypted_dir, 'media') if decrypted_dir else ''
    has_media = os.path.isdir(media_dir)

    # Knowledge DB stats
    knowledge_count = 0
    try:
        from engine.services.knowledge_store import list_cards, knowledge_db_path
        kdb = knowledge_db_path(decrypted_dir) if decrypted_dir else ''
        if kdb and os.path.isfile(kdb):
            result = list_cards(kdb, page=1, per_page=1)
            knowledge_count = result.get('total', 0)
    except Exception:
        pass

    # Last backup info
    backup_dir = get_backup_data_dir()

    return jsonify({
        'version': VERSION,
        'wxid': wxid,
        'has_data': has_data,
        'db_count': db_count,
        'db_size_mb': round(total_size / 1024 / 1024, 1),
        'has_contacts': has_contacts,
        'has_media': has_media,
        'knowledge_count': knowledge_count,
        'backup_dir': backup_dir or '',
        'decrypted_dir': decrypted_dir or '',
    })


@api_bp.route('/search')
def global_search():
    """Global search across contacts, messages, and knowledge cards."""
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({'contacts': [], 'messages': [], 'knowledge': []})

    decrypted_dir = current_app.config.get('DECRYPTED_DIR', '')
    results = {'contacts': [], 'messages': [], 'knowledge': []}

    # Search contacts
    try:
        contacts = get_contacts(decrypted_dir)
        q_lower = q.lower()
        for c in contacts:
            name = (c.get('display_name') or c.get('name') or '').lower()
            remark = (c.get('remark') or '').lower()
            if q_lower in name or q_lower in remark:
                results['contacts'].append({
                    'name': c.get('display_name') or c.get('name', ''),
                    'chat_id': c.get('chat_id', ''),
                })
                if len(results['contacts']) >= 5:
                    break
    except Exception:
        pass

    # Search knowledge cards
    try:
        from engine.services.knowledge_store import list_cards, knowledge_db_path
        kdb = knowledge_db_path(decrypted_dir) if decrypted_dir else ''
        if kdb and os.path.isfile(kdb):
            cards = list_cards(kdb, q=q, page=1, per_page=5)
            for card in cards.get('cards', []):
                results['knowledge'].append({
                    'title': card.get('title', ''),
                    'score': card.get('score', 0),
                })
    except Exception:
        pass

    return jsonify(results)


@api_bp.route('/config/export')
def export_config():
    """Export config (without sensitive data) as JSON download."""
    from engine.config_file import export_config as _export
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix='.json', delete=False, mode='w')
    tmp.close()
    if _export(tmp.name):
        from flask import send_file
        return send_file(tmp.name, as_attachment=True,
                        download_name='wechat_exp_config.json',
                        mimetype='application/json')
    return jsonify({'error': '导出失败'}), 500


@api_bp.route('/config/import', methods=['POST'])
def import_config():
    """Import config from uploaded JSON file."""
    from engine.config_file import import_config as _import
    file = request.files.get('file')
    if not file:
        return jsonify({'error': '请上传配置文件'}), 400
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix='.json', delete=False, mode='wb')
    tmp.write(file.read())
    tmp.close()
    if _import(tmp.name):
        return jsonify({'ok': True, 'message': '配置已导入'})
    return jsonify({'error': '导入失败，请检查文件格式'}), 400
