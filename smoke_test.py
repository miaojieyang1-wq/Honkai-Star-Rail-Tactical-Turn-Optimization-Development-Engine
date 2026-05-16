# -*- coding: utf-8 -*-
"""Core smoke tests for the tactical battle engine."""

from battle_engine import BattleEngine, BattleEngineConfigurationError, HitModule, plan_optimal
from external_api import build_mock_api_bundle
from search_engine import ActionNode
from tactical_report import TemplateLLMReportClient, build_tactical_report
from state import BattleState, StateError, UnitType


CHARACTER_A = "sample_dps_01"
CHARACTER_B = "sample_support_01"
ENEMY_ID = "sample_boss_01"
DELTA_T_EMPTY = 100.0
STALE_DAMAGE = 99.0
SAFE_ENERGY = 100.0
READY_SP = 1
MAX_BRANCHES = 2


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


def main():
    """Run the smoke-test suite."""
    test_character_registry_lock()
    test_empty_window_resets_damage()
    test_plan_requires_character_registry()
    test_hit_module_uses_character_registry()
    test_external_api_and_report_flow()
    print("smoke tests passed")


if __name__ == "__main__":
    main()
