# -*- coding: utf-8 -*-
"""Interfaces for optional archetype modules."""

from abc import ABC, abstractmethod
from copy import deepcopy
from types import MappingProxyType

from event_system import Event, EventBus


class ArchetypeInterface(ABC):
    """Base interface for plug-in archetype modules such as Remembrance or Elation."""

    archetype_id: str

    def __init__(self, event_bus=None):
        self._event_bus = None
        self._internal_state = {}
        if event_bus is not None:
            self.attach_event_bus(event_bus)

    def attach_event_bus(self, event_bus):
        """Attach the EventBus used for all global-state interactions."""
        if not isinstance(event_bus, EventBus):
            raise TypeError("event_bus must be an EventBus")
        self._event_bus = event_bus

    @abstractmethod
    def initialize(self, state_0):
        """Initialize archetype-local state and emit any required setup events."""

    @abstractmethod
    def handle_event(self, event):
        """Receive an event, update only archetype-local state, and emit events if needed."""

    @abstractmethod
    def get_damage_params(self, unit_id, damage_type):
        """Return archetype-specific damage parameters for damage API calls."""

    @property
    def internal_state(self):
        """Return a read-only snapshot of archetype-local state."""
        return MappingProxyType(deepcopy(self._internal_state))

    def emit_event(self, event):
        """Emit an event instead of directly mutating core battle state."""
        if self._event_bus is None:
            raise RuntimeError("archetype is not attached to an EventBus")
        if not isinstance(event, Event):
            raise TypeError("event must be an Event")
        self._event_bus.emit(event)

    def request_state(self, query_name, payload=None):
        """Request read-only global state information through EventBus callbacks."""
        if self._event_bus is None:
            raise RuntimeError("archetype is not attached to an EventBus")
        return self._event_bus.request(query_name, payload)

    def _update_internal_state(self, key, value):
        """Update archetype-owned internal state."""
        self._internal_state[key] = value

    def _get_internal_value(self, key, default=None):
        """Read archetype-owned internal state."""
        return self._internal_state.get(key, default)


class RemembranceArchetypeInterface(ArchetypeInterface):
    """Interface marker for Remembrance archetype modules."""

    archetype_id = "remembrance"


class ElationArchetypeInterface(ArchetypeInterface):
    """Interface marker for Elation archetype modules."""

    archetype_id = "elation"
