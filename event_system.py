# -*- coding: utf-8 -*-
"""Synchronous deterministic event system and battle module interfaces."""

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Callable, Deque, Dict, List, Mapping, Optional


class EventType(Enum):
    """All supported event types for battle module communication."""

    ACTION_ADVANCE = "action_advance"
    ACTION_DELAY = "action_delay"
    SPEED_UP = "speed_up"
    SLOW_DOWN = "slow_down"
    SUMMON_ACTOR = "summon_actor"
    REMOVE_ACTOR = "remove_actor"
    ACTION_POSTPONE = "action_postpone"

    ACTION_ENERGY_GAIN = "action_energy_gain"
    ASSIST_ENERGY_GAIN = "assist_energy_gain"
    HIT_ENERGY_GAIN = "hit_energy_gain"
    MEMOSPRITE_ENERGY_GAIN = "memosprite_energy_gain"

    SKILL_SP_CONSUME = "skill_sp_consume"
    BASIC_ATTACK_SP_RECOVER = "basic_attack_sp_recover"
    SP_MAX_CHANGE = "sp_max_change"
    BURST_POINT_SUBSTITUTE = "burst_point_substitute"

    TOUGHNESS_REDUCE_REQUEST = "toughness_reduce_request"
    BREAK_TRIGGERED = "break_triggered"
    TOUGHNESS_RECOVER = "toughness_recover"

    BUFF_APPLY = "buff_apply"
    BUFF_EXPIRE = "buff_expire"

    DOT_APPLY = "dot_apply"
    DOT_END_TURN_SETTLE_TRIGGER = "dot_end_turn_settle_trigger"
    DOT_IMMEDIATE_SETTLE_TRIGGER = "dot_immediate_settle_trigger"

    DAMAGE_SETTLEMENT = "damage_settlement"
    DAMAGE_RESET = "damage_reset"
    WINDOW_INIT = "window_init"

    ENEMY_ACTION_HIT_ANALYSIS_TRIGGER = "enemy_action_hit_analysis_trigger"

    FUA_TRIGGER_CHECK = "fua_trigger_check"

    ELATION_JOY_ACCUMULATE = "elation_joy_accumulate"
    AHA_MOMENT_TRIGGER = "aha_moment_trigger"
    MEMOSPRITE_SUMMON = "memosprite_summon"
    MEMOSPRITE_EXIT = "memosprite_exit"


class ModuleType(Enum):
    """Core battle module identifiers."""

    AXIS = "axis"
    ENERGY = "energy"
    SP = "sp"
    TOUGHNESS = "toughness"
    BUFF = "buff"
    DOT = "dot"
    DAMAGE = "damage"
    HIT = "hit"
    FUA = "fua"


class DamageType(Enum):
    """Damage settlement categories."""

    NORMAL = "Normal"
    DOT = "DoT"
    DOT_INSTANT = "DoTInstant"
    FUA = "FUA"
    MEM = "Mem"
    ELATION = "Elation"
    BREAK = "Break"
    SUPER_BREAK = "SuperBreak"


@dataclass(frozen=True)
class Event:
    """Immutable event packet delivered through EventBus."""

    event_type: EventType
    payload: Mapping[str, object] = field(default_factory=dict)
    source: Optional[str] = None

    def __post_init__(self):
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True)
class DamageSettlementEvent(Event):
    """Damage event handled exclusively by DamageAccumulator."""

    def __init__(self, damage_type, damage_value, source=None, metadata=None):
        if not isinstance(damage_type, DamageType):
            damage_type = DamageType(damage_type)
        payload = {
            "damage_type": damage_type,
            "damage_value": float(damage_value),
            "metadata": MappingProxyType(dict(metadata or {})),
        }
        object.__setattr__(self, "event_type", EventType.DAMAGE_SETTLEMENT)
        object.__setattr__(self, "payload", MappingProxyType(payload))
        object.__setattr__(self, "source", source)


class EventBus:
    """Synchronous deterministic event bus for module-to-module communication."""

    def __init__(self):
        self._subscribers: Dict[EventType, List[BattleModuleInterface]] = {}
        self._query_handlers: Dict[str, Callable[[Mapping[str, object]], object]] = {}
        self._event_queue: Deque[Event] = deque()
        self._is_dispatching = False

    def register(self, module, event_types):
        """Subscribe a module to one or more event types."""
        self._validate_module(module)
        for event_type in event_types:
            if not isinstance(event_type, EventType):
                raise TypeError("event_type must be an EventType")
            subscribers = self._subscribers.setdefault(event_type, [])
            if module not in subscribers:
                subscribers.append(module)
        module.attach_event_bus(self)

    def unregister(self, module):
        """Remove a module from all event subscriptions."""
        for subscribers in self._subscribers.values():
            if module in subscribers:
                subscribers.remove(module)

    def emit(self, event):
        """Emit an event and synchronously drain the event queue."""
        if not isinstance(event, Event):
            raise TypeError("event must be an Event")

        self._event_queue.append(event)
        if self._is_dispatching:
            return

        self._is_dispatching = True
        try:
            while self._event_queue:
                current_event = self._event_queue.popleft()
                subscribers = tuple(self._subscribers.get(current_event.event_type, ()))
                for module in subscribers:
                    module.handle_event(current_event)
        finally:
            self._is_dispatching = False

    def register_query_handler(self, query_name, handler):
        """Register a deterministic read callback for a named state query."""
        if not callable(handler):
            raise TypeError("handler must be callable")
        self._query_handlers[query_name] = handler

    def unregister_query_handler(self, query_name):
        """Remove a named state query callback."""
        self._query_handlers.pop(query_name, None)

    def request(self, query_name, payload=None):
        """Request read-only state information through a registered callback."""
        if query_name not in self._query_handlers:
            raise KeyError("query handler is not registered: {0}".format(query_name))

        request_payload = MappingProxyType(dict(payload or {}))
        return self._query_handlers[query_name](request_payload)

    def get_subscribers(self, event_type):
        """Return a snapshot of subscribers for inspection and tests."""
        return tuple(self._subscribers.get(event_type, ()))

    def _validate_module(self, module):
        if not isinstance(module, BattleModuleInterface):
            raise TypeError("module must implement BattleModuleInterface")


class BattleModuleInterface(ABC):
    """Base interface for all battle modules."""

    module_type: ModuleType

    def __init__(self):
        self._event_bus = None

    def attach_event_bus(self, event_bus):
        """Attach the event bus used for event emission and read requests."""
        if not isinstance(event_bus, EventBus):
            raise TypeError("event_bus must be an EventBus")
        self._event_bus = event_bus

    @abstractmethod
    def handle_event(self, event):
        """Receive an event and update only this module's exclusively owned state."""

    def emit_event(self, event):
        """Send a new event through EventBus without directly calling other modules."""
        if self._event_bus is None:
            raise RuntimeError("module is not registered with an EventBus")
        self._event_bus.emit(event)

    def request_state(self, query_name, payload=None):
        """Request read-only state information through EventBus."""
        if self._event_bus is None:
            raise RuntimeError("module is not registered with an EventBus")
        return self._event_bus.request(query_name, payload)


class AxisModuleInterface(BattleModuleInterface):
    """Interface for the action-axis module."""

    module_type = ModuleType.AXIS


class EnergyModuleInterface(BattleModuleInterface):
    """Interface for the energy module."""

    module_type = ModuleType.ENERGY


class SPModuleInterface(BattleModuleInterface):
    """Interface for the skill-point module."""

    module_type = ModuleType.SP


class ToughnessModuleInterface(BattleModuleInterface):
    """Interface for the toughness module."""

    module_type = ModuleType.TOUGHNESS


class BuffModuleInterface(BattleModuleInterface):
    """Interface for the buff/debuff module."""

    module_type = ModuleType.BUFF


class DOTModuleInterface(BattleModuleInterface):
    """Interface for the damage-over-time module."""

    module_type = ModuleType.DOT


class DamageModuleInterface(BattleModuleInterface):
    """Interface for the damage accumulator module."""

    module_type = ModuleType.DAMAGE


class HitModuleInterface(BattleModuleInterface):
    """Interface for the hit-analysis module."""

    module_type = ModuleType.HIT


class FUAModuleInterface(BattleModuleInterface):
    """Interface for the follow-up-attack module."""

    module_type = ModuleType.FUA


AXIS_EVENT_TYPES = (
    EventType.ACTION_ADVANCE,
    EventType.ACTION_DELAY,
    EventType.SPEED_UP,
    EventType.SLOW_DOWN,
    EventType.SUMMON_ACTOR,
    EventType.REMOVE_ACTOR,
    EventType.ACTION_POSTPONE,
)

ENERGY_EVENT_TYPES = (
    EventType.ACTION_ENERGY_GAIN,
    EventType.ASSIST_ENERGY_GAIN,
    EventType.HIT_ENERGY_GAIN,
    EventType.MEMOSPRITE_ENERGY_GAIN,
)

SP_EVENT_TYPES = (
    EventType.SKILL_SP_CONSUME,
    EventType.BASIC_ATTACK_SP_RECOVER,
    EventType.SP_MAX_CHANGE,
    EventType.BURST_POINT_SUBSTITUTE,
)

TOUGHNESS_EVENT_TYPES = (
    EventType.TOUGHNESS_REDUCE_REQUEST,
    EventType.BREAK_TRIGGERED,
    EventType.TOUGHNESS_RECOVER,
)

BUFF_EVENT_TYPES = (
    EventType.BUFF_APPLY,
    EventType.BUFF_EXPIRE,
)

DOT_EVENT_TYPES = (
    EventType.DOT_APPLY,
    EventType.DOT_END_TURN_SETTLE_TRIGGER,
    EventType.DOT_IMMEDIATE_SETTLE_TRIGGER,
)

DAMAGE_EVENT_TYPES = (
    EventType.DAMAGE_SETTLEMENT,
    EventType.DAMAGE_RESET,
    EventType.WINDOW_INIT,
)

HIT_EVENT_TYPES = (
    EventType.ENEMY_ACTION_HIT_ANALYSIS_TRIGGER,
)

FUA_EVENT_TYPES = (
    EventType.FUA_TRIGGER_CHECK,
)

PATH_EVENT_TYPES = (
    EventType.ELATION_JOY_ACCUMULATE,
    EventType.AHA_MOMENT_TRIGGER,
    EventType.MEMOSPRITE_SUMMON,
    EventType.MEMOSPRITE_EXIT,
)
