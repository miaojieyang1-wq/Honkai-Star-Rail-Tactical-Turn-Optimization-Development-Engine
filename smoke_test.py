# -*- coding: utf-8 -*-
"""Core smoke tests for the tactical battle engine."""

import os

from battle_engine import BattleEngine, BattleEngineConfigurationError, HitModule, plan_optimal
from config_loader import get_env
from event_system import DamageSettlementEvent, DamageType, Event, EventBus, EventType
from external_api import build_mock_api_bundle
from search_engine import (
    ActionDefinition,
    ActionNode,
    ActionType,
    HitBranch,
    HitBranchResult,
    SearchResult,
)
from tactical_report import TemplateLLMReportClient, build_tactical_report
from state import ActionItem, ActionQueue, BattleState, StateError, UnitType


CHARACTER_A = "sample_dps_01"
CHARACTER_B = "sample_support_01"
ENEMY_ID = "sample_boss_01"
DELTA_T_EMPTY = 100.0
STALE_DAMAGE = 99.0
SAFE_ENERGY = 100.0
READY_SP = 1
MAX_BRANCHES = 2
CUSTOM_GAMMA = 0.77
TWO_SP_COST = 2
TOUGHNESS_START = 60.0
TOUGHNESS_DAMAGE = 30.0
EXPLICIT_ENERGY_COST = 100.0
EXPLICIT_ENERGY_COST_SCORE = -20.0


def _assert(condition, message):
    """Raise AssertionError with a readable smoke-test message."""
    if not condition:
        raise AssertionError(message)


def _registered_state():
    """Create a minimal state with a locked character set C."""
    state = BattleState()
    state.update_register_characters((CHARACTER_A, CHARACTER_B))
    state.energy.update_energy_max(CHARACTER_A, SAFE_ENERGY)
    state.energy.update_energy_max(CHARACTER_B, SAFE_ENERGY)
    state.energy.update_set_energy(CHARACTER_A, SAFE_ENERGY)
    state.energy.update_set_energy(CHARACTER_B, SAFE_ENERGY)
    state.skill_points.update_set_current_sp(READY_SP)
    return state


def test_character_registry_lock():
    """Character set C must be immutable after first registration."""
    state = _registered_state()
    try:
        state.update_register_characters((CHARACTER_A,))
    except StateError:
        return
    raise AssertionError("character registry accepted a second registration")


def test_empty_window_resets_damage():
    """An empty search window must return zero score and clear stale D_acc."""
    state = _registered_state()
    state.update_add_damage(STALE_DAMAGE)
    engine = BattleEngine(initial_state=state)
    result = engine.search_engine.search_optimal(state, delta_t=DELTA_T_EMPTY)
    _assert(result.score == 0.0, "empty window score must be zero")
    _assert(state.get_damage_accumulated() == 0.0, "WINDOW_INIT must reset D_acc")


def test_plan_requires_character_registry():
    """Planning must reject states without explicit character set C."""
    try:
        plan_optimal(BattleState())
    except BattleEngineConfigurationError:
        return
    raise AssertionError("planning accepted a state without character_unit_ids")


def test_hit_module_uses_character_registry():
    """Hit candidates must come from C, not from current action queue contents."""
    state = _registered_state()
    enemy_node = ActionNode(ENEMY_ID, 50.0, UnitType.ENEMY)
    branches = HitModule().generate_hit_branches(state, (enemy_node,), MAX_BRANCHES)
    hit_targets = {branch.hit_target_id for branch in branches}
    _assert(hit_targets == {CHARACTER_A, CHARACTER_B}, "hit branches must cover C")


def test_external_api_and_report_flow():
    """Mock external APIs and report generation must form a visible product loop."""
    state = _registered_state()
    state.action_queue.update_insert(CHARACTER_A, 10.0, UnitType.CHARACTER)
    api_bundle = build_mock_api_bundle()
    engine = BattleEngine(initial_state=state, external_api_bundle=api_bundle)
    advice = engine.plan_optimal(state, delta_t=100.0)
    report = build_tactical_report(advice, TemplateLLMReportClient())
    markdown = report.to_markdown()
    _assert("星穹铁道战术建议书" in markdown, "report title is missing")
    _assert("LLM 润色版" in markdown, "LLM report section is missing")


def test_plan_forwards_gamma_to_search():
    """Planning parameters must affect search, not only report assumptions."""
    state = _registered_state()
    engine = BattleEngine(initial_state=state)
    captured = {}

    def fake_handle_hit_branches(state_0, delta_t=None, max_branches=None, gamma=None):
        captured["gamma"] = gamma
        return HitBranchResult(SearchResult(tuple(), 0.0), tuple())

    engine.search_engine.handle_hit_branches = fake_handle_hit_branches
    engine.plan_optimal(state, gamma=CUSTOM_GAMMA)
    _assert(captured["gamma"] == CUSTOM_GAMMA, "gamma must be forwarded to search")


def test_get_env_prefers_os_environment():
    """OS environment values must override .env values."""
    key = "LOG_LEVEL"
    original = os.environ.get(key)
    os.environ[key] = "ERROR"
    try:
        _assert(get_env(key) == "ERROR", "OS environment must override .env")
    finally:
        if original is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original


def test_action_definition_sp_cost_controls_transition():
    """Custom action SP cost must be enforced during state transition."""
    state = _registered_state()
    state.skill_points.update_set_current_sp(TWO_SP_COST)
    state.action_queue.update_insert(CHARACTER_A, 10.0, UnitType.CHARACTER)

    def action_definitions(unit_id, current_state):
        return (
            ActionDefinition(
                ActionType.SKILL,
                attack_type="attack",
                sp_cost=TWO_SP_COST,
            ),
        )

    engine = BattleEngine(initial_state=state, action_definition_provider=action_definitions)
    result = engine.search_engine.search_optimal(state, delta_t=20.0)
    _assert(result.score == -2.0, "skill sp_cost must control consumed SP")


def test_action_definition_toughness_damage_controls_transition():
    """Custom action toughness damage must be applied through the event chain."""
    state = _registered_state()
    state.action_queue.update_insert(CHARACTER_A, 10.0, UnitType.CHARACTER)
    state.toughness.update_toughness_max(ENEMY_ID, TOUGHNESS_START)
    state.toughness.update_set_toughness(ENEMY_ID, TOUGHNESS_START)

    def action_definitions(unit_id, current_state):
        return (
            ActionDefinition(
                ActionType.BASIC_ATTACK,
                attack_type="attack",
                element="fire",
                target_id=ENEMY_ID,
                target_weakness_list=("fire",),
                reduces_toughness=True,
                toughness_damage=TOUGHNESS_DAMAGE,
            ),
        )

    engine = BattleEngine(initial_state=state, action_definition_provider=action_definitions)
    result = engine.search_engine.search_optimal(state, delta_t=20.0)
    _assert(result.path, "custom toughness action must be selected")
    _assert(
        state.toughness.get_toughness(ENEMY_ID) == TOUGHNESS_START,
        "search must not mutate caller toughness outside WINDOW_INIT",
    )
    final_state = engine._simulate_path(state, result.path)
    _assert(
        final_state.toughness.get_toughness(ENEMY_ID)
        == TOUGHNESS_START - TOUGHNESS_DAMAGE,
        "toughness_damage must reduce target toughness",
    )


def test_requires_full_energy_without_cost_does_not_consume_energy():
    """Full-energy legality alone must not imply a hidden energy rule."""
    state = _registered_state()
    state.action_queue.update_insert(CHARACTER_A, 10.0, UnitType.CHARACTER)

    def action_definitions(unit_id, current_state):
        return (
            ActionDefinition(
                ActionType.ULTIMATE,
                attack_type="attack",
                requires_full_energy=True,
            ),
        )

    engine = BattleEngine(initial_state=state, action_definition_provider=action_definitions)
    result = engine.search_engine.search_optimal(state, delta_t=20.0)
    _assert(result.score == 0.0, "full-energy legality alone must not affect score")
    final_state = engine._simulate_path(state, result.path)
    _assert(
        final_state.energy.get_energy(CHARACTER_A) == SAFE_ENERGY,
        "full-energy legality alone must not consume energy",
    )


def test_explicit_energy_cost_controls_transition():
    """Explicit action energy cost must be applied through the event chain."""
    state = _registered_state()
    state.action_queue.update_insert(CHARACTER_A, 10.0, UnitType.CHARACTER)

    def action_definitions(unit_id, current_state):
        return (
            ActionDefinition(
                ActionType.ULTIMATE,
                attack_type="attack",
                energy_cost=EXPLICIT_ENERGY_COST,
                requires_full_energy=True,
            ),
        )

    engine = BattleEngine(initial_state=state, action_definition_provider=action_definitions)
    result = engine.search_engine.search_optimal(state, delta_t=20.0)
    _assert(
        result.score == EXPLICIT_ENERGY_COST_SCORE,
        "explicit energy_cost must affect score",
    )
    final_state = engine._simulate_path(state, result.path)
    _assert(
        final_state.energy.get_energy(CHARACTER_A)
        == SAFE_ENERGY - EXPLICIT_ENERGY_COST,
        "explicit energy_cost must consume energy",
    )


def test_action_definition_rejects_negative_costs():
    """Action metadata must reject values outside the formula domain."""
    invalid_definitions = (
        lambda: ActionDefinition(ActionType.SKILL, sp_cost=-1),
        lambda: ActionDefinition(ActionType.ULTIMATE, energy_cost=-1.0),
        lambda: ActionDefinition(
            ActionType.BASIC_ATTACK,
            reduces_toughness=True,
            toughness_damage=-1.0,
        ),
    )
    for build_definition in invalid_definitions:
        try:
            build_definition()
        except ValueError:
            continue
        raise AssertionError("negative action definition value was accepted")


def test_action_definition_rejects_fractional_sp_cost():
    """SP is a discrete resource, so action SP cost must be integral."""
    try:
        ActionDefinition(ActionType.SKILL, sp_cost=1.5)
    except TypeError:
        return
    raise AssertionError("fractional sp_cost was accepted")


def test_damage_values_reject_negative_domain():
    """Damage settlement and pruning bounds must stay inside damage domain."""
    try:
        DamageSettlementEvent(DamageType.NORMAL, -1.0, "smoke_test")
    except ValueError:
        pass
    else:
        raise AssertionError("negative damage settlement was accepted")

    state = _registered_state()
    engine = BattleEngine(
        initial_state=state,
        damage_upper_bound_provider=lambda unit_id, current_state: -1.0,
    )
    try:
        engine.search_engine.heuristic_upper_bound(tuple(), state, (CHARACTER_A,))
    except ValueError:
        return
    raise AssertionError("negative damage upper bound was accepted")


def test_search_parameters_reject_invalid_domain():
    """Search control parameters must stay inside their formula domains."""
    state = _registered_state()
    engine = BattleEngine(initial_state=state)

    invalid_calls = (
        lambda: engine.search_engine.search_optimal(state, delta_t=-1.0),
        lambda: engine.search_engine.search_optimal(state, gamma=0.0),
        lambda: engine.search_engine.search_optimal(state, gamma=1.01),
        lambda: engine.search_engine.handle_hit_branches(state, max_branches=-1),
        lambda: engine.plan_optimal(state, delta_t=-1.0),
        lambda: engine.plan_optimal(state, gamma=0.0),
        lambda: engine.plan_optimal(state, max_hit_branches=-1),
    )
    for call in invalid_calls:
        try:
            call()
        except ValueError:
            continue
        raise AssertionError("invalid search parameter was accepted")


def test_hit_branch_parameters_reject_invalid_domain():
    """Hit branch weights, probabilities, and energy gains must stay valid."""
    invalid_branches = (
        lambda: HitBranch("bad_energy", ENEMY_ID, 1.0, CHARACTER_A, -1.0),
        lambda: HitBranch("bad_weight", ENEMY_ID, 1.0, CHARACTER_A, 0.0, weight=-1.0),
        lambda: HitBranch(
            "bad_probability",
            ENEMY_ID,
            1.0,
            CHARACTER_A,
            0.0,
            probability=1.01,
        ),
    )
    for build_branch in invalid_branches:
        try:
            build_branch()
        except ValueError:
            continue
        raise AssertionError("invalid hit branch value was accepted")


def test_taunt_tags_reject_negative_domain():
    """Taunt distribution tags must not introduce negative branch weights."""
    state = _registered_state()
    state.buffs.update_apply("bad_taunt", CHARACTER_A, 1.0, 1, ("base_taunt:-1",))
    enemy_node = ActionNode(ENEMY_ID, 50.0, UnitType.ENEMY)
    try:
        HitModule().generate_hit_branches(state, (enemy_node,), MAX_BRANCHES)
    except ValueError:
        return
    raise AssertionError("negative taunt tag was accepted")


def test_state_vectors_reject_negative_domains():
    """Core state vectors must keep maxima and reduction amounts non-negative."""
    state = _registered_state()
    invalid_updates = (
        lambda: state.energy.update_energy_max(CHARACTER_A, -1.0),
        lambda: state.skill_points.update_sp_max(-1),
        lambda: state.toughness.update_toughness_max(ENEMY_ID, -1.0),
        lambda: state.toughness.update_reduce_toughness(
            ENEMY_ID,
            -1.0,
            attack_element="fire",
            target_weakness_list=("fire",),
        ),
    )
    for update in invalid_updates:
        try:
            update()
        except StateError:
            continue
        raise AssertionError("negative state-domain value was accepted")


def test_sp_state_rejects_fractional_domain():
    """SP state values and event amounts must stay discrete integers."""
    state = _registered_state()
    invalid_updates = (
        lambda: state.skill_points.update_sp_max(5.5),
        lambda: state.skill_points.update_set_current_sp(1.5),
    )
    for update in invalid_updates:
        try:
            update()
        except StateError:
            continue
        raise AssertionError("fractional SP state value was accepted")

    engine = BattleEngine(initial_state=state)
    local_bus = EventBus()
    engine._register_core_modules(state, local_bus)
    try:
        local_bus.emit(
            Event(
                EventType.SKILL_SP_CONSUME,
                {"unit_id": CHARACTER_A, "amount": 1.5},
                "smoke_test",
            )
        )
    except ValueError:
        return
    raise AssertionError("fractional SP event amount was accepted")


def test_action_queue_rejects_invalid_action_values():
    """Action-value coordinates must be finite and non-negative."""
    state = _registered_state()
    invalid_updates = (
        lambda: state.action_queue.update_insert("bad_negative", -1.0, UnitType.CHARACTER),
        lambda: state.action_queue.update_insert("bad_infinite", float("inf"), UnitType.CHARACTER),
        lambda: state.action_queue.update_insert("bad_nan", float("nan"), UnitType.CHARACTER),
        lambda: state.action_queue.update_prune_before(-1.0),
    )
    for update in invalid_updates:
        try:
            update()
        except StateError:
            continue
        raise AssertionError("invalid action value was accepted")


def test_action_queue_initialization_rejects_invalid_action_values():
    """ActionQueue constructor must not bypass action-value validation."""
    try:
        ActionQueue([ActionItem("bad_init", -1.0, UnitType.CHARACTER)])
    except StateError:
        return
    raise AssertionError("invalid initial action value was accepted")


def test_action_queue_rejects_invalid_unit_type():
    """ActionQueue entries must use the UnitType domain."""
    state = _registered_state()
    invalid_updates = (
        lambda: state.action_queue.update_insert("bad_type", 1.0, "character"),
        lambda: ActionQueue([ActionItem("bad_init_type", 1.0, "character")]),
    )
    for update in invalid_updates:
        try:
            update()
        except StateError:
            continue
        raise AssertionError("invalid unit_type was accepted")


def test_search_engine_rejects_invalid_config_domain():
    """Search scoring config must stay inside the objective-function domain."""
    invalid_config_values = (
        ("search_params.energy_penalty_lambda", -1.0),
        ("search_params.sp_penalty_lambda", -1.0),
        ("search_params.energy_safe_alpha", -0.1),
        ("search_params.energy_safe_alpha", 1.1),
    )
    for config_key, config_value in invalid_config_values:
        original_get_config = __import__("search_engine").get_config

        def fake_get_config(key, original_get_config=original_get_config):
            if key == config_key:
                return config_value
            return original_get_config(key)

        import search_engine

        search_engine.get_config = fake_get_config
        try:
            try:
                BattleEngine(initial_state=_registered_state())
            except ValueError:
                continue
            raise AssertionError("invalid search config value was accepted")
        finally:
            search_engine.get_config = original_get_config


def main():
    """Run the smoke-test suite."""
    test_character_registry_lock()
    test_empty_window_resets_damage()
    test_plan_requires_character_registry()
    test_hit_module_uses_character_registry()
    test_external_api_and_report_flow()
    test_plan_forwards_gamma_to_search()
    test_get_env_prefers_os_environment()
    test_action_definition_sp_cost_controls_transition()
    test_action_definition_toughness_damage_controls_transition()
    test_requires_full_energy_without_cost_does_not_consume_energy()
    test_explicit_energy_cost_controls_transition()
    test_action_definition_rejects_negative_costs()
    test_action_definition_rejects_fractional_sp_cost()
    test_damage_values_reject_negative_domain()
    test_search_parameters_reject_invalid_domain()
    test_hit_branch_parameters_reject_invalid_domain()
    test_taunt_tags_reject_negative_domain()
    test_state_vectors_reject_negative_domains()
    test_sp_state_rejects_fractional_domain()
    test_action_queue_rejects_invalid_action_values()
    test_action_queue_initialization_rejects_invalid_action_values()
    test_action_queue_rejects_invalid_unit_type()
    test_search_engine_rejects_invalid_config_domain()
    print("smoke tests passed")


if __name__ == "__main__":
    main()
