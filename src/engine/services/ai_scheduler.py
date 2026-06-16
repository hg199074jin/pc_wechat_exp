"""APScheduler integration for daily AI analysis jobs.

Exposes start_scheduler(config_path, decrypted_dir) which loads schedules
from config and registers them with a BackgroundScheduler. The Flask app
calls this once at startup.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler

from engine.services import ai_analyzer

_scheduler: Optional[BackgroundScheduler] = None
_current_config: Optional[str] = None
_current_decrypted_dir: Optional[str] = None


def _today() -> datetime:
    return datetime.now()


def _next_run_time(hhmm: str, now: datetime = None) -> datetime:
    """Compute next datetime matching HH:MM, strictly after `now`."""
    now = now or datetime.now()
    h, m = hhmm.split(':')
    target = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def _yesterday_str(now: datetime = None) -> str:
    now = now or datetime.now()
    return (now - timedelta(days=1)).strftime('%Y-%m-%d')


def _resolve_group_names(decrypted_dir: str, chat_ids: list) -> dict:
    """Resolve display names for chat_ids using existing name resolver."""
    from engine.services.name_resolver import resolve_wxid
    return {cid: resolve_wxid(decrypted_dir, cid) or cid for cid in chat_ids}


def _run_for_schedules(decrypted_dir: str, date: str,
                       config_path: str) -> list:
    """Execute analysis for all enabled schedules (used by daily trigger)."""
    schedules = ai_analyzer.list_schedules(config_path)
    results = []
    for sched in schedules:
        if not sched.get('enabled'):
            continue
        chat_ids = sched.get('chat_ids', [])
        if not chat_ids:
            continue
        group_names = _resolve_group_names(decrypted_dir, chat_ids)
        per_results = ai_analyzer.analyze_multiple(
            decrypted_dir, chat_ids, group_names,
            date, wxid=None, progress_cb=None,
        )
        results.extend(per_results)
    return results


def trigger_due_jobs(config_path: str, decrypted_dir: str) -> list:
    """Trigger all enabled schedules (one-shot). Used by tests and manual trigger."""
    if not os.path.isfile(config_path):
        return []
    date = _yesterday_str(_today())
    return _run_for_schedules(decrypted_dir, date, config_path)


def _schedule_job(sched: dict, config_path: str, decrypted_dir: str):
    """Register a single schedule with the BackgroundScheduler."""
    global _scheduler
    if _scheduler is None:
        return
    hhmm = sched.get('time', '08:00')
    job_id = f'ai_analysis_{sched["id"]}'
    try:
        _scheduler.remove_job(job_id)
    except Exception:
        pass
    run_at = _next_run_time(hhmm)
    _scheduler.add_job(
        func=_run_for_schedules,
        trigger='interval',
        days=1,
        start_date=run_at,
        args=[decrypted_dir, _yesterday_str(), config_path],
        id=job_id,
        replace_existing=True,
        misfire_grace_time=3600,
    )


def start_scheduler(config_path: str, decrypted_dir: str) -> None:
    """Start the BackgroundScheduler and register all enabled schedules."""
    global _scheduler, _current_config, _current_decrypted_dir
    stop_scheduler()
    _current_config = config_path
    _current_decrypted_dir = decrypted_dir
    _scheduler = BackgroundScheduler(daemon=True)
    if not os.path.isfile(config_path):
        _scheduler.start()
        return
    schedules = ai_analyzer.list_schedules(config_path)
    for sched in schedules:
        if sched.get('enabled'):
            _schedule_job(sched, config_path, decrypted_dir)
    _scheduler.start()


def stop_scheduler() -> None:
    """Stop the BackgroundScheduler if running."""
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None


def reload_schedules() -> None:
    """Reload all schedules from config (call after schedule CRUD)."""
    if _scheduler is None or _current_config is None or _current_decrypted_dir is None:
        return
    start_scheduler(_current_config, _current_decrypted_dir)
