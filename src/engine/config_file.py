"""Persistent config file for tracking backup output path across sessions."""
import json
import os

CONFIG_FILENAME = ".wechat_exp_config.json"


def _config_path() -> str:
    """Config file lives at project root (dev) or next to the exe (frozen)."""
    import sys as _sys
    if getattr(_sys, 'frozen', False):
        base = os.path.dirname(_sys.executable)
    else:
        # config_file.py → engine/ → src/ → project_root/
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, CONFIG_FILENAME)


def _read_config() -> dict:
    path = _config_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return {}


def _write_config(cfg: dict) -> None:
    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def get_default_backup_root() -> str | None:
    """Return the user's preferred backup output root, if configured."""
    raw = _read_config().get("default_backup_root", "")
    return str(raw) if raw else None


def set_default_backup_root(path: str) -> None:
    """Persist the preferred backup output root in the ignored local config."""
    cfg = _read_config()
    cfg["default_backup_root"] = str(path)
    _write_config(cfg)


def get_obsidian_vault_path() -> str | None:
    """Return the configured Obsidian vault path for knowledge export, if any."""
    raw = _read_config().get("obsidian_vault_path", "")
    return str(raw) if raw else None


def set_obsidian_vault_path(path: str) -> None:
    """Persist the Obsidian vault path in the ignored local config."""
    cfg = _read_config()
    cfg["obsidian_vault_path"] = str(path or "")
    _write_config(cfg)


def get_llm_config() -> dict:
    """Return private LLM config from the ignored local config."""
    llm = _read_config().get("llm", {})
    return llm if isinstance(llm, dict) else {}


def set_llm_config(llm_cfg: dict) -> None:
    """Persist private LLM config in the ignored local config."""
    cfg = _read_config()
    cfg["llm"] = llm_cfg or {}
    _write_config(cfg)


def get_group_blacklist() -> list:
    """Return blacklisted group records from the ignored local config."""
    items = _read_config().get("group_blacklist", [])
    if not isinstance(items, list):
        return []
    normalized = []
    seen = set()
    for item in items:
        if isinstance(item, str):
            wxid = item.strip()
            record = {"wxid": wxid, "display_name": wxid}
        elif isinstance(item, dict):
            wxid = str(item.get("wxid", "")).strip()
            record = {
                "wxid": wxid,
                "display_name": str(item.get("display_name") or wxid),
                "added_at": item.get("added_at", ""),
            }
        else:
            continue
        if not wxid or wxid in seen:
            continue
        seen.add(wxid)
        normalized.append(record)
    return normalized


def set_group_blacklist(items: list) -> None:
    """Persist blacklisted group records in the ignored local config."""
    normalized = []
    seen = set()
    for item in items or []:
        if isinstance(item, str):
            wxid = item.strip()
            display_name = wxid
            added_at = ""
        elif isinstance(item, dict):
            wxid = str(item.get("wxid", "")).strip()
            display_name = str(item.get("display_name") or wxid)
            added_at = str(item.get("added_at") or "")
        else:
            continue
        if not wxid or wxid in seen:
            continue
        seen.add(wxid)
        record = {"wxid": wxid, "display_name": display_name}
        if added_at:
            record["added_at"] = added_at
        normalized.append(record)
    cfg = _read_config()
    cfg["group_blacklist"] = normalized
    _write_config(cfg)


def get_backup_data_dir() -> str | None:
    """Return the output directory from the last successful backup, or None."""
    path = _config_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        raw = cfg.get("last_backup_data_dir", "")
        if raw and os.path.isdir(raw):
            return raw
    except (ValueError, OSError):
        pass
    return None


def set_backup_data_dir(data_dir: str, wxid: str | None = None) -> None:
    """Persist the backup data directory for other features to find."""
    import logging
    logger = logging.getLogger(__name__)
    if data_dir and not os.path.isdir(data_dir):
        logger.warning("Backup data directory does not exist: %s", data_dir)
    path = _config_path()
    cfg = {}
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
    except (ValueError, OSError):
        pass
    cfg["last_backup_data_dir"] = str(data_dir)
    if wxid:
        cfg["last_backup_wxid"] = str(wxid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def get_backup_wxid() -> str | None:
    """Return the wxid from the last successful backup, or None."""
    path = _config_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        wxid = cfg.get("last_backup_wxid", "")
        if wxid:
            return str(wxid)
    except (ValueError, OSError):
        pass
    return None


def get_latest_backup_dir(base_dir: str) -> str | None:
    """Scan a base backup directory and return the latest backup dir with message/ subdir."""
    if not os.path.isdir(base_dir):
        return None
    candidates = []
    for name in os.listdir(base_dir):
        full = os.path.join(base_dir, name)
        if not os.path.isdir(full):
            continue
        if os.path.isdir(os.path.join(full, "message")):
            candidates.append((name, full))
    if not candidates:
        return None
    candidates.sort(key=lambda x: os.path.getmtime(x[1]), reverse=True)
    return candidates[0][1]


def _get_all_keys_path() -> str:
    """Legacy all_keys.json path — used for one-time migration."""
    import sys as _sys
    if getattr(_sys, 'frozen', False):
        base = os.path.dirname(_sys.executable)
        return os.path.join(base, 'output', 'all_keys.json')
    else:
        # config_file.py → engine/ → src/ → project_root/
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(base, 'output', 'all_keys.json')


def _migrate_all_keys(cfg: dict, config_path: str) -> dict:
    """One-time: load keys from legacy output/all_keys.json and persist them in config."""
    legacy_path = _get_all_keys_path()
    if not os.path.isfile(legacy_path):
        return {}
    try:
        with open(legacy_path, 'r', encoding='utf-8') as f:
            legacy = json.load(f)
    except (ValueError, OSError):
        return {}

    keys = {}
    db_dir = ''
    for k, v in legacy.items():
        if k.startswith('_'):
            if k == '_db_dir':
                db_dir = str(v)
            continue
        if isinstance(v, dict):
            hex_key = v.get('enc_key', '')
            if hex_key and len(hex_key) == 64:
                keys[k] = hex_key
        elif isinstance(v, str) and len(v) == 64:
            keys[k] = v

    if not keys:
        return {}

    cfg['db_keys'] = keys
    if db_dir:
        cfg['_db_dir'] = db_dir
    try:
        tmp = config_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, config_path)
    except OSError:
        pass
    return keys


def get_db_keys() -> dict:
    """Return database encryption keys from config.

    Returns dict mapping db_rel_path -> 64-char hex enc_key.
    On first call, migrates keys from legacy output/all_keys.json.
    """
    path = _config_path()
    if not os.path.isfile(path):
        # Try migration before creating empty config
        return _migrate_all_keys({}, path)

    try:
        with open(path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except (ValueError, OSError):
        return {}

    keys = cfg.get('db_keys', {})
    if not keys:
        keys = _migrate_all_keys(cfg, path)
    return keys


def set_db_keys(keys: dict, db_dir: str = '') -> None:
    """Persist database encryption keys in the unified config file.

    Args:
        keys: dict mapping db_rel_path -> 64-char hex enc_key
        db_dir: absolute path to WeChat db_storage directory
    """
    import logging
    logger = logging.getLogger(__name__)
    path = _config_path()
    cfg = {}
    try:
        if os.path.isfile(path):
            with open(path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
    except (ValueError, OSError):
        pass
    # Validate keys are 64-char hex strings
    valid_keys = {}
    for k, v in keys.items():
        sv = str(v)
        if len(sv) == 64 and all(c in '0123456789abcdefABCDEF' for c in sv):
            valid_keys[str(k)] = sv
        else:
            logger.warning("Skipping invalid key for %s (not 64-char hex)", k)
    # Merge with existing keys so cold-shard keys from prior runs are not lost
    existing = cfg.get('db_keys', {})
    existing.update(valid_keys)
    cfg['db_keys'] = existing
    if db_dir:
        cfg['_db_dir'] = str(db_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def get_db_dir() -> str | None:
    """Return the _db_dir (WeChat db_storage path) stored in config, or None."""
    path = _config_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        val = cfg.get('_db_dir', '')
        return val if val else None
    except (ValueError, OSError):
        return None


def export_config(export_path: str) -> bool:
    """Export current config to a file (without sensitive data like API keys)."""
    import logging
    logger = logging.getLogger(__name__)
    cfg = _read_config()
    # Remove sensitive fields
    safe = {k: v for k, v in cfg.items() if k not in ('llm', 'db_keys')}
    try:
        with open(export_path, 'w', encoding='utf-8') as f:
            json.dump(safe, f, ensure_ascii=False, indent=2)
        return True
    except OSError as e:
        logger.warning("Failed to export config: %s", e)
        return False


def import_config(import_path: str) -> bool:
    """Import config from a file, merging with existing config."""
    import logging
    logger = logging.getLogger(__name__)
    try:
        with open(import_path, 'r', encoding='utf-8') as f:
            imported = json.load(f)
    except (ValueError, OSError) as e:
        logger.warning("Failed to read import file: %s", e)
        return False

    existing = _read_config()
    # Only import non-sensitive, known keys
    safe_keys = {
        'default_backup_root', 'obsidian_vault_path',
        'group_blacklist', 'last_backup_data_dir', 'last_backup_wxid',
        'knowledge_schedules', 'tags', 'knowledge_tags',
    }
    for key in safe_keys:
        if key in imported:
            existing[key] = imported[key]

    _write_config(existing)
    return True
