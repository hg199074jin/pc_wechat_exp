"""Knowledge Radar — SQLite storage for knowledge cards, sources, and scan runs."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid


def knowledge_db_path(decrypted_dir: str) -> str:
    """Return path to knowledge.db next to existing ai_analysis config."""
    return os.path.join(os.path.dirname(decrypted_dir), 'ai_analysis', 'knowledge.db')


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str) -> None:
    """Create tables and indexes if they don't exist."""
    conn = _connect(db_path)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS knowledge_cards (
      id TEXT PRIMARY KEY,
      title TEXT NOT NULL,
      type TEXT NOT NULL DEFAULT 'note',
      status TEXT NOT NULL DEFAULT 'inbox',
      score INTEGER NOT NULL DEFAULT 0,
      summary TEXT NOT NULL DEFAULT '',
      why_valuable TEXT NOT NULL DEFAULT '',
      content_md TEXT NOT NULL DEFAULT '',
      tags_json TEXT NOT NULL DEFAULT '[]',
      source_chat_ids_json TEXT NOT NULL DEFAULT '[]',
      date TEXT NOT NULL DEFAULT '',
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS knowledge_sources (
      id TEXT PRIMARY KEY,
      card_id TEXT NOT NULL,
      chat_id TEXT NOT NULL DEFAULT '',
      chat_name TEXT NOT NULL DEFAULT '',
      msg_id INTEGER,
      sender TEXT NOT NULL DEFAULT '',
      create_time INTEGER,
      quote TEXT NOT NULL DEFAULT '',
      context_json TEXT NOT NULL DEFAULT '[]',
      FOREIGN KEY(card_id) REFERENCES knowledge_cards(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS knowledge_runs (
      id TEXT PRIMARY KEY,
      date_from TEXT NOT NULL,
      date_to TEXT NOT NULL,
      chat_ids_json TEXT NOT NULL DEFAULT '[]',
      status TEXT NOT NULL DEFAULT 'running',
      total_messages INTEGER NOT NULL DEFAULT 0,
      candidate_count INTEGER NOT NULL DEFAULT 0,
      card_count INTEGER NOT NULL DEFAULT 0,
      error TEXT NOT NULL DEFAULT '',
      created_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_kc_status ON knowledge_cards(status);
    CREATE INDEX IF NOT EXISTS idx_kc_type ON knowledge_cards(type);
    CREATE INDEX IF NOT EXISTS idx_kc_date ON knowledge_cards(date);
    CREATE INDEX IF NOT EXISTS idx_kc_score ON knowledge_cards(score);
    CREATE INDEX IF NOT EXISTS idx_ks_card_id ON knowledge_sources(card_id);
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Card helpers
# ---------------------------------------------------------------------------

def _row_to_card(row: sqlite3.Row) -> dict:
    d = dict(row)
    d['tags'] = json.loads(d.pop('tags_json') or '[]')
    d['source_chat_ids'] = json.loads(d.pop('source_chat_ids_json') or '[]')
    return d


def save_card(db_path: str, card: dict) -> str:
    """Insert or replace a knowledge card and its sources. Returns card ID."""
    init_db(db_path)
    now = int(time.time())
    card_id = card.get('id') or str(uuid.uuid4())
    conn = _connect(db_path)
    conn.execute("""
      INSERT OR REPLACE INTO knowledge_cards
      (id, title, type, status, score, summary, why_valuable, content_md,
       tags_json, source_chat_ids_json, date, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        card_id,
        (card.get('title') or '').strip(),
        card.get('type') or 'note',
        card.get('status') or 'inbox',
        int(card.get('score') or 0),
        card.get('summary') or '',
        card.get('why_valuable') or '',
        card.get('content_md') or '',
        json.dumps(card.get('tags') or [], ensure_ascii=False),
        json.dumps(card.get('source_chat_ids') or [], ensure_ascii=False),
        card.get('date') or '',
        int(card.get('created_at') or now),
        now,
    ))
    # Replace sources
    conn.execute("DELETE FROM knowledge_sources WHERE card_id = ?", (card_id,))
    for src in (card.get('sources') or []):
        conn.execute("""
          INSERT INTO knowledge_sources
          (id, card_id, chat_id, chat_name, msg_id, sender, create_time, quote, context_json)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()), card_id,
            src.get('chat_id') or '',
            src.get('chat_name') or '',
            src.get('msg_id'),
            src.get('sender') or '',
            src.get('create_time'),
            src.get('quote') or '',
            json.dumps(src.get('context') or [], ensure_ascii=False),
        ))
    conn.commit()
    conn.close()
    return card_id


def get_card(db_path: str, card_id: str) -> dict | None:
    """Load a single card with its sources."""
    init_db(db_path)
    conn = _connect(db_path)
    row = conn.execute("SELECT * FROM knowledge_cards WHERE id = ?", (card_id,)).fetchone()
    if not row:
        conn.close()
        return None
    card = _row_to_card(row)
    sources = []
    for src in conn.execute(
        "SELECT * FROM knowledge_sources WHERE card_id = ? ORDER BY create_time",
        (card_id,),
    ):
        d = dict(src)
        d['context'] = json.loads(d.pop('context_json') or '[]')
        sources.append(d)
    card['sources'] = sources
    conn.close()
    return card


def list_cards(
    db_path: str,
    *,
    status: str = None,
    card_type: str = None,
    q: str = None,
    min_score: int = None,
    date_from: str = None,
    date_to: str = None,
    chat_id: str = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List cards with filters. Returns {'cards': [...], 'total': N}."""
    init_db(db_path)
    conn = _connect(db_path)
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if card_type:
        where.append("type = ?")
        params.append(card_type)
    if min_score is not None:
        where.append("score >= ?")
        params.append(min_score)
    if date_from:
        where.append("date >= ?")
        params.append(date_from)
    if date_to:
        where.append("date <= ?")
        params.append(date_to)
    if chat_id:
        where.append("source_chat_ids_json LIKE ?")
        params.append(f'%{chat_id}%')
    if q:
        like = f'%{q}%'
        where.append("(title LIKE ? OR summary LIKE ? OR content_md LIKE ? OR tags_json LIKE ?)")
        params.extend([like, like, like, like])

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM knowledge_cards{clause}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM knowledge_cards{clause} ORDER BY score DESC, date DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    cards = [_row_to_card(r) for r in rows]
    conn.close()
    return {'cards': cards, 'total': total}


def update_card(db_path: str, card_id: str, updates: dict) -> bool:
    """Update specific fields on a card. Returns True if found."""
    init_db(db_path)
    conn = _connect(db_path)
    row = conn.execute("SELECT id FROM knowledge_cards WHERE id = ?", (card_id,)).fetchone()
    if not row:
        conn.close()
        return False
    allowed = {'title', 'type', 'status', 'score', 'summary', 'why_valuable',
               'content_md', 'tags', 'date'}
    sets, params = ["updated_at = ?"], [int(time.time())]
    for key in allowed:
        if key in updates:
            if key == 'tags':
                sets.append("tags_json = ?")
                params.append(json.dumps(updates[key], ensure_ascii=False))
            else:
                sets.append(f"{key} = ?")
                params.append(updates[key])
    params.append(card_id)
    conn.execute(f"UPDATE knowledge_cards SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return True


def delete_card(db_path: str, card_id: str) -> bool:
    """Delete a card and its sources. Returns True if found."""
    init_db(db_path)
    conn = _connect(db_path)
    cur = conn.execute("DELETE FROM knowledge_cards WHERE id = ?", (card_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def bulk_update(db_path: str, card_ids: list, action: str, tags: list = None) -> int:
    """Bulk action on cards. Returns count of affected cards."""
    init_db(db_path)
    conn = _connect(db_path)
    now = int(time.time())
    count = 0
    for cid in card_ids:
        if action == 'delete':
            cur = conn.execute("DELETE FROM knowledge_cards WHERE id = ?", (cid,))
        elif action in ('inbox', 'saved', 'archived', 'rejected'):
            cur = conn.execute(
                "UPDATE knowledge_cards SET status = ?, updated_at = ? WHERE id = ?",
                (action, now, cid),
            )
        elif action == 'tag' and tags:
            row = conn.execute("SELECT tags_json FROM knowledge_cards WHERE id = ?", (cid,)).fetchone()
            if row:
                existing = json.loads(row['tags_json'] or '[]')
                merged = list(dict.fromkeys(existing + tags))
                cur = conn.execute(
                    "UPDATE knowledge_cards SET tags_json = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(merged, ensure_ascii=False), now, cid),
                )
            else:
                cur = None
        else:
            cur = None
        if cur and cur.rowcount > 0:
            count += 1
    conn.commit()
    conn.close()
    return count


# ---------------------------------------------------------------------------
# Run history
# ---------------------------------------------------------------------------

def create_run(db_path: str, date_from: str, date_to: str, chat_ids: list) -> str:
    """Create a scan run record. Returns run ID."""
    init_db(db_path)
    run_id = str(uuid.uuid4())
    conn = _connect(db_path)
    conn.execute("""
      INSERT INTO knowledge_runs (id, date_from, date_to, chat_ids_json, status, created_at)
      VALUES (?, ?, ?, ?, 'running', ?)
    """, (run_id, date_from, date_to, json.dumps(chat_ids, ensure_ascii=False), int(time.time())))
    conn.commit()
    conn.close()
    return run_id


def finish_run(
    db_path: str,
    run_id: str,
    *,
    status: str = 'done',
    total_messages: int = 0,
    candidate_count: int = 0,
    card_count: int = 0,
    error: str = '',
) -> None:
    """Mark a scan run as finished."""
    conn = _connect(db_path)
    conn.execute("""
      UPDATE knowledge_runs
      SET status = ?, total_messages = ?, candidate_count = ?, card_count = ?, error = ?
      WHERE id = ?
    """, (status, total_messages, candidate_count, card_count, error, run_id))
    conn.commit()
    conn.close()


def list_runs(db_path: str, limit: int = 50) -> list:
    """Return recent scan runs."""
    init_db(db_path)
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT * FROM knowledge_runs ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['chat_ids'] = json.loads(d.pop('chat_ids_json') or '[]')
        result.append(d)
    conn.close()
    return result


def get_stats(db_path: str) -> dict:
    """Return aggregate statistics for the knowledge dashboard."""
    init_db(db_path)
    conn = _connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM knowledge_cards").fetchone()[0]
    by_status = {}
    for row in conn.execute("SELECT status, COUNT(*) as cnt FROM knowledge_cards GROUP BY status"):
        by_status[row['status']] = row['cnt']
    by_type = {}
    for row in conn.execute("SELECT type, COUNT(*) as cnt FROM knowledge_cards GROUP BY type"):
        by_type[row['type']] = row['cnt']
    avg_score = conn.execute("SELECT AVG(score) FROM knowledge_cards").fetchone()[0] or 0
    conn.close()
    return {
        'total': total,
        'by_status': by_status,
        'by_type': by_type,
        'avg_score': round(avg_score, 1),
    }
