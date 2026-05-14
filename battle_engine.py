# -*- coding: utf-8 -*-
"""Main assembly entry for the Star Rail tactical battle engine."""

from copy import deepcopy
from dataclasses import dataclass
import logging
from typing import Mapping, Tuple

from archetype_interface import ArchetypeInterface
from config_loader import get_config
from event_system import (
    AXIS_EVENT_TYPES,
    BUFF_EVENT_TYPES,
    DAMAGE_EVENT_TYPES,
    DOT_EVENT_TYPES,
    ENERGY_EVENT_TYPES,
    FUA_EVENT_TYPES,
    HIT_EVENT_TYPES,
    SP_EVENT_TYPES,
    TOUGHNESS_EVENT_TYPES,
    AxisModuleInterface,
    BuffModuleInterface,
    DOTModuleInterface,
    DamageModuleInterface,
    DamageSettlementEvent,
    DamageType,
    EnergyModuleInterface,
    Event,
    EventBus,
    EventType,
    FUAModuleInterface,
    HitModuleInterface,
    ModuleType,
    SPModuleInterface,
    ToughnessModuleInterface,
)
from search_engine import (
    ActionType,
    SearchEngine,
    TRANSITION_EVENT_ORDER,
)
from state import BattleState, UnitType


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class TacticalAdvice:
    """Player-readable tactical recommendation."""

    baseline_axis: Tuple[str, ...]
    estimated_total_damage: float
    resource_projection: Mapping[str, object]
    hit_branch_plans: Tuple[Mapping[str, object], ...]
    assumptions: Tuple[str, ...]

    def to_text(self):
        """Format tactical advice as readable text."""
        lines = [
            "基准轴：{0}".format(" -> ".join(self.baseline_axis) or "无可执行我方行动"),
            "预估总伤害：{0}".format(self.estimated_total_damage),
            "资源余量预测：{0}".format(self.resource_projection),
            "受击分支预案：",
        ]

        if not self.hit_branch_plans:
            lines.append("- 无受击分支，或未提供确定性受击分类数据")
        else:
            for plan in self.hit_branch_plans:
                lines.append(
                    "- {0}：{1}；伤害变化 {2}".format(
                        plan["branch_id"],
                        " -> ".join(plan["actions"]) or "无变招",
                        plan["damage_delta"],
                    )
                )

        lines.append("前提假设列表：")
        for assumption in self.assumptions:
            lines.append("- {0}".format(assumption))

        return "\n".join(lines)


class BattleEngineConfigurationError(RuntimeError):
    """Raised when battle engine input state is incomplete."""


class AxisModule(AxisModuleInterface):
    """Axis module owning action queue updates."""

    def __init__(self, state):
        super().__init__()
        self._queue = state.action_queue

    def handle_event(self, event):
        payload = event.payload
        unit_id = payload.get("unit_id")

        if event.event_type in (EventType.SUMMON_ACTOR, EventType.MEMOSPRITE_SUMMON):
            unit_type = _unit_type_from_payload(payload)
            self._queue.update_insert(unit_id, payload.get("action_value", 0.0), unit_type)
            return

        if event.event_type in (EventType.REMOVE_ACTOR, EventType.MEMOSPRITE_EXIT):
            self._queue.update_remove(unit_id)
            return

        if unit_id is None:
            return

        item = self._queue.get_unit(unit_id)
        if item is None:
            return

        if "action_value" in payload:
            self._queue.update_action_value(unit_id, payload["action_value"])
            return

        amount = float(payload.get("amount", payload.get("delta", 0.0)))
        if event.event_type is EventType.ACTION_ADVANCE:
            self._queue.update_action_value(unit_id, item.action_value - amount)
        elif event.event_type in (EventType.ACTION_DELAY, EventType.ACTION_POSTPONE):
            self._queue.update_action_value(unit_id, item.action_value + amount)


class EnergyModule(EnergyModuleInterface):
    """Energy module owning energy vector updates."""

    def __init__(self, state):
        super().__init__()
        self._energy = state.energy

    def handle_event(self, event):
        if event.event_type not in ENERGY_EVENT_TYPES:
            return

        unit_id = event.payload.get("unit_id")
        if unit_id is None:
            return

        if "energy_max" in event.payload:
            self._energy.update_energy_max(unit_id, event.payload["energy_max"])

        amount = float(event.payload.get("amount", 0.0))
        self._energy.update_add_energy(unit_id, amount)


class SPModule(SPModuleInterface):
    """Skill-point module owning SP updates."""

    def __init__(self, state):
        super().__init__()
        self._sp = state.skill_points

    def handle_event(self, event):
        amount = int(event.payload.get("amount", 1))

        if event.event_type is EventType.SKILL_SP_CONSUME:
            for _ in range(amount):
                self._sp.update_consume_one()
        elif event.event_type is EventType.BASIC_ATTACK_SP_RECOVER:
            for _ in range(amount):
                self._sp.update_recover_one()
        elif event.event_type is EventType.SP_MAX_CHANGE:
            sp_max = event.payload.get("sp_max", event.payload.get("new_max"))
            if sp_max is not None:
                self._sp.update_sp_max(sp_max)


class ToughnessModule(ToughnessModuleInterface):
    """Toughness module owning toughness vector updates."""

    def __init__(self, state):
        super().__init__()
        self._toughness = state.toughness

    def handle_event(self, event):
        enemy_id = event.payload.get("enemy_id", event.payload.get("target_id"))
        if enemy_id is None:
            return

        if event.event_type is EventType.TOUGHNESS_REDUCE_REQUEST:
            before = self._toughness.get_toughness(enemy_id)
            self._toughness.update_reduce_toughness(
                enemy_id,
                event.payload.get("amount", 0.0),
                event.payload.get("attack_element"),
                event.payload.get("target_weakness_list", ()),
                event.payload.get("ignore_weakness_flag", False),
            )
            after = self._toughness.get_toughness(enemy_id)
            if before > 0.0 and after <= 0.0:
                self.emit_event(
                    Event(
                        EventType.BREAK_TRIGGERED,
                        {"enemy_id": enemy_id},
                        "toughness_module",
                    )
                )
        elif event.event_type is EventType.TOUGHNESS_RECOVER:
            current = self._toughness.get_toughness(enemy_id)
            self._toughness.update_set_toughness(
                enemy_id,
                current + float(event.payload.get("amount", 0.0)),
            )


class BuffModule(BuffModuleInterface):
    """Buff module owning buff list updates."""

    def __init__(self, state):
        super().__init__()
        self._buffs = state.buffs

    def handle_event(self, event):
        if event.event_type is EventType.BUFF_APPLY:
            self._buffs.update_apply(
                event.payload["buff_id"],
                event.payload["target_id"],
                event.payload.get("remaining_duration", event.payload.get("duration", 1.0)),
                event.payload.get("remaining_layers", event.payload.get("layers", 1)),
                event.payload.get("effect_tags", ()),
            )
        elif event.event_type is EventType.BUFF_EXPIRE:
            buff_id = event.payload.get("buff_id")
            if buff_id is None:
                self._buffs.update_remove_expired()
            else:
                self._buffs.update_remove(buff_id, event.payload.get("target_id"))


class DOTModule(DOTModuleInterface):
    """DoT module owning DoT list updates."""

    def __init__(self, state):
        super().__init__()
        self._dots = state.dots

    def handle_event(self, event):
        if event.event_type is EventType.DOT_APPLY:
            self._dots.update_apply(
                event.payload["dot_id"],
                event.payload["source_id"],
                event.payload["target_id"],
                event.payload["dot_type"],
                event.payload.get("remaining_layers", event.payload.get("layers", 1)),
                event.payload.get("max_layers", 1),
                event.payload.get("remaining_turns", event.payload.get("turns", 1)),
            )
        elif event.event_type is EventType.DOT_END_TURN_SETTLE_TRIGGER:
            damage_value = float(event.payload.get("damage_value", 0.0))
            if damage_value:
                self.emit_event(
                    DamageSettlementEvent(
                        DamageType.DOT,
                        damage_value,
                        "dot_module",
                        {"target_id": event.payload.get("target_id")},
                    )
                )
            self._dots.update_end_turn_settle(event.payload.get("target_id"))
        elif event.event_type is EventType.DOT_IMMEDIATE_SETTLE_TRIGGER:
            damage_value = float(event.payload.get("damage_value", 0.0))
            if damage_value:
                self.emit_event(
                    DamageSettlementEvent(
                        DamageType.DOT_INSTANT,
                        damage_value,
                        "dot_module",
                        {"target_id": event.payload.get("target_id")},
                    )
                )
            self._dots.update_immediate_settle(event.payload.get("target_id"))


class DamageAccumulator(DamageModuleInterface):
    """Damage module owning D_acc updates."""

    def __init__(self, state):
        super().__init__()
        self._state = state

    def handle_event(self, event):
        if event.event_type in (EventType.DAMAGE_RESET, EventType.WINDOW_INIT):
            self._state.update_reset_damage()
            return
        if event.event_type is not EventType.DAMAGE_SETTLEMENT:
            return
        self._state.update_add_damage(event.payload.get("damage_value", 0.0))


class HitModule(HitModuleInterface):
    """Hit module placeholder for deterministic hit-analysis event handling."""

    def handle_event(self, event):
        return None

    def generate_hit_branches(self, state, enemy_nodes, max_branches):
        """Generate deterministic hit branches from taunt weights."""
        character_ids = list(state.get_character_unit_ids())
        if not character_ids:
            return tuple()

        from search_engine import HitBranch

        branches = []
        for enemy_node in enemy_nodes:
            weighted_targets = [
                (character_id, _taunt_weight(character_id, state))
                for character_id in character_ids
            ]
            total_weight = sum(weight for _, weight in weighted_targets)
            if total_weight <= 0.0:
                weighted_targets = [(character_id, 1.0) for character_id in character_ids]
                total_weight = float(len(weighted_targets))
            ranked = sorted(
                weighted_targets,
                key=lambda item: (-item[1] / total_weight, item[0]),
            )
            for rank, (character_id, weight) in enumerate(ranked[:max_branches], start=1):
                branches.append(
                    HitBranch(
                        "hit:{0}:{1}".format(enemy_node.unit_id, rank),
                        enemy_node.unit_id,
                        enemy_node.action_value,
                        character_id,
                        float(get_config("hit_params.default_hit_energy")),
                        weight,
                        weight / total_weight,
                        {"source": "hit_module_taunt_distribution"},
                    )
                )
        return tuple(branches)


class FUAModule(FUAModuleInterface):
    """Follow-up attack module placeholder for deterministic trigger checks."""

    def __init__(self, condition_tags=None):
        super().__init__()
        self.condition_tags = tuple(condition_tags or ())
        self._pending_data = []
        if not self.condition_tags:
            self._pending_data.append("fua_condition_tags")
            LOGGER.warning("追加攻击触发条件数据未填充，模块处于待激活状态。")

    @property
    def pending_data(self):
        """Return missing data required to activate FUA trigger checks."""
        return tuple(self._pending_data)

    def check_trigger(self, context):
        """Return whether the current context satisfies FUA trigger conditions."""
        if not self.condition_tags:
            return False
        context_tags = set(context.get("trigger_tags", ()))
        return all(tag in context_tags for tag in self.condition_tags)

    def handle_event(self, event):
        if event.event_type is EventType.FUA_TRIGGER_CHECK:
            triggered = bool(event.payload.get("triggered"))
            if not triggered:
                triggered = self.check_trigger(event.payload.get("context", {}))
            if triggered:
                damage_value = float(event.payload.get("damage_value", 0.0))
                if damage_value:
                    self.emit_event(
                        DamageSettlementEvent(
                            DamageType.FUA,
                            damage_value,
                            "fua_module",
                            {"status": "data_filled"},
                        )
                    )
        return None


class ArchetypeEventAdapter(FUAModuleInterface):
    """Adapter allowing archetypes to subscribe to EventBus events."""

    module_type = ModuleType.FUA

    def __init__(self, archetype):
        super().__init__()
        self._archetype = archetype

    def attach_event_bus(self, event_bus):
        super().attach_event_bus(event_bus)
        self._archetype.attach_event_bus(event_bus)

    def handle_event(self, event):
        self._archetype.handle_event(event)


class BattleEngine:
    """Complete battle engine assembly and tactical planning facade."""

    def __init__(
        self,
        initial_state=None,
        archetypes=None,
        legal_action_provider=None,
        action_definition_provider=None,
        action_event_builder=None,
        damage_upper_bound_provider=None,
        hit_branch_provider=None,
        damage_executor=None,
        team_character_unit_ids=None,
    ):
        self.state = initial_state or BattleState()
        self._ensure_character_registry(self.state, team_character_unit_ids)
        self.archetypes = tuple(archetypes or ())
        self.legal_action_provider = legal_action_provider
        self.action_definition_provider = action_definition_provider
        if self.action_definition_provider is None:
            LOGGER.warning("动作定义提供器未接入，默认仅执行基础资源合法性约束。")
        self.action_event_builder = action_event_builder
        self.damage_upper_bound_provider = damage_upper_bound_provider
        self.hit_branch_provider = hit_branch_provider
        self.damage_executor = damage_executor

        self.event_bus = EventBus()
        self.modules = self._register_core_modules(self.state, self.event_bus)
        self._register_query_handlers(self.state, self.event_bus)
        self._register_archetypes(self.state, self.event_bus)

        self.search_engine = self._create_search_engine()

    def initialize_state(self, state_0=None, team_character_unit_ids=None):
        """Set S_0 and rebuild module wiring around it."""
        self.state = state_0 or BattleState()
        self._ensure_character_registry(self.state, team_character_unit_ids)
        self.event_bus = EventBus()
        self.modules = self._register_core_modules(self.state, self.event_bus)
        self._register_query_handlers(self.state, self.event_bus)
        self._register_archetypes(self.state, self.event_bus)
        self.search_engine = self._create_search_engine()
        return self.state

    def plan_optimal(self, state_0=None, delta_t=None, gamma=None, max_hit_branches=None):
        """Return a tactical advice object for the given initial state and window."""
        state = deepcopy(state_0 or self.state)
        if not state.get_character_unit_ids():
            raise BattleEngineConfigurationError(
                "BattleState.character_unit_ids must be registered before planning"
            )
        branch_result = self.search_engine.handle_hit_branches(
            state,
            _default_window_av(delta_t),
            _default_hit_branches(max_hit_branches),
        )
        final_state = self._simulate_path(state, branch_result.baseline.path)
        return self._format_tactical_advice(
            branch_result,
            final_state,
            delta_t,
            gamma,
            max_hit_branches,
        )

    def _create_search_engine(self):
        return SearchEngine(
            event_bus=self.event_bus,
            legal_action_provider=self.legal_action_provider,
            action_definition_provider=self.action_definition_provider,
            transition_executor=self._execute_transition,
            action_event_builder=self.action_event_builder,
            damage_upper_bound_provider=self.damage_upper_bound_provider,
            hit_branch_provider=self.hit_branch_provider or self._generate_hit_branches,
            hit_branch_applier=self._apply_hit_branch,
        )

    def _register_core_modules(self, state, event_bus):
        modules = {
            "axis": AxisModule(state),
            "energy": EnergyModule(state),
            "sp": SPModule(state),
            "toughness": ToughnessModule(state),
            "buff": BuffModule(state),
            "dot": DOTModule(state),
            "damage": DamageAccumulator(state),
            "hit": HitModule(),
            "fua": FUAModule(),
        }
        event_bus.register(modules["axis"], AXIS_EVENT_TYPES)
        event_bus.register(modules["axis"], (EventType.MEMOSPRITE_SUMMON, EventType.MEMOSPRITE_EXIT))
        event_bus.register(modules["energy"], ENERGY_EVENT_TYPES)
        event_bus.register(modules["sp"], SP_EVENT_TYPES)
        event_bus.register(modules["toughness"], TOUGHNESS_EVENT_TYPES)
        event_bus.register(modules["buff"], BUFF_EVENT_TYPES)
        event_bus.register(modules["dot"], DOT_EVENT_TYPES)
        event_bus.register(modules["damage"], DAMAGE_EVENT_TYPES)
        event_bus.register(modules["hit"], HIT_EVENT_TYPES)
        event_bus.register(modules["fua"], FUA_EVENT_TYPES)
        return modules

    def _register_query_handlers(self, state, event_bus):
        event_bus.register_query_handler("state.full", lambda payload: state.get_state_tuple())
        event_bus.register_query_handler(
            "state.character_unit_ids",
            lambda payload: state.get_character_unit_ids(),
        )
        event_bus.register_query_handler(
            "state.action_queue",
            lambda payload: state.action_queue.get_items(),
        )
        event_bus.register_query_handler(
            "state.energy",
            lambda payload: state.energy.get_all_energy(),
        )
        event_bus.register_query_handler(
            "state.skill_points",
            lambda payload: {
                "current": state.skill_points.get_current_sp(),
                "max": state.skill_points.get_sp_max(),
            },
        )
        event_bus.register_query_handler(
            "state.toughness",
            lambda payload: state.toughness.get_snapshot(),
        )
        event_bus.register_query_handler("state.buffs", lambda payload: state.buffs.get_all())
        event_bus.register_query_handler("state.dots", lambda payload: state.dots.get_all())
        event_bus.register_query_handler(
            "state.damage_accumulated",
            lambda payload: state.get_damage_accumulated(),
        )

    def _register_archetypes(self, state, event_bus):
        for archetype in self.archetypes:
            if not isinstance(archetype, ArchetypeInterface):
                raise TypeError("archetype must implement ArchetypeInterface")
            adapter = ArchetypeEventAdapter(archetype)
            event_bus.register(adapter, tuple(EventType))
            archetype.initialize(state)

    def _register_archetype_clones(self, state, event_bus):
        if not self.archetypes:
            return
        for archetype in deepcopy(self.archetypes):
            adapter = ArchetypeEventAdapter(archetype)
            event_bus.register(adapter, tuple(EventType))
            archetype.initialize(state)

    def _ensure_character_registry(self, state, team_character_unit_ids):
        if team_character_unit_ids is not None:
            if state.get_character_unit_ids():
                if state.get_character_unit_ids() != tuple(team_character_unit_ids):
                    raise BattleEngineConfigurationError(
                        "team_character_unit_ids conflicts with registered character set C"
                    )
                return
            state.update_register_characters(team_character_unit_ids)
            return
        if state.get_character_unit_ids():
            return
        raise BattleEngineConfigurationError(
            "BattleState.character_unit_ids must be registered before engine initialization"
        )

    def _execute_transition(self, state, action, event_bus, control_event=None):
        local_bus = EventBus()
        self._register_core_modules(state, local_bus)
        self._register_query_handlers(state, local_bus)
        self._register_archetype_clones(state, local_bus)
        if control_event is not None:
            local_bus.emit(control_event)
            return
        events = tuple(self._build_action_events(action, state))
        for event in _order_events(events):
            local_bus.emit(event)

        if self.damage_executor is not None:
            damage = self.damage_executor(state, action)
            local_bus.emit(
                DamageSettlementEvent(
                    DamageType.NORMAL,
                    damage,
                    "battle_engine",
                    {"unit_id": action.unit_id, "action_type": action.action_type.value},
                )
            )

    def _build_action_events(self, action, state):
        if self.action_event_builder is not None:
            return self.action_event_builder(action, state)

        if action.action_type is ActionType.BASIC_ATTACK:
            return (
                Event(
                    EventType.BASIC_ATTACK_SP_RECOVER,
                    {"unit_id": action.unit_id, "amount": 1},
                    "battle_engine",
                ),
            )
        if action.action_type is ActionType.SKILL:
            return (
                Event(
                    EventType.SKILL_SP_CONSUME,
                    {"unit_id": action.unit_id, "amount": 1},
                    "battle_engine",
                ),
            )
        return tuple()

    def _apply_hit_branch(self, state, branch, event_bus):
        local_bus = EventBus()
        self._register_core_modules(state, local_bus)
        self._register_query_handlers(state, local_bus)
        self._register_archetype_clones(state, local_bus)
        local_bus.emit(
            Event(
                EventType.HIT_ENERGY_GAIN,
                {
                    "unit_id": branch.hit_target_id,
                    "amount": branch.hit_energy_gain,
                    "branch_id": branch.branch_id,
                },
                "battle_engine",
            )
        )

    def _generate_hit_branches(self, state, enemy_nodes, max_branches):
        return HitModule().generate_hit_branches(state, enemy_nodes, max_branches)

    def _simulate_path(self, state, path):
        final_state = deepcopy(state)
        self._execute_transition(
            final_state,
            None,
            self.event_bus,
            control_event=Event(EventType.WINDOW_INIT, {}, "battle_engine"),
        )
        for action in path:
            self._execute_transition(final_state, action, self.event_bus)
        return final_state

    def _format_tactical_advice(
        self,
        branch_result,
        final_state,
        delta_t,
        gamma,
        max_hit_branches,
    ):
        baseline_axis = tuple(_format_action(action) for action in branch_result.baseline.path)
        branch_plans = tuple(
            {
                "branch_id": plan.branch.branch_id,
                "hit_target_id": plan.branch.hit_target_id,
                "branch_weight": plan.branch.weight,
                "actions": tuple(_format_action(action) for action in plan.result.path),
                "estimated_damage": plan.result.score,
                "damage_delta": plan.damage_delta,
            }
            for plan in branch_result.branches
        )
        return TacticalAdvice(
            baseline_axis=baseline_axis,
            estimated_total_damage=branch_result.baseline.score,
            resource_projection=_resource_projection(final_state),
            hit_branch_plans=branch_plans,
            assumptions=self._assumptions(delta_t, gamma, max_hit_branches),
        )

    def _assumptions(self, delta_t, gamma, max_hit_branches):
        assumptions = [
            "窗口长度 ΔT = {0} AV，来源：调用参数或 config.yaml:search_params.default_window_av".format(
                _default_window_av(delta_t)
            ),
            "容忍度 γ = {0}，来源：调用参数或 config.yaml:search_params.tolerance_gamma".format(
                _default_gamma(gamma)
            ),
            "受击分支数 M = {0}，来源：调用参数或 config.yaml:hit_params.max_hit_branches".format(
                _default_hit_branches(max_hit_branches)
            ),
            "状态转移通过 EventBus 同步执行，底层模块只写入自身独占状态变量",
        ]
        if self.damage_executor is None:
            assumptions.append("未接入伤害执行器，预估伤害仅反映当前 D_acc 与资源惩罚")
        if self.hit_branch_provider is None:
            assumptions.append("未接入自定义受击分支提供器，使用默认确定性仇恨分布生成变招分支。")
        else:
            assumptions.append("使用用户自定义受击分支提供器生成变招分支。")
        return tuple(assumptions)


def plan_optimal(state_0, delta_t=None, gamma=None, max_hit_branches=None):
    """Convenience function returning tactical advice with default engine wiring."""
    if not state_0.get_character_unit_ids():
        raise BattleEngineConfigurationError(
            "BattleState.character_unit_ids must be registered before planning"
        )
    return BattleEngine(initial_state=state_0).plan_optimal(
        state_0,
        delta_t,
        gamma,
        max_hit_branches,
    )


def _unit_type_from_payload(payload):
    unit_type = payload.get("unit_type", UnitType.CHARACTER)
    if isinstance(unit_type, UnitType):
        return unit_type
    return UnitType(unit_type)


def _order_events(events):
    priority = {event_type: index for index, event_type in enumerate(TRANSITION_EVENT_ORDER)}
    return tuple(sorted(events, key=lambda event: priority.get(event.event_type, len(priority))))


def _format_action(action):
    return "{0}:{1}".format(action.unit_id, action.action_type.value)


def _resource_projection(state):
    return {
        "energy": state.energy.get_all_energy(),
        "skill_points": {
            "current": state.skill_points.get_current_sp(),
            "max": state.skill_points.get_sp_max(),
        },
    }


def _taunt_weight(unit_id, state):
    base_taunt = None
    taunt_modifier = 1.0
    for buff in state.buffs.get_by_target(unit_id):
        for tag in buff.effect_tags:
            if tag.startswith("base_taunt:"):
                base_taunt = float(tag.split(":", 1)[1])
            elif tag.startswith("taunt_modifier:"):
                taunt_modifier *= float(tag.split(":", 1)[1])
    if base_taunt is None:
        base_taunt = 1.0
    return base_taunt * taunt_modifier


def _required_config(key):
    value = get_config(key)
    if value is None:
        raise KeyError("missing required config value: {0}".format(key))
    return value


def _default_window_av(delta_t):
    if delta_t is not None:
        return float(delta_t)
    return float(_required_config("search_params.default_window_av"))


def _default_gamma(gamma):
    if gamma is not None:
        return float(gamma)
    return float(_required_config("search_params.tolerance_gamma"))


def _default_hit_branches(max_hit_branches):
    if max_hit_branches is not None:
        return int(max_hit_branches)
    return int(_required_config("hit_params.max_hit_branches"))
