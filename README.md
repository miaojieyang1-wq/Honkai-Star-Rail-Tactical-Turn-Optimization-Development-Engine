# 星铁战术引擎

一个建立在精确数学公式之上的回合制战斗推演系统骨架，用于在给定战斗状态下，搜索有限行动值窗口内的最优操作序列，并输出多分支战术建议。

## 项目亮点

- 七元组战斗状态：`S_t = (Q_t, E_t, SP_t, T_t, B_t, DOT_t, D_t)`
- 模块独占写入：轴、能量、战技点、韧性、Buff、DoT、伤害累积各自拥有状态变量
- 同步确定性事件总线：跨模块通信只通过 `EventBus`
- 搜索引擎：深度优先搜索、启发式剪枝、受击分支剩余窗口搜索
- 流派插件接口：记忆、欢愉等扩展层不直接写核心状态
- 示例数据管道：角色/敌人样例数据从 `config.yaml` 路径配置读取

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

## 设计说明

详见 [ARCHITECTURE.md](ARCHITECTURE.md)。
