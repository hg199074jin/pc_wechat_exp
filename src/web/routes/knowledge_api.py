"""Knowledge Radar — REST API and SSE scan endpoint."""
from __future__ import annotations

import json
import os
import sys
import threading
import uuid

from flask import Blueprint, Response, current_app, jsonify, request

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from engine.services import knowledge_store as store
from engine.services import knowledge_extractor as extractor
from engine.services import knowledge_export as exporter
from engine.services.analysis_artifact import load_artifact
from engine.services.ai_analyzer import (
    config_path_for,
    load_tags,
    collect_tagged_chat_ids,
    period_label,
    storage_dir_for,
)
from engine.services.name_resolver import resolve_wxid
from web.sse import create_sse_progress, sse_response

knowledge_bp = Blueprint('knowledge_api', __name__, url_prefix='/api/knowledge')


def _db_path():
    decrypted_dir = current_app.config.get('DECRYPTED_DIR', '')
    return store.knowledge_db_path(decrypted_dir)


def _config_path():
    decrypted_dir = current_app.config.get('DECRYPTED_DIR', '')
    return config_path_for(decrypted_dir)


def _chat_tag_paths(tags: list) -> dict:
    """Return {chat_id: [tag_path, ...]} from the saved group tag tree."""
    result = {}

    def _walk(nodes, prefix=''):
        for node in nodes or []:
            name = node.get('name') or ''
            path = f'{prefix}/{name}' if prefix else name
            for cid in node.get('chat_ids') or []:
                result.setdefault(cid, []).append(path)
            _walk(node.get('children') or [], path)

    _walk(tags)
    return result


def _attach_card_tag_paths(cards: list) -> list:
    """Attach source group tag paths to cards for UI grouping/context."""
    tag_map = _chat_tag_paths(load_tags(_config_path()))
    for card in cards or []:
        paths = []
        seen = set()
        for cid in card.get('source_chat_ids') or []:
            for path in tag_map.get(cid, []):
                if path and path not in seen:
                    seen.add(path)
                    paths.append(path)
        card['tag_paths'] = paths
    return cards


def _clean_card_sources(card: dict) -> dict:
    """Clean source quotes for display/export without mutating DB schema."""
    for src in card.get('sources') or []:
        src['quote'] = extractor.readable_message_text(src.get('quote') or '')
    return card


def _list_cards_for_request(*, force_large: bool = False) -> dict:
    """List cards with common filters plus optional tag_path filtering."""
    tag_path = request.args.get('tag_path') or ''
    limit = request.args.get('limit', 100, type=int)
    offset = request.args.get('offset', 0, type=int)
    query_limit = 5000 if (tag_path or force_large) else limit
    result = store.list_cards(
        _db_path(),
        status=request.args.get('status'),
        card_type=request.args.get('type'),
        q=request.args.get('q'),
        min_score=request.args.get('min_score', type=int),
        date_from=request.args.get('date_from'),
        date_to=request.args.get('date_to'),
        chat_id=request.args.get('chat_id'),
        limit=query_limit,
        offset=0 if tag_path else offset,
    )
    cards = _attach_card_tag_paths(result.get('cards') or [])
    if tag_path:
        cards = [c for c in cards if tag_path in (c.get('tag_paths') or [])]
        result['total'] = len(cards)
        result['cards'] = cards[offset:offset + limit]
    else:
        result['cards'] = cards
    return result


# ---------------------------------------------------------------------------
# Card CRUD
# ---------------------------------------------------------------------------

@knowledge_bp.route('/cards')
def list_cards_api():
    result = _list_cards_for_request()
    return jsonify(result)


@knowledge_bp.route('/cards/<card_id>')
def get_card_api(card_id):
    card = store.get_card(_db_path(), card_id)
    if not card:
        return jsonify({'error': 'not found'}), 404
    _attach_card_tag_paths([card])
    _clean_card_sources(card)
    return jsonify(card)


@knowledge_bp.route('/cards/<card_id>', methods=['PUT'])
def update_card_api(card_id):
    data = request.get_json(silent=True) or {}
    if not store.update_card(_db_path(), card_id, data):
        return jsonify({'error': 'not found'}), 404
    return jsonify({'ok': True})


@knowledge_bp.route('/cards/<card_id>', methods=['DELETE'])
def delete_card_api(card_id):
    if not store.delete_card(_db_path(), card_id):
        return jsonify({'error': 'not found'}), 404
    return jsonify({'ok': True})


@knowledge_bp.route('/cards/bulk', methods=['POST'])
def bulk_cards_api():
    data = request.get_json(silent=True) or {}
    card_ids = data.get('card_ids') or []
    action = data.get('action') or ''
    tags = data.get('tags')
    if not card_ids or action not in ('inbox', 'saved', 'archived', 'rejected', 'delete', 'tag'):
        return jsonify({'error': 'card_ids and valid action required'}), 400
    count = store.bulk_update(_db_path(), card_ids, action, tags=tags)
    return jsonify({'ok': True, 'affected': count})


# ---------------------------------------------------------------------------
# Card convert
# ---------------------------------------------------------------------------

@knowledge_bp.route('/cards/<card_id>/convert', methods=['POST'])
def convert_card_api(card_id):
    data = request.get_json(silent=True) or {}
    target_type = data.get('target_type') or ''
    valid = {'audit_case', 'sop', 'prompt', 'faq', 'article', 'script'}
    if target_type not in valid:
        return jsonify({'error': f'target_type must be one of {sorted(valid)}'}), 400

    card = store.get_card(_db_path(), card_id)
    if not card:
        return jsonify({'error': 'not found'}), 404

    try:
        llm_call = extractor.make_llm_call(_config_path())
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 400

    try:
        system, user = extractor.build_convert_prompt(card, target_type)
        md = llm_call(system, user)
        # Update card with converted content
        store.update_card(_db_path(), card_id, {
            'content_md': md,
            'type': target_type,
        })
        return jsonify({'ok': True, 'content_md': md, 'type': target_type})
    except Exception as e:
        return jsonify({'error': f'转化失败: {e}'}), 500


# ---------------------------------------------------------------------------
# Source context
# ---------------------------------------------------------------------------

@knowledge_bp.route('/source/<card_id>')
def source_context_api(card_id):
    card = store.get_card(_db_path(), card_id)
    if not card:
        return jsonify({'error': 'not found'}), 404
    _clean_card_sources(card)
    return jsonify({'sources': card.get('sources', [])})


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@knowledge_bp.route('/stats')
def stats_api():
    return jsonify(store.get_stats(_db_path()))


# ---------------------------------------------------------------------------
# SSE scan
# ---------------------------------------------------------------------------

_run_lock = threading.Lock()


def _cards_from_existing_artifact(decrypted_dir: str, chat_id: str,
                                  label: str, min_score: int):
    """Return cards from an existing AI analysis artifact, or None if missing."""
    artifact = load_artifact(storage_dir_for(decrypted_dir), chat_id, label)
    if not artifact:
        return None
    return extractor.cards_from_analysis_artifact(artifact, min_score=min_score)


def _save_scan_cards(dbp: str, cards: list, chat_id: str, max_cards: int) -> int:
    """Persist scan cards and return the saved count."""
    saved = 0
    for card in cards[:max_cards]:
        for src in card.get('sources', []):
            src['chat_id'] = chat_id
        card['source_chat_ids'] = [chat_id]
        store.save_card(dbp, card)
        saved += 1
    return saved


def _knowledge_source_mode(data: dict) -> str:
    """Return the scan source mode. Default is quality-first LLM extraction."""
    value = (data or {}).get('knowledge_source') or 'llm'
    return value if value in {'llm', 'auto', 'artifact_only'} else 'llm'


@knowledge_bp.route('/run', methods=['POST'])
def run_knowledge_scan():
    data = request.get_json(silent=True) or {}
    chat_ids = data.get('chat_ids') or []
    date_range = data.get('date_range') or []
    min_score = int(data.get('min_score') or 70)
    domain = data.get('domain') or 'general'
    max_cards = int(data.get('max_cards') or 30)
    scan_mode = data.get('scan_mode') or 'range'
    knowledge_source = _knowledge_source_mode(data)

    if not chat_ids:
        return jsonify({'error': '请至少选择一个群聊'}), 400
    if len(date_range) != 2:
        return jsonify({'error': '请提供日期范围 (date_range)'}), 400

    decrypted_dir = current_app.config['DECRYPTED_DIR']
    wxid = current_app.config.get('WXID', '')
    dbp = store.knowledge_db_path(decrypted_dir)
    cfg_path = config_path_for(decrypted_dir)

    # Resolve chat names before entering thread
    chat_names = {}
    for cid in chat_ids:
        try:
            name = resolve_wxid(decrypted_dir, cid)
            chat_names[cid] = name or cid
        except Exception:
            chat_names[cid] = cid

    if not _run_lock.acquire(blocking=False):
        return jsonify({'error': '已有扫描任务在运行，请等待完成'}), 409

    push, gen = create_sse_progress()

    def _run():
        run_id = store.create_run(dbp, date_range[0], date_range[1], chat_ids)
        total_msgs = 0
        total_candidates = 0
        total_cards = 0
        reused_artifacts = 0
        llm_call = None

        def get_llm_call():
            nonlocal llm_call
            if llm_call is None:
                llm_call = extractor.make_llm_call(cfg_path)
            return llm_call

        try:
            from datetime import datetime, timedelta
            d0 = datetime.strptime(date_range[0], '%Y-%m-%d')
            d1 = datetime.strptime(date_range[1], '%Y-%m-%d')
            if d0 > d1:
                d0, d1 = d1, d0

            if scan_mode == 'daily':
                dates = []
                cur = d0
                while cur <= d1:
                    dates.append(cur.strftime('%Y-%m-%d'))
                    cur += timedelta(days=1)

                total_steps = len(chat_ids) * len(dates)
                step = 0

                for date in dates:
                    for cid in chat_ids:
                        step += 1
                        cname = chat_names.get(cid, cid)
                        push('progress', f'扫描 {cname} ({date}) [{step}/{total_steps}]', step / max(total_steps, 1))

                        if knowledge_source != 'llm':
                            artifact_cards = _cards_from_existing_artifact(
                                decrypted_dir, cid, date, min_score
                            )
                            if artifact_cards is not None:
                                reused_artifacts += 1
                                total_candidates += len(artifact_cards)
                                total_cards += _save_scan_cards(dbp, artifact_cards, cid, max_cards)
                                push('progress', f'复用 AI 分析结果 {cname} ({date}): {len(artifact_cards)} 条知识',
                                     step / max(total_steps, 1))
                                continue
                            if knowledge_source == 'artifact_only':
                                continue

                        messages = extractor.load_messages_for_scan(decrypted_dir, cid, date, wxid=wxid)
                        total_msgs += len(messages)

                        if not messages:
                            continue

                        cards = extractor.extract_cards_from_messages(
                            messages, cname, date, get_llm_call(),
                            min_score=min_score, domain=domain,
                        )
                        total_candidates += len(cards)
                        total_cards += _save_scan_cards(dbp, cards, cid, max_cards)

                        push('progress', f'完成 {cname} ({date}): {len(cards)} 条知识',
                             step / max(total_steps, 1))
            else:
                start_str = d0.strftime('%Y-%m-%d')
                end_str = d1.strftime('%Y-%m-%d')
                label = period_label(start_str, end_str)
                total_steps = len(chat_ids)

                for step, cid in enumerate(chat_ids, 1):
                    cname = chat_names.get(cid, cid)
                    push('progress', f'扫描 {cname} ({start_str} ~ {end_str}) [{step}/{total_steps}]',
                         ((step - 1) / max(total_steps, 1)) * 0.8 + 0.1)

                    if knowledge_source != 'llm':
                        artifact_cards = _cards_from_existing_artifact(
                            decrypted_dir, cid, label, min_score
                        )
                        if artifact_cards is not None:
                            reused_artifacts += 1
                            total_candidates += len(artifact_cards)
                            total_cards += _save_scan_cards(dbp, artifact_cards, cid, max_cards)
                            push('progress', f'复用 AI 分析结果 {cname} ({start_str} ~ {end_str}): {len(artifact_cards)} 条知识',
                                 step / max(total_steps, 1))
                            continue
                        if knowledge_source == 'artifact_only':
                            continue

                    messages = extractor.load_messages_for_scan(
                        decrypted_dir, cid, start_str, wxid=wxid, end_date=end_str
                    )
                    total_msgs += len(messages)

                    if not messages:
                        continue

                    cards = extractor.extract_cards_from_messages_chunked(
                        messages, cname, label, get_llm_call(),
                        min_score=min_score, domain=domain,
                    )
                    total_candidates += len(cards)
                    total_cards += _save_scan_cards(dbp, cards, cid, max_cards)

                    push('progress', f'完成 {cname} ({start_str} ~ {end_str}): {len(cards)} 条知识',
                         step / max(total_steps, 1))

            store.finish_run(dbp, run_id, status='done',
                             total_messages=total_msgs,
                             candidate_count=total_candidates,
                             card_count=total_cards)
            push.done({
                'run_id': run_id,
                'total_messages': total_msgs,
                'candidate_count': total_candidates,
                'card_count': total_cards,
                'reused_artifacts': reused_artifacts,
            })
        except Exception as e:
            store.finish_run(dbp, run_id, status='error', error=str(e))
            push.error(str(e))
        finally:
            _run_lock.release()

    threading.Thread(target=_run, daemon=True).start()
    return sse_response(gen)


# ---------------------------------------------------------------------------
# Scan history
# ---------------------------------------------------------------------------

@knowledge_bp.route('/runs')
def list_runs_api():
    return jsonify({'runs': store.list_runs(_db_path())})


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@knowledge_bp.route('/export')
def export_cards_api():
    fmt = request.args.get('format', 'md')
    ids_param = request.args.get('ids', '')
    status = request.args.get('status')
    min_score = request.args.get('min_score', type=int)

    dbp = _db_path()

    if ids_param:
        card_ids = [i.strip() for i in ids_param.split(',') if i.strip()]
        cards = []
        for cid in card_ids:
            card = store.get_card(dbp, cid)
            if card:
                cards.append(card)
    else:
        result = _list_cards_for_request(force_large=True)
        cards = result.get('cards', [])

    for card in cards:
        _clean_card_sources(card)

    if not cards:
        return jsonify({'error': '没有可导出的卡片'}), 400

    if fmt == 'md':
        content = exporter.cards_to_markdown(cards)
        return Response(
            content,
            content_type='text/markdown; charset=utf-8',
            headers={'Content-Disposition': 'attachment; filename=knowledge_export.md'},
        )
    elif fmt == 'docx':
        try:
            data = exporter.cards_to_docx(cards)
            return Response(
                data,
                content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                headers={'Content-Disposition': 'attachment; filename=knowledge_export.docx'},
            )
        except ImportError as e:
            return jsonify({'error': str(e)}), 400
    else:
        return jsonify({'error': f'不支持的格式: {fmt}'}), 400


# ---------------------------------------------------------------------------
# Schedule CRUD
# ---------------------------------------------------------------------------

def _load_knowledge_schedules(config_path: str) -> list:
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        return cfg.get('knowledge_schedules', [])
    except Exception:
        return []


def _save_knowledge_schedules(schedules: list, config_path: str) -> None:
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    cfg['knowledge_schedules'] = schedules
    os.makedirs(os.path.dirname(config_path) or '.', exist_ok=True)
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


@knowledge_bp.route('/schedules', methods=['GET'])
def list_schedules_api():
    return jsonify({'schedules': _load_knowledge_schedules(_config_path())})


@knowledge_bp.route('/schedules', methods=['POST'])
def add_schedule_api():
    data = request.get_json(silent=True) or {}
    data.setdefault('id', str(uuid.uuid4()))
    data.setdefault('enabled', True)
    cfg_path = _config_path()
    schedules = _load_knowledge_schedules(cfg_path)
    schedules.append(data)
    _save_knowledge_schedules(schedules, cfg_path)
    # Reload scheduler
    try:
        from engine.services import knowledge_scheduler
        knowledge_scheduler.reload_schedules()
    except Exception:
        pass
    return jsonify({'ok': True, 'schedule': data})


@knowledge_bp.route('/schedules/<sched_id>', methods=['PUT'])
def update_schedule_api(sched_id):
    data = request.get_json(silent=True) or {}
    cfg_path = _config_path()
    schedules = _load_knowledge_schedules(cfg_path)
    for s in schedules:
        if s.get('id') == sched_id:
            s.update(data)
            _save_knowledge_schedules(schedules, cfg_path)
            try:
                from engine.services import knowledge_scheduler
                knowledge_scheduler.reload_schedules()
            except Exception:
                pass
            return jsonify({'ok': True, 'schedule': s})
    return jsonify({'error': 'not found'}), 404


@knowledge_bp.route('/schedules/<sched_id>', methods=['DELETE'])
def delete_schedule_api(sched_id):
    cfg_path = _config_path()
    schedules = _load_knowledge_schedules(cfg_path)
    schedules = [s for s in schedules if s.get('id') != sched_id]
    _save_knowledge_schedules(schedules, cfg_path)
    try:
        from engine.services import knowledge_scheduler
        knowledge_scheduler.reload_schedules()
    except Exception:
        pass
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# Tags (read-only, reuse from ai_analyzer)
# ---------------------------------------------------------------------------

@knowledge_bp.route('/tags')
def list_tags_api():
    tags = load_tags(_config_path())
    return jsonify({'tags': tags})


@knowledge_bp.route('/tag-chat-ids', methods=['POST'])
def tag_chat_ids_api():
    """Resolve tag_paths to chat_ids."""
    data = request.get_json(silent=True) or {}
    tag_paths = data.get('tag_paths') or []
    tags = load_tags(_config_path())
    # Flatten tag tree and match paths
    all_chat_ids = set()

    def _walk(nodes, prefix=''):
        for node in nodes:
            path = f"{prefix}/{node['name']}" if prefix else node['name']
            if path in tag_paths:
                for cid in (node.get('chat_ids') or []):
                    all_chat_ids.add(cid)
            _walk(node.get('children') or [], path)

    _walk(tags)
    return jsonify({'chat_ids': sorted(all_chat_ids)})
