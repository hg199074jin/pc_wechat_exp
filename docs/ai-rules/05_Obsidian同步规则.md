# Obsidian 同步规则

## 卡片同步（已实现）

- 一卡一文件，按 type 分子目录
- YAML frontmatter（id/type/score/status/date/tags/source_chats/updated_at）
- 来源群名转 `[[群名]]` 双链；chat_name 空时 `resolve_wxid` 兜底
- 增量：比对文件 frontmatter `updated_at`，相同跳过
- **只增不删**：绝不删除用户在 Obsidian 的手动编辑

## 规则同步（本批实施）

```
<vault>/30_AI可调用/
├── INDEX.md          ← 生成的导航文档，非真源
├── drafts/<title>__<id>.md
└── published/<title>__<id>.md
```

- 规则文件带 `wechat_exp: "agent-rule-export"` 标记
- 只触碰本产品生成的文件，不覆盖用户文件
- vault 缺失/导出失败/LLM 失败 → 不改变规则发布状态

## 安全边界

- 同步 = 导出到共享 vault
- **不自动修改任何其他仓库的 AGENTS.md**
- 跨项目引用由人工完成（软引用或复制相关段落）

## 不要做

- 不删除 vault 里的旧文件
- 不用 CDN 依赖（保持离线）
- 不在 frontmatter 里泄漏 api_key
