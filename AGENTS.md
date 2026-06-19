# WeChat EXP — Project Instructions

## Project Overview

WeChat EXP (wechat-exp) is a portable Windows tool for extracting, decrypting, and analyzing WeChat 4.x chat records. Its core flow turns chat data into traceable, reusable, AI-readable knowledge:

```
backup/decrypt → chat viewer → group management → AI group analysis
  → Knowledge Radar (cards) → my-version derivatives → reviewed agent rules
  → Obsidian / llm-wiki / project AI instructions
```

Active capabilities:
- SQLCipher 4 key extraction from Weixin.exe memory + batch AES-256-CBC decryption
- Web-based chat viewer (Flask SPA): avatars, group info, message search, voice transcription
- Group management: tag tree, AI auto-classification, blacklist
- AI group analysis: structured artifacts with evidence verification, style fingerprints, chunked rollup
- Knowledge Radar: LLM extraction + scoring → `knowledge.db` cards with review states and lifecycle
- Obsidian sync: one-card-per-file Markdown export with frontmatter and `[[group]]` wikilinks
- Exports: chat TXT/HTML, employee reports, comprehensive HTML report

> Retired: the old Annual "Wrapped" report and word-cloud features have been removed.

**Tech Stack**: Python 3.8+, Flask 3.x, SQLite (SQLCipher 4), Vanilla JS (SPA), ECharts

## Key Paths

- Source: `src/`
- Engine services: `src/engine/services/` (chat.py, message.py, media.py, ai_analyzer.py, knowledge_store.py, knowledge_extractor.py, obsidian_export.py, analysis_artifact.py)
- Web app: `src/web/app.py` → `src/web/routes/`
- Output: `output/` (decrypted DBs, exports, caches)
- Docs: `docs/` (specs, plans, analysis)

## Working Style

Before starting any task, read the relevant rules in `docs/ai-rules/` (see `INDEX.md`). Rules are short and stable; the knowledge base lives elsewhere.

- Make focused changes: explain which files will change before editing, and summarize what changed, why, and how to verify after.
- Do not introduce unnecessary new dependencies.
- Do not delete existing functionality unless explicitly requested.
- `src/engine/services/` is shared between Web and CLI — keep it engine-layer-pure (no Flask imports there).
- Python imports use flat `from engine.services.xxx import yyy` style.
- Keep Flask route handlers thin: put persistence/export logic in engine services.
- Frontend JS is vanilla (no framework), split per-page; escape all dynamic server content before inserting into the DOM.
- All API routes return JSON; use Flask Blueprints.
- Chinese text in UI, English in code identifiers.

## Privacy & Encoding

- All Chinese text files remain UTF-8. Do not rewrite UTF-8 files to GBK/ANSI.
- Raw group content and unreviewed LLM output must never be written directly to a project's `AGENTS.md`.
- AI features send selected chat text to the configured LLM service; never upload media. Assess data-sharing risk before use.

## Memory

Key facts about WeChat 4.x data:
- WeChat 4.x uses SQLCipher 4 with AES-256-CBC
- Message DBs are sharded as message_0.db, message_1.db, etc.
- Group chats stored as xxxxx@chatroom, resolved via 5-level name resolution
- Avatar system uses 3-tier fallback (CDN → cache → SVG default)
