# -*- coding: utf-8 -*-
"""External API adapters for speed-axis and damage calculation services."""

from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence, Tuple

from search_engine import SearchAction
from state import ActionItem, BattleState


DEFAULT_DAMAGE_VALUE = 0.0


class SpeedAxisApi(Protocol):
    """Interface for an external speed-axis simulator."""

    def simulate_axis(self, state, window_av):
        """Return action items predicted inside the requested AV window."""


class DamageCalcApi(Protocol):
    """Interface for an external instant damage calculator."""

    def calculate_damage(self, state, action):
        """Return expected damage for one action under the given state."""

    def max_damage_upper_bound(self, unit_id, state):
        """Return an optimistic single-action damage bound for pruning."""


@dataclass(frozen=True)
class DamageRequest:
    """Serializable damage calculation request."""

    unit_id: str
    action_type: str
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class DamageResponse:
    """Serializable damage calculation response."""

    expected_damage: float
    source: str
    assumptions: Tuple[str, ...] = tuple()


@dataclass(frozen=True)
class AxisResponse:
    """Serializable speed-axis simulation response."""

    action_items: Tuple[ActionItem, ...]
    source: str
    assumptions: Tuple[str, ...] = tuple()


@dataclass(frozen=True)
class ExternalApiBundle:
    """Container for swappable external tactical APIs."""

    speed_axis_api: SpeedAxisApi
    damage_calc_api: DamageCalcApi

    def damage_executor(self, state, action):
        """Expose damage API as BattleEngine.damage_executor."""
        return self.damage_calc_api.calculate_damage(state, action).expected_damage

    def damage_upper_bound_provider(self, unit_id, state):
        """Expose damage API as SearchEngine.damage_upper_bound_provider."""
        return self.damage_calc_api.max_damage_upper_bound(unit_id, state)


class MockSpeedAxisApi:
    """Mock speed-axis API backed by the current local action queue."""

    def simulate_axis(self, state, window_av):
        """Return queue items whose action value is inside the local window."""
        _validate_state(state)
        window_av = float(window_av)
        items = state.action_queue.get_items()
        if not items:
            return AxisResponse(tuple(), "mock_speed_axis", ("当前行动队列为空。",))

        start = items[0].action_value
        end = start + window_av
        action_items = tuple(
            item for item in items if start <= item.action_value <= end
        )
        return AxisResponse(
            action_items,
            "mock_speed_axis",
            ("使用本地 ActionQueue 验证速度轴接口形状。",),
        )


class MockDamageCalcApi:
    """Mock damage API using action metadata instead of real game coefficients."""

    def calculate_damage(self, state, action):
        """Return deterministic example damage for one action."""
        _validate_state(state)
        _validate_action(action)
        damage = float(action.metadata.get("mock_damage", DEFAULT_DAMAGE_VALUE))
        return DamageResponse(
            damage,
            "mock_damage_calc",
            ("示例伤害来自 action.metadata.mock_damage，非真实游戏公式。",),
        )

    def max_damage_upper_bound(self, unit_id, state):
        """Return the configured mock upper bound for pruning demos."""
        _validate_state(state)
        return float(state.energy.get_energy(unit_id))


def build_mock_api_bundle():
    """Return a complete mock external API bundle for local verification."""
    return ExternalApiBundle(MockSpeedAxisApi(), MockDamageCalcApi())


def _validate_state(state):
    if not isinstance(state, BattleState):
        raise TypeError("state must be a BattleState")


def _validate_action(action):
    if not isinstance(action, SearchAction):
        raise TypeError("action must be a SearchAction")
