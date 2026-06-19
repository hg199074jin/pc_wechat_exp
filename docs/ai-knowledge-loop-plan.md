# AI Knowledge Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Turn reviewed group-chat knowledge into traceable, human-approved AI rule files without overwriting original cards or automatically editing another project's AGENTS.md.

**Architecture:** knowledge.db remains the source of truth. Extend it with lifecycle stages, immutable card derivatives, and agent-rule records; export those records incrementally into the configured Obsidian vault. Extend the existing Knowledge Radar page instead of adding a disconnected application.

**Tech Stack:** Python 3.8+, Flask, SQLite, Vanilla JavaScript, Markdown, pytest.

---

## File Map

| File | Responsibility |
|---|---|
| AGENTS.md, README.md, docs/ai-rules/*.md, docs/ROADMAP_AI_KNOWLEDGE_SYSTEM.md | Product navigation, stable coding rules, and phases 2-4 roadmap. |
| src/engine/services/knowledge_store.py | Schema migration plus lifecycle, derivative, and rule persistence. |
| src/engine/services/knowledge_extractor.py | Prompts and strict parsing for my-version content and rule drafts. |
| src/engine/services/obsidian_export.py | Agent-rule Markdown rendering, atomic writing, and incremental sync. |
| src/web/routes/knowledge_api.py | Thin JSON API orchestration. |
| src/web/templates/knowledge.html, src/web/static/js/knowledge-app.js | In-place Knowledge Radar controls and detail UI. |
| tests/test_knowledge_store.py, tests/test_knowledge_extractor.py, tests/test_obsidian_export.py, tests/test_knowledge_api.py | Storage, prompt, export, and API regression coverage. |

## Task 1: Align Project Navigation and Product Documentation

**Files:**
- Modify: AGENTS.md
- Modify: README.md
- Create: docs/ai-rules/INDEX.md
- Create: docs/ai-rules/01_项目定位.md through docs/ai-rules/08_我的长期偏好.md
- Create: docs/ROADMAP_AI_KNOWLEDGE_SYSTEM.md

- [ ] **Step 1: Update active features in AGENTS.md**

Replace Annual Wrapped and word-cloud primary descriptions with this core flow: backup/decrypt -> chat viewer -> group management -> AI group analysis -> Knowledge Radar -> Obsidian and AI-readable Markdown.

Keep UTF-8 requirements. Add focused-change, no-unnecessary-dependency, privacy, and shared-engine compatibility rules.

- [ ] **Step 2: Add the AI rule library**

Create INDEX.md and these stable, short rule files: 01_项目定位.md, 02_代码结构.md, 03_AI分析规则.md, 04_知识沉淀规则.md, 05_Obsidian同步规则.md, 06_隐私合规规则.md, 07_中文编码规则.md, and 08_我的长期偏好.md.

INDEX.md must state that AGENTS.md is a navigation layer, not the whole knowledge base.

- [ ] **Step 3: Add the deferred product roadmap**

Document only these boundaries:
- Phase 2: Markdown, TXT, and HTML inbox.
- Phase 3: action center with source evidence and related card IDs.
- Phase 4: AI data-sharing acknowledgement, optional masking, cache cleanup, reports, and scheduled rule sync.

State that PDF ingestion, crawling, NAS/WebDAV, and full anonymization need separate designs.

- [ ] **Step 4: Update README**

Document the approved loop: knowledge card -> my version -> rule draft -> human publish -> AI-readable rule file. State that external project AGENTS.md files are never edited automatically.

- [ ] **Step 5: Verify and commit documentation**

Run: git diff --check
Expected: no whitespace errors.

Run: git add -f AGENTS.md README.md docs/ai-rules docs/ROADMAP_AI_KNOWLEDGE_SYSTEM.md
Run: git commit -m "docs: define AI knowledge workflow"

## Task 2: Add Backward-Compatible Knowledge Storage

**Files:**
- Modify: src/engine/services/knowledge_store.py
- Modify: tests/test_knowledge_store.py

- [ ] **Step 1: Write failing migration tests**

Create an old-style database without lifecycle_stage, call init_db(), then assert the card reads lifecycle_stage=captured and SQLite now contains knowledge_derivatives and agent_rules. Save a derivative and rule, call init_db() a second time, and assert the rows are unchanged.

Test that a lifecycle-only update leaves status, score, and content_md unchanged.

- [ ] **Step 2: Run the migration test**

Run: ..\..\.venv\Scripts\python.exe -m pytest tests/test_knowledge_store.py -q --basetemp .pytest-tmp
Expected: FAIL because the new column and tables do not exist.

- [ ] **Step 3: Implement an idempotent migration**

Make the initialization cache schema-versioned. A database path is cacheable only after the current schema version's DDL and migrations commit. In init_db(), use PRAGMA table_info(knowledge_cards) and only when lifecycle_stage is missing execute:
ALTER TABLE knowledge_cards ADD COLUMN lifecycle_stage TEXT NOT NULL DEFAULT 'captured'

Add knowledge_derivatives:
- id TEXT PRIMARY KEY
- card_id TEXT NOT NULL references knowledge_cards ON DELETE CASCADE
- kind, title, content_md
- created_at, updated_at

Add agent_rules:
- id TEXT PRIMARY KEY
- source_card_id TEXT NOT NULL references knowledge_cards ON DELETE RESTRICT
- derivative_id TEXT references knowledge_derivatives ON DELETE SET NULL
- title, category, content_md, status
- target_scope default shared, target_project, target_path
- version default 1, created_at, updated_at, published_at

Create indexes for derivative.card_id, rule.source_card_id, and rule.status. Do not rename existing review statuses. Do not add the path to the current-schema cache before the migration transaction commits.

- [ ] **Step 4: Extend card persistence**

Allow only captured, ingested, transformed, and applied as lifecycle stages. save_card() defaults lifecycle_stage to captured; existing callers omitting it remain compatible. update_card() validates lifecycle_stage and includes it in its whitelist.

- [ ] **Step 5: Write and implement derivative CRUD**

Write a test that creates a my_version derivative, verifies the original card content is unchanged, and verifies list_derivatives() returns the derivative. Validate derivative kinds against exactly: my_version (personal interpretation), audit_case, sop, prompt, faq, article, and script (structured rewrites).

Implement these focused functions:
- create_derivative(db_path, card_id, kind, title, content_md) -> str
- list_derivatives(db_path, card_id) -> list
- get_derivative(db_path, derivative_id) -> dict or None

After an insert commits, update the card lifecycle to transformed.

- [ ] **Step 6: Write and implement agent-rule CRUD**

Test and implement:
- create_agent_rule(db_path, source_card_id, derivative_id=None, title='', category='general', content_md='', target_scope='shared', target_project='', target_path='') -> str
- get_agent_rule(db_path, rule_id) -> dict or None
- list_agent_rules(db_path, source_card_id=None, status=None) -> list
- update_agent_rule(db_path, rule_id, updates) -> bool
- publish_agent_rule(db_path, rule_id) -> dict or None
- archive_agent_rule(db_path, rule_id) -> bool
- get_agent_rule_stats(db_path) -> dict

publish_agent_rule() rejects blank title/body, sets published_at, and changes status to published. update_agent_rule() increments version only for metadata/content changes.

- [ ] **Step 7: Make card deletion deliberate**

Before deletion, check agent_rules for source_card_id and return a structured blocked result instead of a raw SQLite foreign-key error. Keep ON DELETE RESTRICT on source_card_id as a database integrity backstop; catch and translate a late SQLite restriction error to the same 409 response. Cards without rules retain current delete behavior, and derivatives cascade-delete with their source card.

- [ ] **Step 8: Run and commit storage work**

Run: ..\..\.venv\Scripts\python.exe -m pytest tests/test_knowledge_store.py -q --basetemp .pytest-tmp
Expected: PASS.

Run: git add src/engine/services/knowledge_store.py tests/test_knowledge_store.py
Run: git commit -m "feat: add knowledge lifecycle and rule storage"

## Task 3: Generate My-Version Derivatives and Rule Drafts

**Files:**
- Modify: src/engine/services/knowledge_extractor.py
- Modify: tests/test_knowledge_extractor.py

- [ ] **Step 1: Write failing prompt and parser tests**

Test build_my_version_prompt(card) contains the Chinese headings for personal inspiration and executable action. Test strict rule parsing for a valid JSON object, invalid JSON, unknown categories, and missing content_md.

- [ ] **Step 2: Run extractor tests**

Run: ..\..\.venv\Scripts\python.exe -m pytest tests/test_knowledge_extractor.py -q --basetemp .pytest-tmp
Expected: FAIL because the builders do not exist.

- [ ] **Step 3: Implement the my-version prompt**

Add build_my_version_prompt(card) -> (system, user). Require Markdown sections for original viewpoint, personal inspiration, suitable scenarios, personal understanding, executable action, reusable Prompt/rule/template, and retention recommendation. Include quoted evidence and prohibit invented facts.

- [ ] **Step 4: Implement rule-draft generation**

Add build_agent_rule_prompt(card, derivative) -> (system, user) and parse_agent_rule_draft(raw) -> dict.

Reuse _extract_json_object() before json.loads(). Accept categories engineering, audit, workflow, writing, ai_usage, and general. Normalize unknown categories to general. Raise ValueError if title or content_md is empty.

- [ ] **Step 5: Preserve old conversion prompt compatibility**

Keep build_convert_prompt() unchanged. Task 5 changes its persistence from card overwrite to derivative creation.

- [ ] **Step 6: Run and commit extractor work**

Run: ..\..\.venv\Scripts\python.exe -m pytest tests/test_knowledge_extractor.py tests/test_ai_analyzer_llm.py -q --basetemp .pytest-tmp
Expected: PASS.

Run: git add src/engine/services/knowledge_extractor.py tests/test_knowledge_extractor.py
Run: git commit -m "feat: generate personal knowledge derivatives and rule drafts"

## Task 4: Export Agent Rules to the Knowledge Vault

**Files:**
- Modify: src/engine/services/obsidian_export.py
- Modify: tests/test_obsidian_export.py

- [ ] **Step 1: Write failing sync tests**

A published rule must write to vault/30_AI可调用/published/<safe-title>__<id8>.md and contain wechat_exp: "agent-rule-export".

Cover draft placement, unchanged skip, changed rewrite, generated INDEX.md, no-vault errors, and no deletion after an empty sync.

- [ ] **Step 2: Run export tests**

Run: ..\..\.venv\Scripts\python.exe -m pytest tests/test_obsidian_export.py -q --basetemp .pytest-tmp
Expected: FAIL because rule sync does not exist.

- [ ] **Step 3: Implement renderers and atomic sync**

Add RULE_EXPORT_MARKER='agent-rule-export', _render_rule_frontmatter(rule), _render_rule_body(rule), _atomic_write_text(path, content), and sync_agent_rules_to_vault(rules, vault_path).

Frontmatter contains id, category, status, source_card_id, target_scope, version, updated_at, published_at, and marker. Write drafts to 30_AI可调用/drafts, published rules to 30_AI可调用/published, and a generated INDEX.md. Use updated_at to skip unchanged files. Never delete files or overwrite non-generated files.

- [ ] **Step 4: Run and commit export work**

Run: ..\..\.venv\Scripts\python.exe -m pytest tests/test_obsidian_export.py -q --basetemp .pytest-tmp
Expected: PASS.

Run: git add src/engine/services/obsidian_export.py tests/test_obsidian_export.py
Run: git commit -m "feat: sync reviewed agent rules to vault"

## Task 5: Add Flask APIs and Preserve Existing Conversion Clients

**Files:**
- Modify: src/web/routes/knowledge_api.py
- Create: tests/test_knowledge_api.py

- [ ] **Step 1: Create a failing Flask API fixture**

Use web.app.create_app() with a temporary decrypted directory. Mock extractor.make_llm_call() for deterministic output.

- [ ] **Step 2: Write failing API tests**

Cover:
- POST /api/knowledge/cards/<id>/lifecycle
- GET /api/knowledge/cards/<id>/derivatives
- POST /api/knowledge/cards/<id>/convert
- POST /api/knowledge/cards/<id>/agent-rules/draft
- GET /api/knowledge/cards/<id>/agent-rules
- PUT /api/knowledge/agent-rules/<id>
- POST /api/knowledge/agent-rules/<id>/publish
- POST /api/knowledge/sync-agent-rules

Assert missing LLM config -> 400, unknown IDs -> 404, blank publish -> 400, missing vault -> 400 without changing rule status, and card deletion with rules -> 409.

- [ ] **Step 3: Run API tests**

Run: ..\..\.venv\Scripts\python.exe -m pytest tests/test_knowledge_api.py -q --basetemp .pytest-tmp
Expected: FAIL because routes are absent.

- [ ] **Step 4: Implement lifecycle and derivative routes**

Routes only validate input and call the store/extractor. Modify convert_card_api() to call create_derivative() after a successful LLM call and remove the card-overwrite call. Return an explicit contract: ok, source_card_id, derivative_id, derivative, and card_unchanged=true. Do not return top-level content_md/type fields that imply the source card changed. Task 6 updates the client to refresh the derivative section in the detail modal after conversion.

- [ ] **Step 5: Implement rule and sync routes**

Generate drafts from the card plus an optional selected my_version derivative. Add list/get/update/publish/archive/stats routes. Sync uses get_obsidian_vault_path() and sync_agent_rules_to_vault(); it returns counts/errors but does not change publish status.

- [ ] **Step 6: Run and commit API work**

Run: ..\..\.venv\Scripts\python.exe -m pytest tests/test_knowledge_api.py -q --basetemp .pytest-tmp
Expected: PASS.

Run: git add src/web/routes/knowledge_api.py tests/test_knowledge_api.py
Run: git commit -m "feat: add knowledge derivative and agent rule APIs"

## Task 6: Extend Knowledge Radar in Place

**Files:**
- Modify: src/web/templates/knowledge.html
- Modify: src/web/static/js/knowledge-app.js

- [ ] **Step 1: Add minimal sidebar UI**

Near existing Obsidian controls add an AI rules heading, agent-rule-summary element, and sync-agent-rules button. Reuse current dark styling, btn-tiny, and modal patterns; do not redesign the page.

- [ ] **Step 2: Add lifecycle and conversion actions**

Render lifecycle stage plus 吸收 and 标记已应用. Add 我的版本 to the current conversion menu. Retain all current save/archive/delete/bulk actions.

- [ ] **Step 3: Add lifecycle and sync handlers**

Implement updateLifecycle(cardId, lifecycleStage), loadAgentRuleStats(), and syncAgentRules(). Reload cards/stats after successful updates. Escape all dynamic server content and never insert raw LLM JSON into the DOM.

- [ ] **Step 4: Expand the existing detail modal**

Load card, derivatives, and rules. Render in this order: 原始知识, 我的版本与其他转化, 规则草案与已发布规则, 来源.

Each draft needs editable title, category select, Markdown textarea, and 保存草案 / 发布 / 归档 actions. Keep the existing modal max-height: 80vh and overflow-y: auto; give Markdown textareas a bounded initial height, max-height, and vertical resize so they cannot overflow the viewport. Disable generation/publish buttons while requests are pending and re-enable in finally.

- [ ] **Step 5: Handle delete conflicts and verify in browser**

For HTTP 409 show the API message and retain the list. Verify scan/filter/export remain usable, conversion preserves original content, rule drafts can be edited/published/synced, long content stays inside the detail modal, and normal Obsidian card sync continues to work.

- [ ] **Step 6: Commit UI work**

Run: git add src/web/templates/knowledge.html src/web/static/js/knowledge-app.js
Run: git commit -m "feat: manage agent rules from knowledge radar"

## Task 7: Regression, Documentation, and Handoff

**Files:**
- Modify: README.md and docs/ROADMAP_AI_KNOWLEDGE_SYSTEM.md only if implementation changes documented behavior
- Test: tests/test_knowledge_store.py, tests/test_knowledge_extractor.py, tests/test_knowledge_api.py, tests/test_obsidian_export.py, tests/test_knowledge_artifact_bridge.py, tests/test_knowledge_artifact_reuse.py, tests/test_ai_analyzer_artifact.py

- [ ] **Step 1: Compile changed Python modules**

Run: ..\..\.venv\Scripts\python.exe -m py_compile src\engine\services\knowledge_store.py src\engine\services\knowledge_extractor.py src\engine\services\obsidian_export.py src\web\routes\knowledge_api.py
Expected: exit code 0.

- [ ] **Step 2: Run the focused regression suite**

Run: ..\..\.venv\Scripts\python.exe -m pytest tests\test_knowledge_store.py tests\test_knowledge_extractor.py tests\test_knowledge_api.py tests\test_obsidian_export.py tests\test_knowledge_artifact_bridge.py tests\test_knowledge_artifact_reuse.py tests\test_ai_analyzer_artifact.py -q --basetemp .pytest-tmp
Expected: all tests PASS.

The storage tests must explicitly include a second init_db() call after creating derivatives/rules and assert that migration is idempotent and data remains intact.

- [ ] **Step 3: Check final diff and commit documentation**

Run: git diff --check
Run: git status --short
Run: git diff master...HEAD --stat
Expected: no whitespace errors and only intended files.

Run: git add README.md docs/ROADMAP_AI_KNOWLEDGE_SYSTEM.md
Run: git commit -m "docs: document AI knowledge rule workflow"

- [ ] **Step 4: Produce handoff summary**

Report branch, commits, tests, migration behavior, vault requirement, and the safety guarantee that no external repository AGENTS.md was modified automatically.
