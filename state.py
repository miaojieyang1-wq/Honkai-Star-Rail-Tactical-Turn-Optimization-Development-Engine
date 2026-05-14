# -*- coding: utf-8 -*-
"""Core battle state containers.

This module defines S = (Q, E, SP, T, B, DOT, D_acc). DOT is an independent
state component owned by DOTModule; D_acc is owned by DamageAccumulator.
This module only owns state storage and state update methods; battle rules
and cross-module coordination belong to dedicated handlers and event channels.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, NamedTuple, Optional, Tuple

from config_loader import get_config


class StateError(RuntimeError):
    """Raised when a state invariant is violated."""


class UnitType(Enum):
    """Type of unit scheduled on the action queue."""

    CHARACTER = "character"
    ENEMY = "enemy"
    MEMOSPRITE = "memosprite"
    AHA_MOMENT = "aha_moment"


class ActionItem(NamedTuple):
    """Single action queue item."""

    unit_id: str
    action_value: float
    unit_type: UnitType


class BuffEntry(NamedTuple):
    """Buff or debuff state entry."""

    buff_id: str
    target_id: str
    remaining_duration: float
    remaining_layers: int
    effect_tags: Tuple[str, ...]


class DOTEntry(NamedTuple):
    """Damage-over-time state entry."""

    dot_id: str
    source_id: str
    target_id: str
    dot_type: str
    remaining_layers: int
    max_layers: int
    remaining_turns: int


@dataclass
class ActionQueue:
    """Ordered action queue Q, sorted by ascending action value."""

    _items: List[ActionItem] = field(default_factory=list)

    def __post_init__(self):
        self._sort_items()

    def get_items(self):
        """Return an immutable snapshot of the queue."""
        return tuple(self._items)

    def get_unit(self, unit_id):
        """Return the first queue item for a unit, or None when absent."""
        for item in self._items:
            if item.unit_id == unit_id:
                return item
        return None

    def update_pop_front(self):
        """Pop and return the first queue item."""
        if not self._items:
            return None
        return self._items.pop(0)

    def update_insert(self, unit_id, action_value, unit_type):
        """Insert a new queue item, preserving action value order."""
        self._items.append(ActionItem(unit_id, float(action_value), unit_type))
        self._sort_items()

    def update_action_value(self, unit_id, action_value):
        """Update a unit action value and re-sort the queue."""
        for index, item in enumerate(self._items):
            if item.unit_id == unit_id:
                self._items[index] = ActionItem(
                    item.unit_id,
                    float(action_value),
                    item.unit_type,
                )
                self._sort_items()
                return True
        return False

    def update_remove(self, unit_id):
        """Remove all queue items matching a unit id."""
        original_count = len(self._items)
        self._items = [item for item in self._items if item.unit_id != unit_id]
        return len(self._items) != original_count

    def update_prune_before(self, action_value, inclusive=True):
        """Remove queue items before a time point."""
        original_count = len(self._items)
        if inclusive:
            self._items = [
                item for item in self._items if item.action_value > float(action_value)
            ]
        else:
            self._items = [
                item for item in self._items if item.action_value >= float(action_value)
            ]
        return len(self._items) != original_count

    def _sort_items(self):
        self._items.sort(key=lambda item: item.action_value)


@dataclass
class EnergyVector:
    """Energy vector E with per-unit maximum energy."""

    _energy: Dict[str, float] = field(default_factory=dict)
    _energy_max: Dict[str, float] = field(default_factory=dict)

    def get_energy(self, unit_id):
        """Return the current energy for a unit."""
        return self._energy.get(unit_id, 0.0)

    def get_energy_max(self, unit_id):
        """Return the maximum energy for a unit, or None when unset."""
        return self._energy_max.get(unit_id)

    def get_all_energy(self):
        """Return a snapshot of all current energy values."""
        return dict(self._energy)

    def update_energy_max(self, unit_id, energy_max):
        """Set a unit maximum energy and clamp current energy to it."""
        energy_max = float(energy_max)
        self._energy_max[unit_id] = energy_max
        self._energy[unit_id] = min(self.get_energy(unit_id), energy_max)

    def update_set_energy(self, unit_id, energy):
        """Set current energy, respecting maximum energy when configured."""
        energy = max(0.0, float(energy))
        energy_max = self.get_energy_max(unit_id)
        if energy_max is not None:
            energy = min(energy, energy_max)
        self._energy[unit_id] = energy

    def update_add_energy(self, unit_id, amount):
        """Increase energy, respecting maximum energy when configured."""
        self.update_set_energy(unit_id, self.get_energy(unit_id) + float(amount))

    def get_is_full(self, unit_id):
        """Return whether a unit has enough energy to use ultimate."""
        energy_max = self.get_energy_max(unit_id)
        if energy_max is None:
            return False
        return self.get_energy(unit_id) >= energy_max


@dataclass
class SPTracker:
    """Skill point tracker SP."""

    _current_sp: int = 0
    _sp_max: Optional[int] = None

    def __post_init__(self):
        if self._sp_max is None:
            self._sp_max = int(get_config("game_constants.default_sp_max"))
        self._current_sp = self._clamp_sp(self._current_sp)

    def get_current_sp(self):
        """Return current skill points."""
        return self._current_sp

    def get_sp_max(self):
        """Return current maximum skill points."""
        return self._sp_max

    def update_consume_one(self):
        """Consume one skill point if available."""
        if self._current_sp <= 0:
            return False
        self._current_sp -= 1
        return True

    def update_recover_one(self):
        """Recover one skill point up to the current maximum."""
        if self._current_sp >= self._sp_max:
            return False
        self._current_sp += 1
        return True

    def update_sp_max(self, sp_max):
        """Set maximum skill points and clamp current skill points."""
        self._sp_max = int(sp_max)
        self._current_sp = self._clamp_sp(self._current_sp)

    def update_set_current_sp(self, current_sp):
        """Set current skill points within [0, SP_max]."""
        self._current_sp = self._clamp_sp(int(current_sp))

    def _clamp_sp(self, value):
        return max(0, min(int(value), self._sp_max))


@dataclass
class ToughnessVector:
    """Toughness vector T for enemies."""

    _toughness: Dict[str, float] = field(default_factory=dict)
    _toughness_max: Dict[str, float] = field(default_factory=dict)
    _toughness_locked: Dict[str, bool] = field(default_factory=dict)

    def get_toughness(self, enemy_id):
        """Return current toughness for an enemy."""
        return self._toughness.get(enemy_id, 0.0)

    def get_toughness_max(self, enemy_id):
        """Return maximum toughness for an enemy, or None when unset."""
        return self._toughness_max.get(enemy_id)

    def get_is_locked(self, enemy_id):
        """Return whether an enemy toughness bar is locked."""
        return self._toughness_locked.get(enemy_id, False)

    def get_is_broken(self, enemy_id):
        """Return whether an enemy toughness is depleted."""
        return self.get_toughness(enemy_id) <= 0.0

    def get_snapshot(self):
        """Return a snapshot of toughness current values, maxima, and lock flags."""
        return {
            "current": dict(self._toughness),
            "max": dict(self._toughness_max),
            "locked": dict(self._toughness_locked),
        }

    def update_toughness_max(self, enemy_id, toughness_max):
        """Set maximum toughness and clamp current toughness to it."""
        toughness_max = float(toughness_max)
        self._toughness_max[enemy_id] = toughness_max
        current = self._toughness.get(enemy_id, toughness_max)
        self._toughness[enemy_id] = min(max(0.0, current), toughness_max)

    def update_set_toughness(self, enemy_id, toughness):
        """Set current toughness within [0, T_max] when maximum is configured."""
        toughness = max(0.0, float(toughness))
        toughness_max = self.get_toughness_max(enemy_id)
        if toughness_max is not None:
            toughness = min(toughness, toughness_max)
        self._toughness[enemy_id] = toughness

    def update_reduce_toughness(
        self,
        enemy_id,
        amount,
        attack_element=None,
        target_weakness_list=None,
        ignore_weakness_flag=False,
    ):
        """Reduce toughness only when not locked and weakness rules allow it."""
        if self.get_is_locked(enemy_id):
            return self.get_toughness(enemy_id)
        if not ignore_weakness_flag:
            weaknesses = tuple(target_weakness_list or ())
            if attack_element is None or attack_element not in weaknesses:
                return self.get_toughness(enemy_id)
        new_value = max(0.0, self.get_toughness(enemy_id) - float(amount))
        self._toughness[enemy_id] = new_value
        return new_value

    def update_lock(self, enemy_id):
        """Lock an enemy toughness bar."""
        self._toughness_locked[enemy_id] = True

    def update_unlock(self, enemy_id):
        """Unlock an enemy toughness bar."""
        self._toughness_locked[enemy_id] = False


@dataclass
class BuffList:
    """Buff/debuff list B."""

    _buffs: List[BuffEntry] = field(default_factory=list)

    def get_all(self):
        """Return an immutable snapshot of all buffs."""
        return tuple(self._buffs)

    def get_by_target(self, target_id):
        """Return buffs affecting a target."""
        return tuple(buff for buff in self._buffs if buff.target_id == target_id)

    def get_by_tag(self, effect_tag):
        """Return buffs containing an effect tag."""
        return tuple(buff for buff in self._buffs if effect_tag in buff.effect_tags)

    def get_by_target_and_tag(self, target_id, effect_tag):
        """Return target buffs containing an effect tag."""
        return tuple(
            buff
            for buff in self._buffs
            if buff.target_id == target_id and effect_tag in buff.effect_tags
        )

    def update_apply(self, buff_id, target_id, remaining_duration, remaining_layers, effect_tags):
        """Apply a new buff entry."""
        self._buffs.append(
            BuffEntry(
                buff_id,
                target_id,
                float(remaining_duration),
                int(remaining_layers),
                tuple(effect_tags),
            )
        )

    def update_tick(self, duration_delta=1.0):
        """Decrease buff duration during traversal updates and remove expired buffs."""
        duration_delta = float(duration_delta)
        self._buffs = [
            BuffEntry(
                buff.buff_id,
                buff.target_id,
                buff.remaining_duration - duration_delta,
                buff.remaining_layers,
                buff.effect_tags,
            )
            for buff in self._buffs
        ]
        self.update_remove_expired()

    def update_remove_expired(self):
        """Remove buffs with no remaining duration or layers."""
        self._buffs = [
            buff
            for buff in self._buffs
            if buff.remaining_duration > 0 and buff.remaining_layers > 0
        ]

    def update_remove(self, buff_id, target_id=None):
        """Remove buffs by id, optionally restricted to a target."""
        original_count = len(self._buffs)
        self._buffs = [
            buff
            for buff in self._buffs
            if not (
                buff.buff_id == buff_id
                and (target_id is None or buff.target_id == target_id)
            )
        ]
        return len(self._buffs) != original_count


@dataclass
class DOTList:
    """Damage-over-time list DOT."""

    _dots: List[DOTEntry] = field(default_factory=list)

    def get_all(self):
        """Return an immutable snapshot of all DoT entries."""
        return tuple(self._dots)

    def get_by_target(self, target_id):
        """Return DoT entries affecting a target."""
        return tuple(dot for dot in self._dots if dot.target_id == target_id)

    def update_apply(
        self,
        dot_id,
        source_id,
        target_id,
        dot_type,
        remaining_layers,
        max_layers,
        remaining_turns,
    ):
        """Apply a DoT, stacking layers when the same DoT already exists."""
        new_entry = DOTEntry(
            dot_id,
            source_id,
            target_id,
            dot_type,
            int(remaining_layers),
            int(max_layers),
            int(remaining_turns),
        )

        for index, dot in enumerate(self._dots):
            if self._is_same_dot(dot, new_entry):
                layers = min(dot.max_layers, dot.remaining_layers + new_entry.remaining_layers)
                turns = max(dot.remaining_turns, new_entry.remaining_turns)
                self._dots[index] = DOTEntry(
                    dot.dot_id,
                    dot.source_id,
                    dot.target_id,
                    dot.dot_type,
                    layers,
                    dot.max_layers,
                    turns,
                )
                return

        layers = min(new_entry.remaining_layers, new_entry.max_layers)
        self._dots.append(new_entry._replace(remaining_layers=layers))

    def update_end_turn_settle(self, target_id=None):
        """Settle DoTs at turn end, consuming one layer from affected entries."""
        updated = []
        for dot in self._dots:
            if target_id is not None and dot.target_id != target_id:
                updated.append(dot)
                continue

            settled = dot._replace(
                remaining_layers=dot.remaining_layers - 1,
                remaining_turns=dot.remaining_turns - 1,
            )
            if settled.remaining_layers > 0 and settled.remaining_turns > 0:
                updated.append(settled)

        self._dots = updated

    def update_immediate_settle(self, target_id=None):
        """Mark an immediate DoT settlement without consuming layers."""
        return self.get_by_target(target_id) if target_id is not None else self.get_all()

    def _is_same_dot(self, left, right):
        return (
            left.dot_id == right.dot_id
            and left.source_id == right.source_id
            and left.target_id == right.target_id
            and left.dot_type == right.dot_type
        )


@dataclass
class BattleState:
    """Global battle state tuple S = (Q, E, SP, T, B, DOT, D_acc)."""

    action_queue: ActionQueue = field(default_factory=ActionQueue)
    character_unit_ids: Tuple[str, ...] = tuple()
    _characters_locked: bool = False
    energy: EnergyVector = field(default_factory=EnergyVector)
    skill_points: SPTracker = field(default_factory=SPTracker)
    toughness: ToughnessVector = field(default_factory=ToughnessVector)
    buffs: BuffList = field(default_factory=BuffList)
    dots: DOTList = field(default_factory=DOTList)
    _damage_accumulated: float = 0.0

    def get_damage_accumulated(self):
        """Return accumulated damage inside the current window."""
        return self._damage_accumulated

    def get_character_unit_ids(self):
        """Return the registered character set C."""
        return tuple(self.character_unit_ids)

    def update_register_characters(self, character_unit_ids):
        """Register the immutable character set C for formula-level scoring."""
        if self._characters_locked:
            raise StateError("character_unit_ids is locked for this BattleState")
        self.character_unit_ids = tuple(character_unit_ids)
        self._characters_locked = True

    def get_state_tuple(self):
        """Return S = (Q, E, SP, T, B, DOT, D_acc) as read-only snapshots."""
        return (
            self.action_queue.get_items(),
            self.energy.get_all_energy(),
            self.skill_points.get_current_sp(),
            self.toughness.get_snapshot(),
            self.buffs.get_all(),
            self.dots.get_all(),
            self.get_damage_accumulated(),
        )

    def update_add_damage(self, damage):
        """Accumulate damage inside the current search window."""
        self._damage_accumulated += float(damage)

    def update_reset_damage(self):
        """Reset accumulated damage for a new search window."""
        self._damage_accumulated = 0.0
