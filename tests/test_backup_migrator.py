"""Regression tests for image-only backup media migration."""
import os
import sqlite3
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from backup import migrator
from engine.services.media import _resolve_hardlink_path


def test_migrate_media_keeps_only_images(monkeypatch, tmp_path):
    """Backup migration must not duplicate files, videos, or voice messages."""
    output_dir = tmp_path / 'backup'
    message_dir = output_dir / 'message'
    message_dir.mkdir(parents=True)
    (message_dir / 'message_0.db').touch()

    source_dir = tmp_path / 'wechat' / 'msg'
    source_dir.mkdir(parents=True)
    files = {
        3: source_dir / 'image.dat',
        6: source_dir / 'report.pdf',
        43: source_dir / 'clip.mp4',
        34: source_dir / 'voice.silk',
    }
    for path in files.values():
        path.write_bytes(b'media')

    refs = [
        {'md5': f'{media_type:032x}', 'media_type': media_type}
        for media_type in files
    ]
    monkeypatch.setattr(migrator, '_iter_media_refs', lambda *args, **kwargs: refs)
    monkeypatch.setattr(
        migrator, '_find_media_source',
        lambda _db_dir, ref, decrypted_dir=None: files[ref['media_type']],
    )

    stats = migrator.migrate_media(str(tmp_path / 'db_storage'), str(output_dir))

    assert (output_dir / 'media' / 'images' / 'image.dat').is_file()
    assert not (output_dir / 'media' / 'files').exists()
    assert not (output_dir / 'media' / 'videos').exists()
    assert not (output_dir / 'media' / 'voice').exists()
    assert stats['hardlinked'] + stats['copied'] == 1
    assert stats['skipped_non_image'] == 3


def test_attachment_still_resolves_from_original_wechat_directory(tmp_path):
    """A skipped attachment remains openable while its original file exists."""
    decrypted_dir = tmp_path / 'backup'
    hardlink_dir = decrypted_dir / 'hardlink'
    hardlink_dir.mkdir(parents=True)
    storage_root = tmp_path / 'wechat_storage'
    attachment = storage_root / 'wxid_001' / 'msg' / 'file' / '2026-06' / 'report.pdf'
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b'%PDF')

    hardlink_db = hardlink_dir / 'hardlink.db'
    conn = sqlite3.connect(hardlink_db)
    conn.execute('CREATE TABLE db_info (Key TEXT, ValueStdStr TEXT)')
    conn.execute('CREATE TABLE dir2id (username TEXT)')
    conn.execute(
        'CREATE TABLE file_hardlink_info_v4 '
        '(md5 TEXT, file_name TEXT, dir1 INTEGER, dir2 INTEGER)'
    )
    conn.execute('INSERT INTO db_info VALUES (?, ?)', ('uuid', f'a_b_{storage_root}'))
    conn.execute('INSERT INTO dir2id (username) VALUES (?)', ('2026-06',))
    conn.execute(
        'INSERT INTO file_hardlink_info_v4 VALUES (?, ?, ?, ?)',
        ('a' * 32, 'report.pdf', 1, None),
    )
    conn.commit()
    conn.close()

    resolved = _resolve_hardlink_path(
        str(decrypted_dir), {'md5': 'a' * 32, 'media_type': 6}, 'wxid_001'
    )

    assert resolved == str(attachment)
