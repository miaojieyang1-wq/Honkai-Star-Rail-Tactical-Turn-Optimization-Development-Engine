# -*- coding: utf-8 -*-
"""Optimal path search engine for finite action-value windows."""

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, List, Mapping, Optional, Sequence, Tuple

from config_loader import get_config
from event_system import Event, EventBus, EventType
from state import BattleState, UnitType


class SearchEngineConfigurationError(RuntimeError):
    """Raised when SearchEngine is not wired to required engine modules."""


class ActionType(Enum):
    """Search-level operation choices."""

    BASIC_ATTACK = "basic_attack"
    SKILL = "skill"
    ULTIMATE = "ultimate"
    ELATION_SKILL = "elation_skill"
    MEMOSPRITE_SKILL = "memosprite_skill"


@dataclass(frozen=True)
class ActionDefinition:
    """Metadata used to decide whether an action is legal."""

    action_type: ActionType
    attack_type: str = "none"
    element: Optional[str] = None
    target_id: Optional[str] = None
    target_weakness_list: Tuple[str, ...] = tuple()
    ignore_weakness: bool = False
    sp_cost: int = 0
    requires_full_energy: bool = False
    reduces_toughness: bool = False
    tags: Tuple[str, ...] = tuple()


@dataclass(frozen=True)
class ActionNode:
    """Friendly action node extracted from an action-value window."""

    unit_id: str
    action_value: float
    unit_type: UnitType


@dataclass(frozen=True)
class SearchAction:
    """Concrete operation selected for one action node."""

    unit_id: str
    action_type: ActionType
    action_value: float
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResult:
    """Search result for an operation path."""

    path: Tuple[SearchAction, ...]
    score: float


@dataclass(frozen=True)
class HitBranch:
    """Deterministic hit-analysis branch."""

    branch_id: str
    enemy_unit_id: str
    enemy_action_value: float
    hit_target_id: str
    hit_energy_gain: float
    weight: float = 1.0
    probability: float = 0.0
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class HitBranchPlan:
    """Search output for one hit branch."""

    branch: HitBranch
    result: SearchResult
    damage_delta: float


@dataclass(frozen=True)
class HitBranchResult:
    """Baseline and branch-specific tactical plans."""

    baseline: SearchResult
    branches: Tuple[HitBranchPlan, ...]


TransitionExecutor = Callable[[BattleState, Optional[SearchAction], EventBus], None]
LegalActionProvider = Callable[[str, BattleState], Sequence[ActionType]]
ActionDefinitionProvider = Callable[[str, BattleState], Sequence[ActionDefinition]]
DamageUpperBoundProvider = Callable[[str, BattleState], float]
HitBranchProvider = Callable[[BattleState, Sequence[ActionNode], int], Sequence[HitBranch]]
EventBuilder = Callable[[SearchAction, BattleState], Iterable[Event]]
HitBranchApplier = Callable[[BattleState, HitBranch, EventBus], None]


TRANSITION_EVENT_ORDER = (
    EventType.ACTION_ADVANCE,
    EventType.ACTION_DELAY,
    EventType.SPEED_UP,
    EventType.SLOW_DOWN,
    EventType.SUMMON_ACTOR,
    EventType.REMOVE_ACTOR,
    EventType.ACTION_POSTPONE,
    EventType.TOUGHNESS_REDUCE_REQUEST,
    EventType.BREAK_TRIGGERED,
    EventType.TOUGHNESS_RECOVER,
    EventType.BUFF_APPLY,
    EventType.BUFF_EXPIRE,
    EventType.ACTION_ENERGY_GAIN,
    EventType.ASSIST_ENERGY_GAIN,
    EventType.HIT_ENERGY_GAIN,
    EventType.MEMOSPRITE_ENERGY_GAIN,
    EventType.SKILL_SP_CONSUME,
    EventType.BASIC_ATTACK_SP_RECOVER,
    EventType.SP_MAX_CHANGE,
    EventType.BURST_POINT_SUBSTITUTE,
    EventType.ENEMY_ACTION_HIT_ANALYSIS_TRIGGER,
    EventType.DOT_END_TURN_SETTLE_TRIGGER,
    EventType.DAMAGE_SETTLEMENT,
)


FRIENDLY_UNIT_TYPES = (
    UnitType.CHARACTER,
    UnitType.MEMOSPRITE,
    UnitType.AHA_MOMENT,
)


ENEMY_UNIT_TYPES = (
    UnitType.ENEMY,
)


class SearchEngine:
    """Depth-first optimizer with upper-bound pruning and hit branch handling."""

    def __init__(
        self,
        event_bus=None,
        legal_action_provider=None,
        action_definition_provider=None,
        transition_executor=None,
        action_event_builder=None,
        damage_upper_bound_provider=None,
        hit_branch_provider=None,
        hit_branch_applier=None,
    ):
        if transition_executor is None:
            raise SearchEngineConfigurationError(
                "SearchEngine requires transition_executor so WINDOW_INIT is handled"
            )
        self.event_bus = event_bus or EventBus()
        self.legal_action_provider = legal_action_provider
        self.action_definition_provider = action_definition_provider
        self.transition_executor = transition_executor
        self.action_event_builder = action_event_builder
        self.damage_upper_bound_provider = damage_upper_bound_provider
        self.hit_branch_provider = hit_branch_provider
        self.hit_branch_applier = hit_branch_applier
        self.energy_penalty_lambda = float(
            _required_config("search_params.energy_penalty_lambda")
        )
        self.sp_penalty_lambda = float(_required_config("search_params.sp_penalty_lambda"))
        self.energy_safe_alpha = float(_required_config("search_params.energy_safe_alpha"))

    def extract_action_window(self, state, delta_t=None, t_start=None, t_end=None):
        """Extract friendly action nodes with t in [t_start, t_end]."""
        delta_t = _default_window_av(delta_t)
        items = state.action_queue.get_items()
        if not items:
            return []

        if t_start is None:
            t_start = items[0].action_value
        if t_end is None:
            t_end = t_start + delta_t
        nodes = [
            ActionNode(item.unit_id, item.action_value, item.unit_type)
            for item in items
            if item.unit_type in FRIENDLY_UNIT_TYPES
            and t_start <= item.action_value <= t_end
        ]
        return sorted(nodes, key=lambda node: node.action_value)

    def get_legal_actions(self, unit_id, state):
        """Return legal actions for a unit under current resource constraints."""
        if self.legal_action_provider is not None:
            return list(self.legal_action_provider(unit_id, state))

        actions = []
        for definition in self._get_action_definitions(unit_id, state):
            if self._is_action_definition_legal(definition, unit_id, state):
                actions.append(definition.action_type)

        return actions

    def evaluate_path(self, path, state_0):
        """Execute a full path from S_0 and return J."""
        state = deepcopy(state_0)
        self._reset_window_damage(state)

        for action in path:
            self._execute_action(state, action)

        return self._score_state(state)

    def heuristic_upper_bound(self, partial_path, state_0, remaining_steps):
        """Return U_k: accumulated damage plus unconstrained best remaining damage."""
        state = deepcopy(state_0)
        self._reset_window_damage(state)

        for action in partial_path:
            self._execute_action(state, action)

        upper_damage = state.get_damage_accumulated()
        for unit_id in remaining_steps:
            upper_damage += self._max_theoretical_damage(unit_id, state)

        return upper_damage

    def search_optimal(self, state_0, delta_t=None, gamma=None, t_start=None, t_end=None):
        """Search for the best path and score inside an action-value window."""
        gamma = _default_gamma(gamma)
        initial_state = deepcopy(state_0)
        self._reset_window_damage(initial_state)
        if t_start is None:
            t_start = self._window_start(initial_state)
        if t_end is None:
            t_end = t_start + _default_window_av(delta_t)
        nodes = self.extract_action_window(initial_state, delta_t, t_start, t_end)

        if not nodes:
            return SearchResult(tuple(), 0.0)

        best_result = SearchResult(tuple(), float("-inf"))
        stop_requested = {"value": False}

        def dfs(index, current_state, current_path):
            nonlocal best_result

            if stop_requested["value"]:
                return

            remaining_nodes = nodes[index:]
            upper_bound = self._upper_bound_from_state(current_state, remaining_nodes)
            if upper_bound <= best_result.score:
                return

            if index >= len(nodes):
                score = self._score_state(current_state)
                previous_best = best_result.score
                if score > best_result.score:
                    best_result = SearchResult(tuple(current_path), score)
                if previous_best != float("-inf") and score >= gamma * previous_best:
                    stop_requested["value"] = True
                return

            node = nodes[index]
            legal_actions = self.get_legal_actions(node.unit_id, current_state)
            for action_type in legal_actions:
                action = SearchAction(
                    node.unit_id,
                    action_type,
                    node.action_value,
                    self._metadata_for_action(node.unit_id, action_type, current_state),
                )
                next_state = deepcopy(current_state)
                self._execute_action(next_state, action)
                current_path.append(action)
                dfs(index + 1, next_state, current_path)
                current_path.pop()

        dfs(0, initial_state, [])

        if best_result.score == float("-inf"):
            return SearchResult(tuple(), self._score_state(initial_state))
        return best_result

    def handle_hit_branches(self, state_0, delta_t=None, max_branches=None):
        """Return baseline and deterministic hit-branch tactical plans."""
        delta_t = _default_window_av(delta_t)
        max_branches = _default_hit_branches(max_branches)
        original_start = self._window_start(state_0)
        original_end = original_start + delta_t
        baseline = self.search_optimal(state_0, delta_t, t_start=original_start, t_end=original_end)
        enemy_nodes = self._extract_enemy_window(state_0, original_start, original_end)

        if not enemy_nodes:
            return HitBranchResult(baseline, tuple())

        if self.hit_branch_provider is None:
            branches = self._default_hit_branches(state_0, enemy_nodes, max_branches)
        else:
            branches = self.hit_branch_provider(state_0, enemy_nodes, max_branches)

        branch_plans = []
        for branch in branches:
            branched_state = deepcopy(state_0)
            branched_state.action_queue.update_prune_before(
                branch.enemy_action_value,
                inclusive=True,
            )
            if self.hit_branch_applier is not None:
                self.hit_branch_applier(branched_state, branch, self.event_bus)
            result = self.search_optimal(
                branched_state,
                delta_t,
                t_start=branch.enemy_action_value,
                t_end=original_end,
            )
            branch_plans.append(
                HitBranchPlan(branch, result, result.score - baseline.score)
            )

        return HitBranchResult(baseline, tuple(branch_plans))

    def _extract_enemy_window(self, state, t_start, t_end):
        items = state.action_queue.get_items()
        if not items:
            return []

        nodes = [
            ActionNode(item.unit_id, item.action_value, item.unit_type)
            for item in items
            if item.unit_type in ENEMY_UNIT_TYPES and t_start <= item.action_value <= t_end
        ]
        return sorted(nodes, key=lambda node: node.action_value)

    def _window_start(self, state):
        items = state.action_queue.get_items()
        if not items:
            return 0.0
        return items[0].action_value

    def _execute_action(self, state, action):
        if self.transition_executor is not None:
            self.transition_executor(state, action, self.event_bus)
            return

        for event in self._build_default_events(action, state):
            self.event_bus.emit(event)

    def _build_default_events(self, action, state):
        if self.action_event_builder is not None:
            return self._order_events(self.action_event_builder(action, state))

        if action.action_type is ActionType.BASIC_ATTACK:
            return (
                Event(
                    EventType.BASIC_ATTACK_SP_RECOVER,
                    {"unit_id": action.unit_id, "amount": 1},
                    "search_engine",
                ),
            )
        if action.action_type is ActionType.SKILL:
            return (
                Event(
                    EventType.SKILL_SP_CONSUME,
                    {"unit_id": action.unit_id, "amount": 1},
                    "search_engine",
                ),
            )
        return tuple()

    def _order_events(self, events):
        priority = {
            event_type: index for index, event_type in enumerate(TRANSITION_EVENT_ORDER)
        }
        return tuple(
            sorted(
                events,
                key=lambda event: priority.get(event.event_type, len(priority)),
            )
        )

    def _score_state(self, state):
        damage = state.get_damage_accumulated()
        energy_penalty = self._energy_depletion_penalty(state)
        sp_penalty = self._sp_depletion_penalty(state)
        return damage - self.energy_penalty_lambda * energy_penalty - self.sp_penalty_lambda * sp_penalty

    def _energy_depletion_penalty(self, state):
        penalty = 0.0
        character_unit_ids = state.get_character_unit_ids()
        for unit_id in character_unit_ids:
            energy = state.energy.get_energy(unit_id)
            energy_max = state.energy.get_energy_max(unit_id)
            if energy_max is not None:
                energy_safe = self.energy_safe_alpha * energy_max
                penalty += max(0.0, energy_safe - energy)
        return penalty

    def _sp_depletion_penalty(self, state):
        return max(0.0, 1.0 - float(state.skill_points.get_current_sp()))

    def _upper_bound_from_state(self, state, remaining_nodes):
        upper_damage = state.get_damage_accumulated()
        for node in remaining_nodes:
            upper_damage += self._max_theoretical_damage(node.unit_id, state)
        return upper_damage

    def _max_theoretical_damage(self, unit_id, state):
        if self.damage_upper_bound_provider is None:
            return 0.0
        return float(self.damage_upper_bound_provider(unit_id, state))

    def _get_action_definitions(self, unit_id, state):
        if self.action_definition_provider is not None:
            return tuple(self.action_definition_provider(unit_id, state))
        return (
            ActionDefinition(ActionType.BASIC_ATTACK, attack_type="attack"),
            ActionDefinition(ActionType.SKILL, attack_type="attack", sp_cost=1),
            ActionDefinition(ActionType.ULTIMATE, attack_type="attack", requires_full_energy=True),
        )

    def _is_action_definition_legal(self, definition, unit_id, state):
        if definition.sp_cost > state.skill_points.get_current_sp():
            return False
        if definition.requires_full_energy and not state.energy.get_is_full(unit_id):
            return False
        if self._is_forbidden_by_buff(definition, unit_id, state):
            return False
        if definition.reduces_toughness and definition.target_id is not None:
            if state.toughness.get_is_locked(definition.target_id):
                return False
            if state.toughness.get_toughness(definition.target_id) > 0.0:
                if not definition.ignore_weakness:
                    if definition.element not in definition.target_weakness_list:
                        return False
        return True

    def _is_forbidden_by_buff(self, definition, unit_id, state):
        forbidden_tags = {
            "forbid_action:{0}".format(definition.action_type.value),
            "forbid_attack_type:{0}".format(definition.attack_type),
        }
        for buff in state.buffs.get_by_target(unit_id):
            if any(tag in forbidden_tags for tag in buff.effect_tags):
                return True
        return False

    def _metadata_for_action(self, unit_id, action_type, state):
        for definition in self._get_action_definitions(unit_id, state):
            if definition.action_type is action_type:
                return {
                    "attack_type": definition.attack_type,
                    "element": definition.element,
                    "target_id": definition.target_id,
                    "target_weakness_list": definition.target_weakness_list,
                    "ignore_weakness": definition.ignore_weakness,
                    "sp_cost": definition.sp_cost,
                    "requires_full_energy": definition.requires_full_energy,
                    "reduces_toughness": definition.reduces_toughness,
                    "tags": definition.tags,
                }
        return {}

    def _default_hit_branches(self, state, enemy_nodes, max_branches):
        character_ids = list(state.get_character_unit_ids())
        if not character_ids:
            return tuple()

        base_weight = 1.0 / len(character_ids)
        branches = []
        for enemy_node in enemy_nodes:
            weighted_targets = []
            for character_id in character_ids:
                weight = self._taunt_weight(character_id, state)
                weighted_targets.append((character_id, weight))
            total_weight = sum(weight for _, weight in weighted_targets)
            if total_weight <= 0.0:
                weighted_targets = [(character_id, base_weight) for character_id in character_ids]
                total_weight = sum(weight for _, weight in weighted_targets)
            ranked = sorted(
                weighted_targets,
                key=lambda item: (-item[1] / total_weight, item[0]),
            )
            for rank, (character_id, weight) in enumerate(ranked[:max_branches], start=1):
                probability = weight / total_weight
                branches.append(
                    HitBranch(
                        "hit:{0}:{1}".format(enemy_node.unit_id, rank),
                        enemy_node.unit_id,
                        enemy_node.action_value,
                        character_id,
                        float(_required_config("hit_params.default_hit_energy")),
                        weight,
                        probability,
                        {"source": "default_taunt_distribution"},
                    )
                )
        return tuple(branches)

    def _taunt_weight(self, unit_id, state):
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

    def _reset_window_damage(self, state):
        reset_event = Event(EventType.WINDOW_INIT, {}, "search_engine")
        self.transition_executor(
            state,
            None,
            self.event_bus,
            control_event=reset_event,
        )


def extract_action_window(state_0, delta_t=None):
    """Module-level helper for extracting friendly action nodes."""
    return _metadata_only_engine().extract_action_window(state_0, delta_t)


def get_legal_actions(unit_id, state):
    """Module-level helper for default legal action extraction."""
    return _metadata_only_engine().get_legal_actions(unit_id, state)


def evaluate_path(path, state_0):
    """Module-level helper for evaluating a path with default engine wiring."""
    raise SearchEngineConfigurationError("evaluate_path requires a configured SearchEngine")


def heuristic_upper_bound(partial_path, state_0, remaining_steps):
    """Module-level helper for upper-bound estimation with default wiring."""
    raise SearchEngineConfigurationError(
        "heuristic_upper_bound requires a configured SearchEngine"
    )


def search_optimal(state_0, delta_t=None, gamma=None):
    """Module-level helper for optimal path search with default wiring."""
    raise SearchEngineConfigurationError("search_optimal requires a configured SearchEngine")


def handle_hit_branches(state_0, delta_t=None, max_branches=None):
    """Module-level helper for hit branch handling with default wiring."""
    raise SearchEngineConfigurationError(
        "handle_hit_branches requires a configured SearchEngine"
    )


class _MetadataOnlySearchEngine:
    legal_action_provider = None
    action_definition_provider = None

    def extract_action_window(self, state, delta_t=None, t_start=None, t_end=None):
        return SearchEngine.extract_action_window(self, state, delta_t, t_start, t_end)

    def get_legal_actions(self, unit_id, state):
        return SearchEngine.get_legal_actions(self, unit_id, state)

    def _get_action_definitions(self, unit_id, state):
        return (
            ActionDefinition(ActionType.BASIC_ATTACK, attack_type="attack"),
            ActionDefinition(ActionType.SKILL, attack_type="attack", sp_cost=1),
            ActionDefinition(ActionType.ULTIMATE, attack_type="attack", requires_full_energy=True),
        )

    def _is_action_definition_legal(self, definition, unit_id, state):
        return SearchEngine._is_action_definition_legal(self, definition, unit_id, state)

    def _is_forbidden_by_buff(self, definition, unit_id, state):
        return SearchEngine._is_forbidden_by_buff(self, definition, unit_id, state)


def _metadata_only_engine():
    return _MetadataOnlySearchEngine()


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


def _default_hit_branches(max_branches):
    if max_branches is not None:
        return int(max_branches)
    return int(_required_config("hit_params.max_hit_branches"))
