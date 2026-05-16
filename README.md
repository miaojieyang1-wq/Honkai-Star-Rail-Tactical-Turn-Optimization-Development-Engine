# 星铁战术引擎

一个建立在精确数学公式之上的回合制战斗推演系统骨架，用于在给定战斗状态下，搜索有限行动值窗口内的最优操作序列，并输出多分支战术建议。

这个作品源于玩家真实排轴痛点：社区工具可以分别模拟速度轴和单次伤害，但缺少一个把两者串起来、自动枚举合法操作、处理受击回能分支并生成战术建议书的中间层引擎。本仓库展示的正是这个中间层。

## 项目亮点

- 七元组战斗状态：`S_t = (Q_t, E_t, SP_t, T_t, B_t, DOT_t, D_t)`
- 模块独占写入：轴、能量、战技点、韧性、Buff、DoT、伤害累积各自拥有状态变量
- 同步确定性事件总线：跨模块通信只通过 `EventBus`
- 搜索引擎：深度优先搜索、启发式剪枝、受击分支剩余窗口搜索
- 流派插件接口：记忆、欢愉等扩展层不直接写核心状态
- 示例数据管道：角色/敌人样例数据从 `config.yaml` 路径配置读取
- 外部 API 抽象：速度轴模拟器与即时伤害计算器通过 `external_api.py` 注入
- 战术建议书输出：`tactical_report.py` 支持模板输出与 LLM 客户端润色

## 当前边界

本仓库展示的是战术引擎的架构、状态隔离、事件通道、搜索框架和数据接入方式。示例数据均为假想数据，仅用于验证数据管道。

未伪造的部分：

- 真实角色技能倍率
- 真实敌人技能行为
- 真实命途基础仇恨表
- 真实伤害公式 `f_dmg`
- 真实追加攻击触发规则

这些内容应由后续规则数据、战斗数据中台或伤害外挂 API 接入。

## 文件结构

```text
.
├── battle_engine.py          # 主入口与模块组装
├── search_engine.py          # 最优路径搜索
├── state.py                  # 战斗状态七元组
├── event_system.py           # 事件系统与模块接口
├── external_api.py           # 速度轴/伤害计算 API 抽象与 Mock
├── tactical_report.py        # 战术建议书与 LLM 输出适配
├── archetype_interface.py    # 流派层接口
├── data_loader.py            # 示例数据加载
├── data/
│   ├── characters/
│   └── enemies/
├── config.yaml
├── ARCHITECTURE.md
└── CODING_STANDARDS.md
```

## 快速验证

```powershell
python -m py_compile config_loader.py state.py event_system.py search_engine.py archetype_interface.py battle_engine.py data_loader.py
```

```powershell
python -c "from data_loader import load_characters, load_enemies; print(load_characters().keys()); print(load_enemies().keys())"
```

```powershell
python smoke_test.py
```

## 外部 API 与 LLM 输出

`external_api.py` 定义了两个可替换接口：

- `SpeedAxisApi.simulate_axis(state, window_av)`：封装社区速度轴模拟器或企业内部速度轴服务。
- `DamageCalcApi.calculate_damage(state, action)`：封装即时伤害演算工具。

当前仓库提供 `MockSpeedAxisApi` 和 `MockDamageCalcApi`，用于验证接口形状和引擎注入流程。切换真实服务时，新建符合签名的类并传入 `BattleEngine(external_api_bundle=...)` 即可。

`tactical_report.py` 将 `TacticalAdvice` 渲染为战术建议书，并支持传入 `LLMReportClient` 生成自然语言润色版。默认 `TemplateLLMReportClient` 不访问网络，只用于本地闭环验证。

## 设计说明

详见 [ARCHITECTURE.md](ARCHITECTURE.md)。
