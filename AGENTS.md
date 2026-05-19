# AGENTS.md

本项目使用 AI/Codex 辅助开发。AI 在执行任何任务前必须遵守以下规则。

## 必读

1. 先读本文件。
2. 再读 `SAFE_AI_RULES.md`。
3. 若存在 `README.md`、`CHANGELOG.md`、`docs/decisions.md`，按任务需要读取。
4. 不要一次性加载所有长文档，只读取当前任务相关内容。

## 工作方式

- 修改前先检查现有文件结构、编码、风格和依赖。
- 大改、删除、覆盖、迁移前必须先备份。
- 保持 UTF-8 without BOM 和 LF 换行。
- 不写入密钥、Token、密码、真实连接串。
- 不覆盖用户未要求覆盖的改动。
- 修改后必须说明变更、风险和验证结果。

## 规则入口

完整工程约束见：

- `SAFE_AI_RULES.md`

如果项目需要更细规则，可新增：

- `docs/codex/CODING_RULES.md`
- `docs/codex/ARCHITECTURE_RULES.md`
- `docs/codex/SECURITY_RULES.md`
- `docs/codex/DEPLOYMENT_RULES.md`
- `docs/codex/PROJECT_CONTEXT.md`

AI 应按需读取这些文件，不得把所有长规则一次性塞入上下文。
