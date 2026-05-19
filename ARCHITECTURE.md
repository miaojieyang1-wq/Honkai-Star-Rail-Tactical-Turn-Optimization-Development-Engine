# 星铁战术引擎架构说明

## 项目定位

星铁战术引擎是一个回合制战斗推演系统骨架，目标是在给定战斗状态下，搜索有限行动值窗口内的最优操作序列，并输出多分支战术建议。

本仓库当前展示的是引擎架构、状态隔离、事件通道、搜索框架、数据接入方式、外部 API 抽象和战术建议书输出。示例数据均为假想数据，仅用于验证数据管道，不代表真实游戏数值。

## 五层架构

1. 核心战斗引擎层：八个硬模块各自拥有写入权，通过事件总线通信。
2. 搜索引擎层：在有限行动值窗口内枚举合法操作序列，执行目标泛函评分和剪枝。
3. 流派扩展层：记忆、欢愉等扩展只维护内部状态，通过接口和事件参与计算。
4. 数据抽象层：角色、敌人、技能等数据通过统一加载接口进入引擎。
5. 外部 API 与输出层：速度轴模拟器、即时伤害计算器和 LLM 建议书输出通过适配器接入。

## 数学状态

引擎采用七元组状态：

```text
S_t = (Q_t, E_t, SP_t, T_t, B_t, DOT_t, D_t)
```

- `Q_t`：行动队列
- `E_t`：能量向量
- `SP_t`：战技点状态
- `T_t`：敌人韧性向量
- `B_t`：Buff/Debuff 列表
- `DOT_t`：持续伤害状态列表
- `D_t`：窗口内累积伤害

角色集合 `C` 由 `BattleState.character_unit_ids` 独立注册，初始化后锁定，不从行动队列反推。

状态向量本身也遵守基础数值域：行动值必须是非负有限数，行动队列单位类型必须属于 `UnitType`，`E_max >= 0`，`SP_max` 和当前 `SP_t` 必须是非负整数，`T_max >= 0`，削韧量非负。能量变化量可以为负，用于表达显式能量成本，但当前能量会被状态容器夹在非负区间内。

## 模块写入权

每个底层模块只写入自己的独占状态：

- `AxisModule` 写 `Q`
- `EnergyModule` 写 `E`
- `SPModule` 写 `SP`
- `ToughnessModule` 写 `T`
- `BuffModule` 写 `B`
- `DOTModule` 写 `DOT`
- `DamageAccumulator` 写 `D`
- `HitModule` 生成确定性受击分支
- `FUAModule` 暴露追加攻击触发检测入口

跨模块交互通过 `EventBus` 完成，模块不直接调用其他模块的写接口。

## 搜索流程

`SearchEngine` 执行深度优先搜索：

1. 发送 `WINDOW_INIT`，由 `DamageAccumulator` 重置窗口伤害。
2. 从行动队列提取 `[t_start, t_end]` 内的我方行动节点。
3. 根据当前状态动态计算合法操作。
4. 通过事件链执行状态转移。
5. 计算目标泛函：

```text
J = D_acc
    - lambda_E * sum_{u in C} max(0, alpha * E_max(u) - E_end(u))
    - lambda_SP * max(0, 1 - SP_end)
```

6. 使用启发式上界剪枝。
7. 对敌人行动生成受击分支，并从敌人行动时刻到原窗口终点搜索剩余窗口。

搜索控制参数也受公式域约束：窗口长度 `ΔT >= 0`，容忍度 `0 < γ <= 1`，受击分支数 `M >= 0`。

受击分支参数也必须保持在概率分布语义内：受击回能 `>= 0`，分支权重 `>= 0`，概率 `p in [0, 1]`，仇恨基础值和仇恨修正值均为非负数。

目标泛函配置也受公式域约束：`lambda_E >= 0`，`lambda_SP >= 0`，安全能量比例 `alpha in [0, 1]`。

## 动作定义边界

`ActionDefinition` 只描述当前搜索节点可选动作的数学元数据，不内置真实角色倍率或真实技能机制。默认动作转移遵循以下边界：

- `sp_cost` 为显式战技点成本，必须是非负整数，执行时通过 `SKILL_SP_CONSUME` 写入 `SP_t`。
- `energy_cost` 为显式能量成本，执行时通过能量事件写入 `E_t`。
- `requires_full_energy` 只表示合法性门槛，不隐式清空能量。
- `toughness_damage` 为显式削韧值，且仅在 `reduces_toughness=True`、目标未锁韧并满足弱点规则时，通过 `TOUGHNESS_REDUCE_REQUEST` 写入 `T_t`。
- 真实角色倍率、终结技耗能、削韧值和目标选择规则应由动作定义提供器、规则数据或外部 API 显式传入。

动作定义的资源成本、显式削韧值、伤害结算值和剪枝伤害上界均要求非负。负数不属于当前公式域；若未来需要表达治疗、回能或负向收益，应新增独立事件类型或状态变量，而不是复用 `D_acc` 或伤害上界。

## 事件通道

核心事件定义在 `event_system.py`：

- 轴事件
- 能量事件
- 战技点事件
- 韧性事件
- Buff 事件
- DoT 事件
- 伤害结算事件
- 受击分支事件
- 追加攻击事件
- 流派事件

伤害写入只能通过 `DamageSettlementEvent`、`DAMAGE_RESET` 或 `WINDOW_INIT` 进入 `DamageAccumulator`。

## 流派层

流派模块通过 `ArchetypeInterface` 接入：

- `initialize(S_0)`
- `handle_event(event)`
- `get_damage_params(unit_id, damage_type)`
- `internal_state`

流派只维护内部状态，不直接写入 `Q/E/SP/T/B/DOT/D`。

## 数据管道

示例数据位于：

- `data/characters/sample_roster.json`
- `data/enemies/sample_enemy.json`

`data_loader.py` 从 `config.yaml` 的路径配置读取数据目录，并提供：

- `load_characters()`
- `load_enemies()`
- `get_character(unit_id)`
- `get_enemy(enemy_id)`

示例数据为假想数据，仅用于验证加载流程。真实项目应从游戏战斗引擎数据模型或统一数据中台接入。

## 外部 API 接口层

`external_api.py` 定义：

- `SpeedAxisApi`：封装社区速度轴穷举模拟器或企业速度轴服务。
- `DamageCalcApi`：封装即时伤害演算工具。
- `ExternalApiBundle`：把速度轴 API 和伤害 API 作为一个可注入依赖传入引擎。
- `MockSpeedAxisApi`、`MockDamageCalcApi`：本地验证用 Mock，不代表真实游戏数值。

`BattleEngine` 可接收 `external_api_bundle`。若没有显式传入 `damage_executor` 或 `damage_upper_bound_provider`，引擎会从该 bundle 中读取伤害执行器和剪枝上界提供器。

## LLM 战术建议书

`tactical_report.py` 将搜索结果转换为玩家可读报告：

- 基准轴
- 目标泛函得分
- 受击变招
- 前提假设
- 作品边界说明
- 可选 LLM 润色版

当前 `TemplateLLMReportClient` 是本地模板实现，不访问网络。真实 LLM 接入时只需实现 `LLMReportClient.generate(prompt)`。

## 已实现边界

当前已实现：

- 状态七元组与独占写入边界
- 同步确定性 `EventBus`
- DFS 搜索框架
- 资源惩罚目标泛函
- 受击分支接口与默认确定性仇恨分布
- 韧性锁定与弱点匹配约束
- 流派插件接口
- 示例数据加载管道
- 外部速度轴/伤害 API 抽象和 Mock 实现
- 战术建议书与 LLM 输出适配层

当前未伪造：

- 真实角色技能倍率
- 真实敌人技能行为
- 真实命途基础仇恨表
- 真实伤害公式 `f_dmg`
- 真实追加攻击触发规则

这些内容应由后续规则数据、战斗数据中台或伤害外挂 API 接入。
