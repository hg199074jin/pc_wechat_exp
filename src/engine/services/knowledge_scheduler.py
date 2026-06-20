"""Knowledge Radar — APScheduler integration for daily knowledge scans."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
_current_config_path: str = ''
_current_decrypted_dir: str = ''


def start_scheduler(config_path: str, decrypted_dir: str) -> None:
    """Start or restart the knowledge scan scheduler from config."""
    global _scheduler, _current_config_path, _current_decrypted_dir
    _current_config_path = config_path
    _current_decrypted_dir = decrypted_dir

    stop_scheduler()

    schedules = _load_schedules(config_path)
    if not schedules:
        return

    _scheduler = BackgroundScheduler(daemon=True)
    for sched in schedules:
        if not sched.get('enabled', True):
            continue
        _register_job(_scheduler, sched, config_path, decrypted_dir)

    _scheduler.start()


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None


def reload_schedules() -> None:
    if _current_config_path:
        start_scheduler(_current_config_path, _current_decrypted_dir)


def _load_schedules(config_path: str) -> list:
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        return cfg.get('knowledge_schedules', [])
    except Exception as e:
        logger.warning("Failed to load knowledge schedules from %s: %s", config_path, e)
        return []


def _register_job(sched: BackgroundScheduler, job: dict, config_path: str, decrypted_dir: str) -> None:
    time_str = job.get('time') or '08:00'
    try:
        parts = time_str.split(':')
        hour = int(parts[0]) if len(parts) >= 1 else 8
        minute = int(parts[1]) if len(parts) >= 2 else 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"Time out of range: {time_str}")
    except (ValueError, IndexError) as e:
        logger.warning("Invalid time '%s' for job '%s', using 08:00: %s",
                       time_str, job.get('name', '?'), e)
        hour, minute = 8, 0

    now = datetime.now()
    first_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if first_run <= now:
        first_run += timedelta(days=1)

    sched.add_job(
        _run_for_job,
        trigger=IntervalTrigger(days=1, start_date=first_run, misfire_grace_time=3600),
        args=[job, config_path, decrypted_dir],
        id=f"knowledge_scan_{job.get('id', 'unknown')}",
        name=f"知识雷达: {job.get('name', '未命名')}",
        replace_existing=True,
    )


def _run_for_job(job: dict, config_path: str, decrypted_dir: str) -> None:
    """Execute a scheduled knowledge scan."""
    from engine.services import knowledge_extractor as extractor
    from engine.services import knowledge_store as store
    from engine.services.ai_analyzer import load_tags
    from engine.services.name_resolver import resolve_wxid

    try:
        llm_call = extractor.make_llm_call(config_path)
    except RuntimeError as e:
        logger.error("[知识雷达] LLM 配置错误: %s", e)
        return

    # Determine chat_ids
    chat_ids = list(job.get('chat_ids') or [])
    tag_paths = job.get('tag_paths') or []
    if tag_paths:
        try:
            tags = load_tags(config_path)
            _resolve_tag_paths(tags, tag_paths, chat_ids)
        except Exception:
            pass

    if not chat_ids:
        logger.warning("[知识雷达] 没有配置扫描群聊，跳过")
        return

    # Yesterday
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    min_score = int(job.get('min_score') or 70)
    max_cards = int(job.get('max_cards') or 30)
    domain = job.get('domain') or 'general'

    dbp = store.knowledge_db_path(decrypted_dir)
    run_id = store.create_run(dbp, yesterday, yesterday, chat_ids)

    total_msgs = 0
    total_cards = 0
    errors = []

    for cid in chat_ids:
        try:
            name = resolve_wxid(decrypted_dir, cid)
            chat_name = name or cid
        except Exception:
            chat_name = cid

        messages = extractor.load_messages_for_scan(decrypted_dir, cid, yesterday)
        total_msgs += len(messages)
        if not messages:
            continue

        def _error_cb(msg):
            errors.append(f"[{chat_name}] {msg}")
            logger.warning("Knowledge extraction error for %s: %s", chat_name, msg)

        cards = extractor.extract_cards_from_messages_chunked(
            messages, chat_name, yesterday, llm_call,
            min_score=min_score, domain=domain,
            error_cb=_error_cb,
        )
        for card in cards[:max_cards]:
            for src in card.get('sources', []):
                src['chat_id'] = cid
            card['source_chat_ids'] = [cid]
            store.save_card(dbp, card)
            total_cards += 1

    status = 'done' if not errors else 'partial'
    store.finish_run(dbp, run_id, status=status,
                     total_messages=total_msgs, card_count=total_cards,
                     error='; '.join(errors[-3:]) if errors else None)
    logger.info("知识雷达完成: %d 条知识卡片 (来自 %d 条消息, %d 个错误)",
                total_cards, total_msgs, len(errors))

    # Best-effort Obsidian sync after a scan (new cards just landed).
    _sync_obsidian_safely(decrypted_dir)


def run_obsidian_sync(decrypted_dir: str) -> dict:
    """Pull all knowledge cards and sync them into the configured Obsidian vault.

    Returns the sync result dict (written/skipped/errors). No-op (returns zeros)
    when the vault path is not configured.
    """
    from engine.config_file import get_obsidian_vault_path
    from engine.services.obsidian_export import sync_cards_to_vault

    vault_path = get_obsidian_vault_path()
    if not vault_path:
        return {'written': 0, 'skipped': 0, 'errors': [], 'skipped_no_vault': True}

    from engine.services import knowledge_store as store
    dbp = store.knowledge_db_path(decrypted_dir)
    # Fetch full cards (with sources) for [[group]] wikilinks.
    listing = store.list_cards(dbp, limit=5000)
    cards = []
    for slim in listing.get('cards') or []:
        complete = store.get_card(dbp, slim.get('id') or '')
        cards.append(complete or slim)
    return sync_cards_to_vault(cards, vault_path, decrypted_dir=decrypted_dir)


def _sync_obsidian_safely(decrypted_dir: str) -> None:
    """Run an Obsidian sync, swallowing errors so a scan job never fails late."""
    try:
        result = run_obsidian_sync(decrypted_dir)
        if result.get('skipped_no_vault'):
            return
        logger.info("[知识雷达] Obsidian 同步: 写入 %d，跳过 %d",
                    result.get('written', 0), result.get('skipped', 0))
    except Exception as e:
        logger.error("[知识雷达] Obsidian 同步失败: %s", e)


def _resolve_tag_paths(tags: list, tag_paths: list, chat_ids: list) -> None:
    """Recursively resolve tag_paths to chat_ids."""
    def _walk(nodes, prefix=''):
        for node in nodes:
            path = f"{prefix}/{node['name']}" if prefix else node['name']
            if path in tag_paths:
                for cid in (node.get('chat_ids') or []):
                    if cid not in chat_ids:
                        chat_ids.append(cid)
            _walk(node.get('children') or [], path)
    _walk(tags)
