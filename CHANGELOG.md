# 变更日志

## 2026-05-16

### 新增

- 新增 `external_api.py`，定义速度轴模拟器、即时伤害计算器、外部 API 包和本地 Mock 实现。
- 新增 `tactical_report.py`，将 `TacticalAdvice` 转换为战术建议书，并支持可替换 LLM 客户端。
- `BattleEngine` 新增 `external_api_bundle` 参数，可将伤害计算 API 注入搜索评分和剪枝上界。
- 冒烟测试新增外部 API 与战术报告闭环验证。

### 文档

- README 补充玩家痛点、外部 API、LLM 输出和验证命令。
- ARCHITECTURE 补充五层架构、外部 API 接口层和 LLM 战术建议书层。

### 边界说明

- 当前 Mock 仅验证架构和接口形状，不代表真实游戏伤害公式、仇恨公式或角色机制参数。
