"""Knowledge Radar — SQLite storage for knowledge cards, sources, and scan runs."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid

# Schema version tracked via SQLite PRAGMA user_version. Bump when a migration
# is added. init_db only caches a db_path as initialised once its user_version
# reaches CURRENT_SCHEMA_VERSION, so an old database is never skipped before its
# migrations run.
CURRENT_SCHEMA_VERSION = 3

# Cache of db paths already initialised in this process, so we don't re-run the
# full CREATE TABLE/INDEX script on every save_card/list_cards call.
_init_lock = threading.Lock()
_initialized_dbs: set = set()


def knowledge_db_path(decrypted_dir: str) -> str:
    """Return path to knowledge.db next to existing ai_analysis config."""
    return os.path.join(os.path.dirname(decrypted_dir), 'ai_analysis', 'knowledge.db')


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # Wait up to 5s for a write lock instead of failing immediately when the
    # background scheduler and a manual scan write concurrently.
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str) -> None:
    """Create tables/indexes and run migrations (idempotent, schema-versioned).

    A db_path is only cached as initialised once its user_version reaches
    CURRENT_SCHEMA_VERSION, so an old user database is never skipped before its
    migrations (e.g. adding lifecycle_stage) have committed.
    """
    abs_path = os.path.abspath(db_path)
    with _init_lock:
        if abs_path in _initialized_dbs:
            return
    conn = _connect(db_path)
    try:
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
          date_from TEXT NOT NULL DEFAULT '',
          date_to TEXT NOT NULL DEFAULT '',
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
        # --- migrations to CURRENT_SCHEMA_VERSION ---
        _migrate(conn)
        conn.commit()
        with _init_lock:
            _initialized_dbs.add(abs_path)
    finally:
        conn.close()


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info([{table}])").fetchall()
    return any(r[1] == column for r in rows)


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply idempotent migrations up to CURRENT_SCHEMA_VERSION."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]

    # v0/v1 -> v2: lifecycle_stage + derivatives + agent_rules.
    if version < CURRENT_SCHEMA_VERSION:
        # Add lifecycle_stage to existing knowledge_cards (default captured).
        if not _column_exists(conn, 'knowledge_cards', 'lifecycle_stage'):
            conn.execute(
                "ALTER TABLE knowledge_cards ADD COLUMN lifecycle_stage "
                "TEXT NOT NULL DEFAULT 'captured'"
            )
        conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_derivatives (
          id TEXT PRIMARY KEY,
          card_id TEXT NOT NULL,
          kind TEXT NOT NULL DEFAULT 'my_version',
          title TEXT NOT NULL DEFAULT '',
          content_md TEXT NOT NULL DEFAULT '',
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          FOREIGN KEY(card_id) REFERENCES knowledge_cards(id) ON DELETE CASCADE
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_rules (
          id TEXT PRIMARY KEY,
          source_card_id TEXT NOT NULL,
          derivative_id TEXT,
          title TEXT NOT NULL DEFAULT '',
          category TEXT NOT NULL DEFAULT 'general',
          content_md TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'draft',
          target_scope TEXT NOT NULL DEFAULT 'shared',
          target_project TEXT NOT NULL DEFAULT '',
          target_path TEXT NOT NULL DEFAULT '',
          version INTEGER NOT NULL DEFAULT 1,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          published_at INTEGER,
          FOREIGN KEY(source_card_id) REFERENCES knowledge_cards(id) ON DELETE RESTRICT,
          FOREIGN KEY(derivative_id) REFERENCES knowledge_derivatives(id) ON DELETE SET NULL
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_kd_card_id ON knowledge_derivatives(card_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ar_source_card_id ON agent_rules(source_card_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ar_status ON agent_rules(status);")
        conn.execute("PRAGMA user_version = 2")

    if version < 3:
        if not _column_exists(conn, 'knowledge_cards', 'knowledge_space_id'):
            conn.execute("ALTER TABLE knowledge_cards ADD COLUMN knowledge_space_id TEXT NOT NULL DEFAULT ''")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS knowledge_spaces (
          id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, vault_path TEXT NOT NULL,
          created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS knowledge_space_chats (
          chat_id TEXT PRIMARY KEY, space_id TEXT NOT NULL, updated_at INTEGER NOT NULL,
          FOREIGN KEY(space_id) REFERENCES knowledge_spaces(id) ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_kc_space ON knowledge_cards(knowledge_space_id);
        CREATE INDEX IF NOT EXISTS idx_ksc_space ON knowledge_space_chats(space_id);
        """)
        conn.execute("PRAGMA user_version = 3")



# ---------------------------------------------------------------------------
# Card helpers
# ---------------------------------------------------------------------------

# Independent use dimension (separate from review status). Existing cards
# migrate to 'captured' and stay backward compatible.
LIFECYCLE_STAGES = {'captured', 'ingested', 'transformed', 'applied'}

# Valid derivative kinds: my_version is a personal-interpretation derivative;
# the rest are structured rewrites aligned with the conversion targets.
DERIVATIVE_KINDS = {
    'my_version', 'audit_case', 'sop', 'prompt', 'faq', 'article', 'script',
}

# Agent rule statuses and categories.
RULE_STATUSES = {'draft', 'published', 'archived'}
RULE_CATEGORIES = {'engineering', 'audit', 'workflow', 'writing', 'ai_usage', 'general'}


def list_knowledge_spaces(db_path: str) -> list:
    init_db(db_path)
    conn = _connect(db_path)
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM knowledge_spaces ORDER BY name COLLATE NOCASE, created_at")]
    finally:
        conn.close()


def create_knowledge_space(db_path: str, name: str, vault_path: str) -> dict:
    name, vault_path = (name or '').strip(), (vault_path or '').strip()
    if not name or not vault_path:
        raise ValueError('知识库名称和 Obsidian 路径不能为空')
    init_db(db_path)
    now = int(time.time())
    space = {'id': str(uuid.uuid4()), 'name': name, 'vault_path': vault_path, 'created_at': now, 'updated_at': now}
    conn = _connect(db_path)
    try:
        conn.execute("INSERT INTO knowledge_spaces (id, name, vault_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                     (space['id'], name, vault_path, now, now))
        conn.commit()
        return space
    except sqlite3.IntegrityError as e:
        raise ValueError('知识库名称不能重复') from e
    finally:
        conn.close()


def assign_chats_to_knowledge_space(db_path: str, space_id: str, chat_ids: list) -> int:
    ids = list(dict.fromkeys(cid for cid in (chat_ids or []) if cid))
    init_db(db_path)
    conn = _connect(db_path)
    try:
        if not conn.execute("SELECT 1 FROM knowledge_spaces WHERE id = ?", (space_id,)).fetchone():
            raise ValueError('知识库空间不存在')
        now = int(time.time())
        conn.executemany("INSERT INTO knowledge_space_chats (chat_id, space_id, updated_at) VALUES (?, ?, ?) ON CONFLICT(chat_id) DO UPDATE SET space_id=excluded.space_id, updated_at=excluded.updated_at", [(cid, space_id, now) for cid in ids])
        conn.commit()
        return len(ids)
    finally:
        conn.close()


def partition_chat_ids_by_knowledge_space(db_path: str, chat_ids: list) -> dict:
    ids = list(dict.fromkeys(cid for cid in (chat_ids or []) if cid))
    if not ids:
        return {'buckets': [], 'unassigned': []}
    init_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT c.chat_id, s.id, s.name, s.vault_path, s.created_at, s.updated_at FROM knowledge_space_chats c JOIN knowledge_spaces s ON s.id=c.space_id WHERE c.chat_id IN (" + ','.join('?' for _ in ids) + ')', ids).fetchall()
    finally:
        conn.close()
    by_chat, buckets, unassigned = {r['chat_id']: dict(r) for r in rows}, {}, []
    for cid in ids:
        space = by_chat.get(cid)
        if not space:
            unassigned.append(cid); continue
        bucket = buckets.setdefault(space['id'], {'space': {k: space[k] for k in ('id','name','vault_path','created_at','updated_at')}, 'chat_ids': []})
        bucket['chat_ids'].append(cid)
    return {'buckets': list(buckets.values()), 'unassigned': unassigned}


def _row_to_card(row: sqlite3.Row) -> dict:
    d = dict(row)
    d['tags'] = json.loads(d.pop('tags_json') or '[]')
    d['source_chat_ids'] = json.loads(d.pop('source_chat_ids_json') or '[]')
    return d


def _escape_like(value: str) -> str:
    """Escape LIKE wildcard characters so user input is matched literally."""
    return (value or '').replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def save_card(db_path: str, card: dict) -> str:
    """Insert or replace a knowledge card and its sources. Returns card ID."""
    init_db(db_path)
    now = int(time.time())
    card_id = card.get('id') or str(uuid.uuid4())
    conn = _connect(db_path)
    try:
        # Preserve the original created_at when updating an existing card;
        # otherwise INSERT OR REPLACE would reset it to now and break
        # time-based ordering on re-scans.
        if card.get('created_at') is None:
            existing = conn.execute(
                "SELECT created_at FROM knowledge_cards WHERE id = ?", (card_id,)
            ).fetchone()
            created_at = existing['created_at'] if existing else now
        else:
            created_at = int(card['created_at'])
        # Preserve lifecycle_stage across re-saves (INSERT OR REPLACE would
        # otherwise reset an ingested/transformed/applied card back to captured).
        if card.get('lifecycle_stage') is None:
            existing = conn.execute(
                "SELECT lifecycle_stage FROM knowledge_cards WHERE id = ?", (card_id,)
            ).fetchone()
            lifecycle = existing['lifecycle_stage'] if existing else 'captured'
        else:
            lifecycle = card['lifecycle_stage'] if card['lifecycle_stage'] in LIFECYCLE_STAGES else 'captured'
        conn.execute("""
          INSERT INTO knowledge_cards
          (id, title, type, status, score, summary, why_valuable, content_md,
           tags_json, source_chat_ids_json, date, created_at, updated_at, lifecycle_stage, knowledge_space_id)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT(id) DO UPDATE SET
            title=excluded.title, type=excluded.type, status=excluded.status,
            score=excluded.score, summary=excluded.summary,
            why_valuable=excluded.why_valuable, content_md=excluded.content_md,
            tags_json=excluded.tags_json,
            source_chat_ids_json=excluded.source_chat_ids_json,
            date=excluded.date, created_at=excluded.created_at,
            updated_at=excluded.updated_at, lifecycle_stage=excluded.lifecycle_stage,
            knowledge_space_id=CASE WHEN knowledge_cards.knowledge_space_id = '' THEN excluded.knowledge_space_id ELSE knowledge_cards.knowledge_space_id END
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
            created_at,
            now,
            lifecycle,
            (card.get('knowledge_space_id') or '').strip(),
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
    finally:
        conn.close()
    return card_id


def get_card(db_path: str, card_id: str) -> dict | None:
    """Load a single card with its sources."""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM knowledge_cards WHERE id = ?", (card_id,)).fetchone()
        if not row:
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
        return card
    finally:
        conn.close()


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
    knowledge_space_id: str = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List cards with filters. Returns {'cards': [...], 'total': N}."""
    init_db(db_path)
    conn = _connect(db_path)
    try:
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
        # LIKE on the JSON column: escape wildcards so a chat_id or search term
        # containing % or _ is matched literally, not as a pattern.
        if chat_id:
            where.append("source_chat_ids_json LIKE ? ESCAPE '\\'")
            params.append(f'%{_escape_like(chat_id)}%')
        if knowledge_space_id:
            where.append("knowledge_space_id = ?")
            params.append(knowledge_space_id)
        if q:
            like = f'%{_escape_like(q)}%'
            where.append("(title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\' OR content_md LIKE ? ESCAPE '\\' OR tags_json LIKE ? ESCAPE '\\')")
            params.extend([like, like, like, like])

        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = conn.execute(f"SELECT COUNT(*) FROM knowledge_cards{clause}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM knowledge_cards{clause} ORDER BY score DESC, date DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        cards = [_row_to_card(r) for r in rows]
        return {'cards': cards, 'total': total}
    finally:
        conn.close()


def update_card(db_path: str, card_id: str, updates: dict) -> bool:
    """Update specific fields on a card. Returns True if found."""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT id FROM knowledge_cards WHERE id = ?", (card_id,)).fetchone()
        if not row:
            return False
        allowed = {'title', 'type', 'status', 'score', 'summary', 'why_valuable',
                   'content_md', 'tags', 'date', 'lifecycle_stage'}
        sets, params = ["updated_at = ?"], [int(time.time())]
        for key in allowed:
            if key in updates:
                if key == 'tags':
                    sets.append("tags_json = ?")
                    params.append(json.dumps(updates[key], ensure_ascii=False))
                elif key == 'lifecycle_stage':
                    stage = updates[key]
                    if stage not in LIFECYCLE_STAGES:
                        raise ValueError(f'invalid lifecycle_stage: {stage}')
                    sets.append("lifecycle_stage = ?")
                    params.append(stage)
                else:
                    sets.append(f"{key} = ?")
                    params.append(updates[key])
        params.append(card_id)
        conn.execute(f"UPDATE knowledge_cards SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
        return True
    finally:
        conn.close()


def delete_card(db_path: str, card_id: str):
    """Delete a card and its cascading sources/derivatives.

    Returns True if deleted. Raises CardHasRulesError if the card still has
    agent_rules (a published rule may be in use); the caller should surface a
    409. Cards without rules delete normally; derivatives cascade with the card.
    The ON DELETE RESTRICT on agent_rules.source_card_id is the final backstop
    for concurrent/out-of-band deletes.
    """
    init_db(db_path)
    conn = _connect(db_path)
    try:
        rule_count = conn.execute(
            "SELECT COUNT(*) FROM agent_rules WHERE source_card_id = ?", (card_id,)
        ).fetchone()[0]
        if rule_count > 0:
            raise CardHasRulesError(card_id, rule_count)
        cur = conn.execute("DELETE FROM knowledge_cards WHERE id = ?", (card_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


class CardHasRulesError(Exception):
    """Raised when deleting a card that still has agent_rules attached."""

    def __init__(self, card_id: str, rule_count: int):
        self.card_id = card_id
        self.rule_count = rule_count
        super().__init__(f'card {card_id} has {rule_count} agent rule(s); archive rules first')


def bulk_update(db_path: str, card_ids: list, action: str, tags: list = None) -> int:
    """Bulk action on cards. Returns count of affected cards."""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        now = int(time.time())
        count = 0
        for cid in card_ids:
            if action == 'delete':
                # Check for dependent agent_rules (ON DELETE RESTRICT)
                rule_count = conn.execute(
                    "SELECT COUNT(*) FROM agent_rules WHERE source_card_id = ?", (cid,)
                ).fetchone()[0]
                if rule_count > 0:
                    raise CardHasRulesError(cid, rule_count)
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
        return count
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Run history
# ---------------------------------------------------------------------------

def create_run(db_path: str, date_from: str, date_to: str, chat_ids: list) -> str:
    """Create a scan run record. Returns run ID."""
    init_db(db_path)
    run_id = str(uuid.uuid4())
    conn = _connect(db_path)
    try:
        conn.execute("""
          INSERT INTO knowledge_runs (id, date_from, date_to, chat_ids_json, status, created_at)
          VALUES (?, ?, ?, ?, 'running', ?)
        """, (run_id, date_from, date_to, json.dumps(chat_ids, ensure_ascii=False), int(time.time())))
        conn.commit()
        return run_id
    finally:
        conn.close()


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
    init_db(db_path)
    conn = _connect(db_path)
    try:
        conn.execute("""
          UPDATE knowledge_runs
          SET status = ?, total_messages = ?, candidate_count = ?, card_count = ?, error = ?
          WHERE id = ?
        """, (status, total_messages, candidate_count, card_count, error, run_id))
        conn.commit()
    finally:
        conn.close()


def list_runs(db_path: str, limit: int = 50) -> list:
    """Return recent scan runs."""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM knowledge_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d['chat_ids'] = json.loads(d.pop('chat_ids_json') or '[]')
            result.append(d)
        return result
    finally:
        conn.close()


def get_stats(db_path: str) -> dict:
    """Return aggregate statistics for the knowledge dashboard."""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM knowledge_cards").fetchone()[0]
        by_status = {}
        for row in conn.execute("SELECT status, COUNT(*) as cnt FROM knowledge_cards GROUP BY status"):
            by_status[row['status']] = row['cnt']
        by_type = {}
        for row in conn.execute("SELECT type, COUNT(*) as cnt FROM knowledge_cards GROUP BY type"):
            by_type[row['type']] = row['cnt']
        avg_score = conn.execute("SELECT AVG(score) FROM knowledge_cards").fetchone()[0] or 0
        return {
            'total': total,
            'by_status': by_status,
            'by_type': by_type,
            'avg_score': round(avg_score, 1),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Knowledge derivatives (one card -> many immutable derivatives)
# ---------------------------------------------------------------------------

def create_derivative(db_path: str, card_id: str, kind: str,
                      title: str, content_md: str) -> str:
    """Create a derivative for a card. Returns derivative id.

    Validates kind against DERIVATIVE_KINDS. After commit, bumps the card's
    lifecycle_stage to 'transformed' (only if currently captured/ingested, so
    an already-applied card is not regressed).
    """
    if kind not in DERIVATIVE_KINDS:
        raise ValueError(f'invalid derivative kind: {kind}')
    init_db(db_path)
    deriv_id = str(uuid.uuid4())
    now = int(time.time())
    conn = _connect(db_path)
    try:
        exists = conn.execute(
            "SELECT 1 FROM knowledge_cards WHERE id = ?", (card_id,)
        ).fetchone()
        if not exists:
            raise ValueError(f'unknown card: {card_id}')
        conn.execute("""
          INSERT INTO knowledge_derivatives
          (id, card_id, kind, title, content_md, created_at, updated_at)
          VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (deriv_id, card_id, kind, title or '', content_md or '', now, now))
        conn.execute(
            "UPDATE knowledge_cards SET lifecycle_stage = 'transformed', updated_at = ? "
            "WHERE id = ? AND lifecycle_stage IN ('captured', 'ingested')",
            (now, card_id),
        )
        conn.commit()
        return deriv_id
    finally:
        conn.close()


def list_derivatives(db_path: str, card_id: str) -> list:
    """List derivatives for a card, oldest first."""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM knowledge_derivatives WHERE card_id = ? ORDER BY created_at",
            (card_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_derivative(db_path: str, derivative_id: str) -> dict | None:
    """Load a single derivative."""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM knowledge_derivatives WHERE id = ?", (derivative_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Agent rules (draft -> published -> archived; LLM never publishes)
# ---------------------------------------------------------------------------

def create_agent_rule(db_path: str, source_card_id: str, *,
                      derivative_id: str = None, title: str = '',
                      category: str = 'general', content_md: str = '',
                      target_scope: str = 'shared', target_project: str = '',
                      target_path: str = '') -> str:
    """Create a draft agent rule. Returns rule id."""
    if category not in RULE_CATEGORIES:
        category = 'general'
    init_db(db_path)
    rule_id = str(uuid.uuid4())
    now = int(time.time())
    conn = _connect(db_path)
    try:
        # If a derivative is referenced, it must belong to the same source card;
        # otherwise the rule's traceability becomes incoherent (design 3.5).
        if derivative_id:
            d = conn.execute(
                "SELECT card_id FROM knowledge_derivatives WHERE id = ?", (derivative_id,)
            ).fetchone()
            if not d:
                raise ValueError(f'unknown derivative: {derivative_id}')
            if d['card_id'] != source_card_id:
                raise ValueError('derivative does not belong to this card')
        conn.execute("""
          INSERT INTO agent_rules
          (id, source_card_id, derivative_id, title, category, content_md, status,
           target_scope, target_project, target_path, version, created_at, updated_at)
          VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, 1, ?, ?)
        """, (rule_id, source_card_id, derivative_id, title, category, content_md,
              target_scope, target_project, target_path, now, now))
        conn.commit()
        return rule_id
    finally:
        conn.close()


def get_agent_rule(db_path: str, rule_id: str) -> dict | None:
    init_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM agent_rules WHERE id = ?", (rule_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_agent_rules(db_path: str, source_card_id: str = None,
                     status: str = None) -> list:
    """List agent rules, optionally filtered by card and/or status."""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        where, params = [], []
        if source_card_id:
            where.append("source_card_id = ?")
            params.append(source_card_id)
        if status:
            where.append("status = ?")
            params.append(status)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(
            f"SELECT * FROM agent_rules{clause} ORDER BY updated_at DESC", params
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_agent_rule(db_path: str, rule_id: str, updates: dict) -> bool:
    """Update editable rule fields. Increments version for content/metadata
    changes. Returns True if found. Published rules cannot be edited."""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM agent_rules WHERE id = ?", (rule_id,)).fetchone()
        if not row:
            return False
        # Published rules are immutable audit records; archive first and create
        # a new version if a change is needed (design principle 5: traceability).
        if row['status'] == 'published':
            raise ValueError('published rules are immutable; archive and create a new version')
        allowed = {'title', 'category', 'content_md', 'target_scope',
                   'target_project', 'target_path'}
        sets, params = [], []
        bumped = False
        for key in allowed:
            if key in updates:
                if key == 'category' and updates[key] not in RULE_CATEGORIES:
                    continue
                sets.append(f"{key} = ?")
                params.append(updates[key])
                bumped = True
        if bumped:
            sets.append("version = version + 1")
        sets.append("updated_at = ?")
        params.append(int(time.time()))
        params.append(rule_id)
        conn.execute(f"UPDATE agent_rules SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
        return True
    finally:
        conn.close()


def publish_agent_rule(db_path: str, rule_id: str) -> dict | None:
    """Publish a rule. Rejects blank title/body. Returns the published rule."""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM agent_rules WHERE id = ?", (rule_id,)).fetchone()
        if not row:
            return None
        if row['status'] == 'published':
            return dict(row)
        if not (row['title'] or '').strip() or not (row['content_md'] or '').strip():
            raise ValueError('rule title and content_md must not be blank')
        now = int(time.time())
        conn.execute(
            "UPDATE agent_rules SET status = 'published', published_at = ?, updated_at = ? "
            "WHERE id = ?",
            (now, now, rule_id),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM agent_rules WHERE id = ?", (rule_id,)).fetchone()
        return dict(updated) if updated else None
    finally:
        conn.close()


def archive_agent_rule(db_path: str, rule_id: str) -> bool:
    """Archive a rule. Returns True if found (already-archived rules are a no-op)."""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT status FROM agent_rules WHERE id = ?", (rule_id,)).fetchone()
        if not row:
            return False
        if row['status'] == 'archived':
            return True
        conn.execute(
            "UPDATE agent_rules SET status = 'archived', updated_at = ? WHERE id = ?",
            (int(time.time()), rule_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_agent_rule_stats(db_path: str) -> dict:
    """Return counts by rule status for the sidebar summary."""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        by_status = {'draft': 0, 'published': 0, 'archived': 0}
        for row in conn.execute("SELECT status, COUNT(*) as cnt FROM agent_rules GROUP BY status"):
            by_status[row['status']] = row['cnt']
        return by_status
    finally:
        conn.close()
