"""Shared HardLink path resolution utilities.

Consolidates duplicate hardlink/storage logic from media.py and v2_key_extract.py.
"""
import os
import sqlite3

# Fallback storage roots when hardlink.db doesn't provide a path
FALLBACK_STORAGE_ROOTS = [
    r'D:\xwechat_files',
    r'C:\xwechat_files',
    r'D:\WeChat Files',
    r'C:\WeChat Files',
]


def get_base_storage(decrypted_dir: str):
    """Get the original WeChat file storage root from hardlink.db's db_info table.

    Returns the storage root path if found and exists, else None.
    """
    hardlink_db = os.path.join(decrypted_dir, "hardlink", "hardlink.db")
    if not os.path.isfile(hardlink_db):
        hardlink_db = os.path.join(decrypted_dir, "HardLink", "hardlink.db")
    if not os.path.isfile(hardlink_db):
        return None
    conn = None
    try:
        conn = sqlite3.connect(hardlink_db)
        row = conn.execute(
            "SELECT ValueStdStr FROM db_info WHERE Key='uuid'"
        ).fetchone()
        conn.close()
        conn = None
        if row and row[0]:
            parts = str(row[0]).split('_', 2)
            if len(parts) >= 3:
                storage_path = parts[-1]
                if os.path.isdir(storage_path):
                    return storage_path
    except sqlite3.Error:
        pass
    finally:
        if conn:
            conn.close()
    return None


def resolve_from_hardlink_db(decrypted_dir: str, md5: str, media_type: int) -> str:
    """Look up a file in the HardLink DB by md5 and return its relative path.

    Args:
        decrypted_dir: Path to the decrypted data directory.
        md5: The CDN md5 hash to look up.
        media_type: WeChat media type (3=image, 43=video, 6=file, 34=voice).

    Returns:
        Relative path string (e.g. 'msg/attach/{dir1}/{dir2}/Img/{fname}')
        or None if not found.
    """
    hardlink_db = os.path.join(decrypted_dir, "hardlink", "hardlink.db")
    if not os.path.isfile(hardlink_db):
        hardlink_db = os.path.join(decrypted_dir, "HardLink", "hardlink.db")
    if not os.path.isfile(hardlink_db):
        return None

    table_map = {3: 'image', 43: 'video', 6: 'file', 34: 'voice'}
    table_suffix = table_map.get(media_type, 'image')
    table_name = f'{table_suffix}_hardlink_info_v4'

    conn = None
    try:
        conn = sqlite3.connect(hardlink_db)
        # Prefer original (.dat) over thumbnails (_h.dat, _t.dat) when the
        # same CDN md5 maps to multiple rows in the HardLink DB.
        rows = conn.execute(
            f"SELECT file_name, dir1, dir2 FROM [{table_name}] WHERE md5=? "
            f"ORDER BY CASE WHEN substr(file_name, -6)='_h.dat' THEN 2 "
            f"WHEN substr(file_name, -6)='_t.dat' THEN 3 ELSE 1 END",
            (md5,)
        ).fetchall()
        if not rows:
            return None

        file_name, dir1, dir2 = rows[0]
        dir1_name = None
        dir2_name = None
        if dir2:
            d2 = conn.execute("SELECT * FROM dir2id WHERE rowid=?", (dir2,)).fetchone()
            dir2_name = d2[0] if d2 else None
        if dir1:
            d1 = conn.execute("SELECT * FROM dir2id WHERE rowid=?", (dir1,)).fetchone()
            dir1_name = d1[0] if d1 else None

        if media_type == 3:  # Image
            if dir1_name and dir2_name:
                return f'msg/attach/{dir1_name}/{dir2_name}/Img/{file_name}'
        elif media_type == 43:  # Video
            if dir1_name:
                return f'msg/video/{dir1_name}/{file_name}'
        elif media_type == 6:  # File
            if dir1_name:
                return f'msg/file/{dir1_name}/{file_name}'

        return None
    except sqlite3.Error:
        return None
    finally:
        if conn:
            conn.close()


def try_resolve_path(decrypted_dir: str, rel_path: str, wxid: str) -> str:
    """Try to resolve a relative path against all known storage roots.

    Uses get_base_storage() first, then falls back to FALLBACK_STORAGE_ROOTS.
    Includes path traversal prevention via realpath + commonpath check.

    Args:
        decrypted_dir: Path to the decrypted data directory.
        rel_path: Relative path (forward slashes OK, will be converted).
        wxid: WeChat user ID to prefix to the path.

    Returns:
        Resolved absolute path if file exists and passes traversal check,
        else None.
    """
    if not rel_path:
        return None
    rel_path = rel_path.replace('/', os.sep)
    storage_roots = []
    base = get_base_storage(decrypted_dir)
    if base:
        storage_roots.append(base)
    for sr in FALLBACK_STORAGE_ROOTS:
        if os.path.isdir(sr) and sr not in storage_roots:
            storage_roots.append(sr)
    for root in storage_roots:
        for wd in [wxid, '']:
            if not wd:
                continue
            candidate = os.path.join(root, wd, rel_path)
            try:
                real = os.path.realpath(candidate)
            except (OSError, ValueError):
                continue
            if not os.path.isfile(real):
                continue
            # Containment check — prevent path traversal
            expected_parent = os.path.realpath(os.path.join(root, wd))
            try:
                if os.path.commonpath([real, expected_parent]) != expected_parent:
                    continue
            except ValueError:
                continue
            return real
    return None


def get_all_storage_roots(decrypted_dir: str) -> list:
    """Get all storage roots (base + fallbacks) as a deduplicated list.

    Returns paths that exist on disk.
    """
    roots = []
    base = get_base_storage(decrypted_dir)
    if base:
        roots.append(base)
    for sr in FALLBACK_STORAGE_ROOTS:
        if os.path.isdir(sr) and sr not in roots:
            roots.append(sr)
    return roots
