# -*- coding: utf-8 -*-
"""Data loading helpers for sample character and enemy data."""

import json
import logging
from pathlib import Path

from config_loader import get_config


LOGGER = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent


def load_characters():
    """Load all character JSON files and return a dict keyed by character id."""
    data_path = _configured_data_path("paths.character_data")
    return _load_entities(data_path, "characters")


def load_enemies():
    """Load all enemy JSON files and return a dict keyed by enemy id."""
    data_path = _configured_data_path("paths.enemy_data")
    return _load_entities(data_path, "enemies")


def get_character(unit_id):
    """Return one character record by id, or None when absent."""
    return load_characters().get(unit_id)


def get_enemy(enemy_id):
    """Return one enemy record by id, or None when absent."""
    return load_enemies().get(enemy_id)


def _configured_data_path(config_key):
    configured_path = get_config(config_key)
    if configured_path is None:
        LOGGER.warning("Missing data path config: %s", config_key)
        return None
    return BASE_DIR / configured_path


def _load_entities(data_path, collection_key):
    if data_path is None:
        return {}
    if not data_path.exists():
        LOGGER.warning("Data folder does not exist: %s", data_path)
        return {}

    entities = {}
    for json_file in sorted(data_path.glob("*.json")):
        try:
            with json_file.open("r", encoding="utf-8") as file:
                payload = json.load(file)
        except json.JSONDecodeError:
            LOGGER.error("Skipping invalid JSON file: %s", json_file)
            continue

        for entity in _extract_entities(payload, collection_key):
            entity_id = entity.get("id")
            if not entity_id:
                LOGGER.warning("Skipping entity without id in file: %s", json_file)
                continue
            entities[entity_id] = entity

    return entities


def _extract_entities(payload, collection_key):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        value = payload.get(collection_key, [])
        if isinstance(value, list):
            return value
    return []
