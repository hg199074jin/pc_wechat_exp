# AI 知识系统路线图

本文件定义首批交付之后的后续阶段边界。每阶段需单独设计后再实施，不混入首批包。

## 首批交付（当前）

知识闭环的端到端打通：知识卡 → 我的版本 derivative → 规则草案 → 人工发布 → AI 可读规则文件同步到 vault。
详见 `docs/ai-knowledge-loop-design.md` 和 `docs/ai-knowledge-loop-plan.md`。

---

## Phase 2：多源 inbox

把 Markdown、TXT、HTML 剪藏导入 inbox 表，走与聊天卡片相同的审阅/derivative/规则流程。

**边界**：
- PDF 摄取、网页爬取**暂不纳入**（需要不同的抽取和安全控制，单独设计）
- inbox 卡片与聊天卡片共用同一套 review/lifecycle/derivative/rule 机制

## Phase 3：行动中心

增加 action items：标题、来源证据、优先级、截止日期、下一步动作、状态、关联知识卡 ID。

**来源**：
- 从卡片创建
- 从选定的聊天区间抽取

## Phase 4：隐私与自动化

- 显式的 AI 数据共享确认
- 可选的常见标识符本地遮蔽（**不声称完全匿名化**，需单独验证的脱敏设计）
- 出站内容预览摘要
- AI 缓存清理
- 日报/周报
- 定时规则同步

---

## 明确暂缓的设计（需单独评估）

- **PDF 摄取**：抽取质量、版权、安全控制
- **网页爬取**：robots、合规、去噪
- **NAS / WebDAV / Remotely Save 自动同步**：基础设施层，超出本项目（微信分析工具）定位，应在 Obsidian/NAS 层搭
- **完全匿名化**：需单独的脱敏设计，不能未经验证就声称
