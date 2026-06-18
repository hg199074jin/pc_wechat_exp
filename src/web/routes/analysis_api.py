"""AI Analysis API endpoints."""
from __future__ import annotations

import os
import sys
import threading
import json as _json
import time as _time
from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify, current_app

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from web.sse import create_sse_progress, sse_response
from engine.services import ai_analyzer, ai_scheduler
from engine.services import knowledge_extractor, knowledge_store
from engine.services.analysis_artifact import artifact_path, load_artifact

analysis_bp = Blueprint('analysis_api', __name__, url_prefix='/api/analysis')

_run_lock = threading.Lock()
_GROUP_NAMES_CACHE_TTL = 7 * 24 * 3600


def _decrypted_dir() -> str:
    return current_app.config.get('DECRYPTED_DIR', '')


def _config_path() -> str:
    return ai_analyzer.config_path_for(_decrypted_dir())


def _storage_dir() -> str:
    return ai_analyzer.storage_dir_for(_decrypted_dir())


def _cached_group_items(decrypted_dir: str) -> list:
    path = os.path.join(os.path.dirname(decrypted_dir), 'ai_analysis', 'group_names_cache.json')
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = _json.load(f)
        if _time.time() - float(data.get('ts', 0)) > _GROUP_NAMES_CACHE_TTL:
            return None
        groups = data.get('groups')
        if not isinstance(groups, list):
            return None
        return [
            {'wxid': g['wxid'], 'display_name': g.get('display_name') or g['wxid']}
            for g in groups
            if g.get('wxid', '').endswith('@chatroom')
        ]
    except (OSError, ValueError, TypeError, KeyError, _json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# LLM Config
# ---------------------------------------------------------------------------

@analysis_bp.route('/config', methods=['GET'])
def get_config():
    cfg = ai_analyzer.load_llm_config(_config_path())
    masked = ai_analyzer._mask_api_key(cfg.get('api_key', '')) if cfg.get('api_key') else ''
    return jsonify({'llm': {**cfg, 'api_key': masked}})


@analysis_bp.route('/config', methods=['PUT'])
def put_config():
    data = request.get_json(silent=True) or {}
    llm_cfg = data.get('llm', {})
    config_path = _config_path()
    # If mask matches existing, keep real key
    if llm_cfg.get('api_key', '').startswith('sk-') and '*' in llm_cfg.get('api_key', ''):
        existing = ai_analyzer.load_llm_config(config_path)
        llm_cfg['api_key'] = existing.get('api_key', '')
    ai_analyzer.save_llm_config(llm_cfg, config_path)
    return jsonify({'ok': True})


@analysis_bp.route('/test', methods=['POST'])
def test_connection():
    cfg = ai_analyzer.load_llm_config(_config_path())
    if not cfg.get('base_url') or not cfg.get('api_key'):
        return jsonify({'ok': False, 'error': '未配置 LLM'}), 400
    try:
        reply = ai_analyzer.call_llm(
            system='你是一个助手。',
            user='说"连接成功"即可。',
            base_url=cfg['base_url'], api_key=cfg['api_key'],
            model=cfg['model'], temperature=cfg.get('temperature', 0.3),
            max_tokens=64, timeout=ai_analyzer._llm_timeout(cfg),
        )
        return jsonify({'ok': True, 'reply': reply[:200]})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Manual analysis (SSE)
# ---------------------------------------------------------------------------

@analysis_bp.route('/run', methods=['POST'])
def run_analysis():
    if not _run_lock.acquire(blocking=False):
        return jsonify({'error': '已有分析任务在运行'}), 409
    data = request.get_json(silent=True) or {}
    chat_ids = data.get('chat_ids', [])
    date_range = data.get('date_range', [])
    analysis_mode = data.get('analysis_mode') or 'range'
    if not chat_ids or len(date_range) != 2:
        _run_lock.release()
        return jsonify({'error': 'chat_ids 和 date_range 必填'}), 400

    decrypted_dir = _decrypted_dir()
    wxid = current_app.config.get('WXID')
    push, gen = create_sse_progress()

    def _run():
        try:
            start = datetime.strptime(date_range[0], '%Y-%m-%d')
            end = datetime.strptime(date_range[1], '%Y-%m-%d')
            if start > end:
                start, end = end, start

            group_names = {}
            for cid in chat_ids:
                from engine.services.name_resolver import resolve_wxid
                group_names[cid] = resolve_wxid(decrypted_dir, cid) or cid

            all_results = []
            if analysis_mode == 'daily':
                current = start
                while current <= end:
                    date_str = current.strftime('%Y-%m-%d')
                    push('progress', f'分析 {date_str}...', 0.1)
                    day_results = ai_analyzer.analyze_multiple(
                        decrypted_dir, chat_ids, group_names,
                        date_str, wxid=wxid,
                        progress_cb=lambda *args: _push_analysis_progress(push, *args),
                    )
                    for item in day_results:
                        item['date'] = date_str
                    all_results.extend(day_results)
                    current += timedelta(days=1)
            else:
                start_str = start.strftime('%Y-%m-%d')
                end_str = end.strftime('%Y-%m-%d')
                push('progress', f'分析 {start_str} ~ {end_str}...', 0.1)
                all_results = ai_analyzer.analyze_multiple_range(
                    decrypted_dir, chat_ids, group_names,
                    start_str, end_str, wxid=wxid,
                    progress_cb=lambda *args: _push_analysis_progress(push, *args),
                )
            ok_count = sum(1 for r in all_results if r.get('status') == 'ok')
            skip_count = sum(1 for r in all_results if r.get('status') == 'skip')
            error_count = sum(1 for r in all_results if r.get('status') == 'error')
            push.done({
                'ok': True,
                'summary': {
                    'ok': ok_count,
                    'skip': skip_count,
                    'error': error_count,
                    'total': len(all_results),
                },
                'results': all_results,
            })
        except Exception as e:
            push.error(str(e))
        finally:
            _run_lock.release()

    threading.Thread(target=_run, daemon=True).start()
    return sse_response(gen)


def _push_analysis_progress(push, *args):
    """Normalize analyzer progress callbacks into user-readable SSE events."""
    if len(args) == 5:
        cid, group_name, status, current, total = args
        if status == 'analyzing':
            detail = f'准备分析「{group_name}」({current}/{total})'
        elif status == 'done':
            detail = f'「{group_name}」分析完成 ({current}/{total})'
        elif status == 'skip':
            detail = f'「{group_name}」已跳过：没有可分析内容 ({current}/{total})'
        elif status == 'error':
            detail = f'「{group_name}」分析失败 ({current}/{total})'
        else:
            detail = f'「{group_name}」{status} ({current}/{total})'
        push('progress', detail, current / max(total, 1) * 0.9 + 0.1)
        return

    if len(args) == 6:
        cid, group_name, status, current, total, msg_count = args
        if status == 'summarizing':
            detail = f'正在对「{group_name}」的 {msg_count} 条内容进行总结...'
        elif status == 'extracting_artifact':
            detail = f'正在提取「{group_name}」的结构化分析...'
        elif status == 'verifying_evidence':
            detail = f'正在校验「{group_name}」的证据来源...'
        elif status == 'rendering_report':
            detail = f'「{group_name}」发现 {msg_count} 个知识候选，正在生成报告...'
        else:
            detail = f'「{group_name}」{status}'
        push('progress', detail, current / max(total, 1) * 0.9 + 0.1)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

def _artifact_summary(storage: str, chat_id: str, date: str) -> dict:
    artifact = load_artifact(storage, chat_id, date)
    if not artifact:
        return {
            'artifact_status': 'missing',
            'knowledge_candidate_count': 0,
            'verify_status': 'missing',
            'verify_pass_rate': None,
        }
    candidate_count = 0
    for topic in artifact.get('topics') or []:
        candidate_count += len(topic.get('knowledge_candidates') or [])
    verify = artifact.get('verify') or {}
    total = int(verify.get('total_evidence') or 0)
    passed = int(verify.get('passed') or 0)
    return {
        'artifact_status': 'available',
        'knowledge_candidate_count': candidate_count,
        'verify_status': verify.get('status') or 'unverified',
        'verify_pass_rate': round(passed / total, 3) if total else None,
    }

@analysis_bp.route('/results', methods=['GET'])
def list_results():
    storage = _storage_dir()
    if not os.path.isdir(storage):
        return jsonify({'results': []})
    results = []
    for chat_dir in os.listdir(storage):
        chat_path = os.path.join(storage, chat_dir)
        if not os.path.isdir(chat_path):
            continue
        for fname in os.listdir(chat_path):
            if fname.endswith('.md'):
                date = fname[:-3]
                fpath = os.path.join(chat_path, fname)
                stat = os.stat(fpath)
                results.append({
                    'chat_id': chat_dir, 'date': date,
                    'size': stat.st_size, 'mtime': stat.st_mtime,
                    **_artifact_summary(storage, chat_dir, date),
                })
    results.sort(key=lambda r: r['date'], reverse=True)
    return jsonify({'results': results})


@analysis_bp.route('/result/<chat_id>/<date>', methods=['GET'])
def get_result(chat_id, date):
    path = ai_analyzer.result_path(_storage_dir(), chat_id, date)
    if not os.path.isfile(path):
        return jsonify({'error': 'not found'}), 404
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    name_map = {g['wxid']: g['display_name'] for g in _all_group_display_items()}
    content = ai_analyzer.sanitize_analysis_markdown(content, name_map.get(chat_id, chat_id))
    return jsonify({'content': content})


@analysis_bp.route('/result/<chat_id>/<date>/artifact', methods=['GET'])
def get_result_artifact(chat_id, date):
    artifact = load_artifact(_storage_dir(), chat_id, date)
    if not artifact:
        return jsonify({'ok': False, 'error': 'artifact not found'}), 404
    return jsonify({'ok': True, 'artifact': artifact})


@analysis_bp.route('/result/<chat_id>/<date>/knowledge-candidates/import', methods=['POST'])
def import_result_knowledge_candidates(chat_id, date):
    artifact = load_artifact(_storage_dir(), chat_id, date)
    if not artifact:
        return jsonify({'ok': False, 'error': 'artifact not found'}), 404
    data = request.get_json(silent=True) or {}
    min_score = int(data.get('min_score') or 80)
    cards = knowledge_extractor.cards_from_analysis_artifact(artifact, min_score=min_score)
    dbp = knowledge_store.knowledge_db_path(_decrypted_dir())
    card_ids = [knowledge_store.save_card(dbp, card) for card in cards]
    return jsonify({'ok': True, 'created': len(card_ids), 'card_ids': card_ids})


@analysis_bp.route('/result/<chat_id>/<date>', methods=['DELETE'])
def delete_result(chat_id, date):
    path = ai_analyzer.result_path(_storage_dir(), chat_id, date)
    if not os.path.isfile(path):
        return jsonify({'error': 'not found'}), 404
    try:
        os.remove(path)
        apath = artifact_path(_storage_dir(), chat_id, date)
        if os.path.isfile(apath):
            os.remove(apath)
        return jsonify({'deleted': True})
    except OSError as e:
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------

@analysis_bp.route('/schedules', methods=['GET'])
def list_schedules_api():
    return jsonify({'schedules': ai_analyzer.list_schedules(_config_path())})


@analysis_bp.route('/schedules', methods=['POST'])
def add_schedule_api():
    data = request.get_json(silent=True) or {}
    sid = ai_analyzer.add_schedule(data, _config_path())
    ai_scheduler.reload_schedules()
    return jsonify({'id': sid, 'ok': True})


@analysis_bp.route('/schedules/<schedule_id>', methods=['PUT'])
def update_schedule_api(schedule_id):
    data = request.get_json(silent=True) or {}
    ok = ai_analyzer.update_schedule(schedule_id, data, _config_path())
    if not ok:
        return jsonify({'error': 'not found'}), 404
    ai_scheduler.reload_schedules()
    return jsonify({'ok': True})


@analysis_bp.route('/schedules/<schedule_id>', methods=['DELETE'])
def delete_schedule_api(schedule_id):
    ok = ai_analyzer.delete_schedule(schedule_id, _config_path())
    if not ok:
        return jsonify({'error': 'not found'}), 404
    ai_scheduler.reload_schedules()
    return jsonify({'deleted': True})


# ---------------------------------------------------------------------------
# Tags (group management)
# ---------------------------------------------------------------------------

def _all_group_chat_ids(decrypted_dir: str = None) -> list:
    """Return all group chat_ids from the address book."""
    from engine.services.address_book import get_all_groups
    if decrypted_dir is None:
        decrypted_dir = _decrypted_dir()
    cached = _cached_group_items(decrypted_dir)
    if cached is not None:
        return [g['wxid'] for g in cached]
    groups = get_all_groups(decrypted_dir)
    return [g['wxid'] for g in groups if g.get('wxid', '').endswith('@chatroom')]


def _all_group_display_map(decrypted_dir: str = None) -> dict:
    """Return {display_name: wxid} for all groups."""
    from engine.services.address_book import get_all_groups
    if decrypted_dir is None:
        decrypted_dir = _decrypted_dir()
    groups = get_all_groups(decrypted_dir)
    return {g.get('display_name') or g['wxid']: g['wxid'] for g in groups if g.get('wxid', '').endswith('@chatroom')}


def _all_group_display_items(decrypted_dir: str = None) -> list:
    """Return [{'wxid', 'display_name'}] for all groups without collapsing duplicate names."""
    from engine.services.address_book import get_all_groups
    if decrypted_dir is None:
        decrypted_dir = _decrypted_dir()
    cached = _cached_group_items(decrypted_dir)
    if cached is not None:
        return cached
    groups = get_all_groups(decrypted_dir)
    return [
        {'wxid': g['wxid'], 'display_name': g.get('display_name') or g['wxid']}
        for g in groups
        if g.get('wxid', '').endswith('@chatroom')
    ]


@analysis_bp.route('/tags', methods=['GET'])
def get_tags():
    tags = ai_analyzer.load_tags(_config_path())
    all_chat_ids = _all_group_chat_ids()
    untagged_ids = ai_analyzer.compute_untagged_chat_ids(tags, all_chat_ids)
    id_to_name = {g['wxid']: g['display_name'] for g in _all_group_display_items()}
    untagged = [{'wxid': cid, 'display_name': id_to_name.get(cid, cid)}
                for cid in untagged_ids]
    return jsonify({'tags': tags, 'untagged': untagged})


@analysis_bp.route('/tags', methods=['PUT'])
def put_tags():
    data = request.get_json(silent=True) or {}
    tags = data.get('tags', [])
    if not isinstance(tags, list):
        return jsonify({'error': 'tags must be a list'}), 400
    ai_analyzer.save_tags(tags, _config_path())
    return jsonify({'ok': True})


@analysis_bp.route('/tags/auto-classify', methods=['POST'])
def auto_classify():
    """Run AI auto-classification. SSE stream with progress + final result."""
    push, gen = create_sse_progress()
    decrypted_dir = _decrypted_dir()
    cfg_path = _config_path()
    # Resolve group names BEFORE entering background thread (needs app context)
    group_items = _all_group_display_items(decrypted_dir)
    name_counts = {}
    for g in group_items:
        name_counts[g['display_name']] = name_counts.get(g['display_name'], 0) + 1
    name_map = {}
    for g in group_items:
        display = g['display_name']
        label = f'{display} [{g["wxid"]}]' if name_counts.get(display, 0) > 1 else display
        name_map[label] = g['wxid']

    def _run():
        try:
            def _cb(done, total):
                push('progress', f'正在分类 ({done}/{total})...', done / max(total, 1) * 0.9)
            push('progress', '开始 AI 分类...', 0.05)
            ai_results = ai_analyzer.auto_classify_groups(
                list(name_map.keys()), progress_cb=_cb,
                config_path=cfg_path,
            )
            tags = ai_analyzer.map_ai_results_to_tags(ai_results, name_map)
            push.done({'tags': tags})
        except Exception as e:
            push.error(str(e))

    threading.Thread(target=_run, daemon=True).start()
    return sse_response(gen)
