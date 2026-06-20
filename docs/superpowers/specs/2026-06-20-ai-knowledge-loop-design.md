# AI Knowledge Loop Design
> **Canonical version.** The earlier `docs/ai-knowledge-loop-design.md` has been removed; this file is the single source of truth for the AI knowledge loop design.

**Date:** 2026-06-20
**Status:** Approved for planning
**Scope:** Project rule foundation and the first end-to-end "knowledge to agent rules" delivery. Later inbox, action, privacy, and automation work is defined as a roadmap, not mixed into the first implementation package.

## 1. Product Goal

Reframe WeChat EXP around a clear long-term flow:

```text
WeChat records and other useful material
  -> AI analysis and knowledge extraction
  -> reviewed knowledge cards
  -> personal interpretation and reusable outputs
  -> reviewed agent rules
  -> Obsidian / llm-wiki / project AI instructions
```

The product is not only a chat backup tool. Its core value is turning high-value group discussion into traceable, reusable, AI-readable knowledge without allowing unreviewed LLM output to become long-lived development rules.

## 2. Current Foundation

The project already provides:

- AI group analysis that writes structured artifacts with knowledge candidates.
- Knowledge Radar extraction, scoring, source-message references, and a SQLite knowledge store.
- Card review states: `inbox`, `saved`, `archived`, and `rejected`.
- Card conversion, source tracing, and incremental Obsidian export.
- Scheduled knowledge scans and AI-analysis artifact reuse.

The missing part is a controlled path from a reviewed knowledge card to a rule that Codex, Cursor, or another AI tool can read.

## 3. Design Principles

1. `knowledge.db` remains the source of truth for cards, derivatives, and rules.
2. Obsidian and llm-wiki folders are generated, incremental, human-readable views.
3. Raw group content and unreviewed LLM output must never be written directly to a project's `AGENTS.md`.
4. Original knowledge cards are immutable with respect to conversion: conversions create derivatives instead of replacing card content.
5. Every published rule remains traceable to its source card and source messages.
6. Existing card APIs, exports, status filters, and databases remain compatible.
7. All Chinese text files remain UTF-8.

## 4. Workflow

```text
Knowledge card (review status = saved)
  -> mark as ingested
  -> generate "my version" derivative
  -> generate agent rule draft
  -> human edits and reviews draft
  -> publish rule
  -> sync published rule files to the configured knowledge vault
  -> project AGENTS.md navigates to its local rules directory
```

Publishing means exporting an approved rule to a shared AI-readable knowledge directory. It does not automatically alter any other repository's `AGENTS.md`.

## 5. Data Model

### 5.1 Existing knowledge cards

Keep the existing `knowledge_cards.status` as the review dimension:

- `inbox`: needs review
- `saved`: manually retained
- `archived`: retained but inactive
- `rejected`: intentionally ignored

Add `lifecycle_stage` as an independent use dimension:

- `captured`: default for existing and newly extracted cards
- `ingested`: user confirms it has entered their long-term knowledge system
- `transformed`: one or more reusable derivatives exist
- `applied`: user has used it in a project, article, audit workpaper, or rule set

This two-dimensional design preserves existing filters and avoids changing the meaning of `saved` during migration.

### 5.2 Knowledge derivatives

Add a `knowledge_derivatives` table. One card can have multiple derivatives.

| Field | Purpose |
|---|---|
| `id` | Stable UUID |
| `card_id` | Source knowledge card |
| `kind` | `my_version`, `sop`, `prompt`, `faq`, `article`, `audit_case`, or `script` |
| `title` | Derivative title |
| `content_md` | Generated or edited Markdown |
| `created_at`, `updated_at` | Audit and sync timestamps |

The current conversion endpoint becomes derivative creation. The original card's `content_md` and `type` are no longer overwritten for new conversions.

### 5.3 Agent rules

Add an `agent_rules` table.

| Field | Purpose |
|---|---|
| `id` | Stable UUID |
| `source_card_id` | Required source card |
| `derivative_id` | Optional supporting "my version" derivative |
| `title`, `category`, `content_md` | Editable rule content |
| `status` | `draft`, `published`, `archived` |
| `target_scope` | Default `shared`; reserved for `project` |
| `target_project`, `target_path` | Reserved fields for later project-specific publishing |
| `version`, `created_at`, `updated_at`, `published_at` | Versioning and auditability |

Rules are separate from cards because one card may yield several rules and an edited rule must not alter the original knowledge.

## 6. User Experience

Extend the existing Knowledge Radar page. Do not create a separate disconnected application.

### 6.1 Card actions

Add:

- `吸收`: sets `lifecycle_stage=ingested`.
- `标记已应用`: sets `lifecycle_stage=applied`.
- `转化 -> 我的版本`: creates a `my_version` derivative.
- `生成规则草案`: produces a draft agent rule from the card, its derivative, and verified evidence.

### 6.2 Card detail

The existing detail modal gains sections for:

- Original knowledge
- Derivatives, including "my version"
- Agent rule drafts and published rules
- Existing message evidence

Rule drafts are editable before publication. The user can publish or archive a rule from this modal.

### 6.3 Knowledge sidebar

Add a compact AI-rules block near the existing Obsidian controls:

- draft count
- published count
- `同步已发布规则` action

Existing scan, search, filter, export, bulk, schedule, and Obsidian controls retain their positions and behavior.

## 7. LLM Prompts

### 7.1 My-version derivative

The LLM output must use this Markdown structure:

```markdown
# 我的版本

## 原始观点
## 对我的启发
## 适用场景
## 我的理解
## 可执行动作
## 可复用 Prompt、规则或模板
## 是否建议长期保留
```

The prompt must cite the card and available source evidence, avoid inventing facts, and state uncertainty when evidence is incomplete.

### 7.2 Agent-rule draft

Generate a concise, executable rule with:

- a clear title and category
- explicit applicability and exclusions
- concrete directives rather than generic advice
- source references for human review

The LLM returns a draft only. It cannot publish rules.

### 7.3 Prompt externalization

Existing analysis and extraction prompts remain in their current locations in the first delivery. The roadmap records a later prompt-file registry and versioning design. This avoids destabilizing the working group-analysis pipeline.

## 8. Publishing and Sync

Use the configured Obsidian vault as the shared AI-readable knowledge destination:

```text
<vault>/
└── 30_AI可调用/
    ├── INDEX.md
    ├── drafts/
    │   └── <title>__<id>.md
    └── published/
        └── <title>__<id>.md
```

- Each rule has a stable ID and YAML frontmatter including status, category, source card, timestamps, and export marker.
- `INDEX.md` is a generated navigation document, not the source of truth.
- Sync uses `updated_at`, atomic writes, and only touches generated files marked by this product.
- It never deletes user-authored files.
- Missing vault configuration, export errors, or LLM failures leave rule publication state unchanged.

For current projects, root `AGENTS.md` and local `docs/ai-rules/` files remain the AI navigation layer. Future project-specific publishing will use the reserved target fields to copy approved rules into a chosen project's local rule directory after explicit user action.

## 9. Project Rule Foundation

Update the root `AGENTS.md` to match the current product:

- Core flow: backup, view, AI analysis, group management, knowledge extraction, and AI-readable exports.
- Remove or downgrade retired Annual Wrapped and word-cloud descriptions.
- Preserve UTF-8 and privacy requirements.
- Require focused changes, no unnecessary dependencies, and compatibility with shared engine services.

Create `docs/ai-rules/` with:

1. `INDEX.md`
2. `01_项目定位.md`
3. `02_代码结构.md`
4. `03_AI分析规则.md`
5. `04_知识沉淀规则.md`
6. `05_Obsidian同步规则.md`
7. `06_隐私合规规则.md`
8. `07_中文编码规则.md`
9. `08_我的长期偏好.md`

Add `docs/ROADMAP_AI_KNOWLEDGE_SYSTEM.md` to define the next three phases below.

## 10. Roadmap Beyond the First Delivery

### Phase 2: Multi-source inbox

Import Markdown, TXT, and HTML clippings into an inbox table and run them through the same review, derivative, and rule workflow as chat-derived cards. PDF and web crawling remain separate follow-up decisions because they require different extraction and safety controls.

### Phase 3: Action center

Add action items with title, source evidence, priority, due date, next action, status, and related knowledge-card ID. Actions may be created from a card or extracted from a selected chat range.

### Phase 4: Privacy and automation

Add explicit AI data-sharing acknowledgement, optional local masking of common identifiers, outbound-content preview summaries, AI-cache cleanup, daily and weekly reports, and scheduled rule sync. Do not claim full anonymization without a separately validated redaction design.

## 11. API and Service Boundaries

Keep Flask route handlers thin and place persistence/export logic in engine services.

Expected additions:

- Card derivative create/list/read APIs.
- Agent-rule create/list/read/update/publish/archive APIs.
- Published-rules sync API.
- Storage helpers and idempotent schema migration in `knowledge_store.py`.
- Rule rendering/sync helpers in `obsidian_export.py` or a narrowly scoped adjacent engine service.
- Prompt builders in `knowledge_extractor.py`.

No change is required to decryption, backup, or message database logic.

## 12. Failure Handling

- LLM configuration missing: return a clear API error; do not create derivative or rule rows.
- LLM error or malformed response: persist nothing except existing diagnostic behavior; preserve card state.
- Unknown card/rule/derivative ID: return 404.
- Vault path missing or inaccessible: return 400/500 without changing publish state.
- Database migration rerun: must be idempotent.
- Sync interrupted: atomic file writes prevent partial generated files.

## 13. Test Plan

1. Migrate a database that contains current cards and verify all cards survive with `lifecycle_stage=captured`.
2. Create multiple derivatives from one card and verify original card content/type are unchanged.
3. Generate, edit, publish, archive, and resync an agent rule.
4. Verify rule output has stable ID/frontmatter and incremental sync skips unchanged files.
5. Verify a failed LLM call or failed vault write does not publish a rule or alter a card stage.
6. Verify existing card listing, filter, status updates, source display, exports, and Obsidian card sync still work.
7. Run Python compile checks and the relevant analysis/knowledge test suite.
8. Verify the browser flow for card actions, detail sections, draft editing, publication, and sync feedback.

## 14. Acceptance Criteria for the First Delivery

- Root project rules describe the active product accurately.
- Existing `knowledge.db` upgrades without data loss.
- A saved card can be ingested, converted to "my version", and retained alongside the original.
- A user can generate, edit, publish, and archive a rule draft.
- Published rules sync incrementally to `30_AI可调用/published/` in the configured vault.
- Each rule exposes its source card and message evidence.
- No automatic edit occurs in another repository's `AGENTS.md`.
- Existing Knowledge Radar workflows remain usable and tests pass.
