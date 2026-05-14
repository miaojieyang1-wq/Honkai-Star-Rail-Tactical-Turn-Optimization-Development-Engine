# -*- coding: utf-8 -*-
"""Unified configuration access for environment and YAML settings."""

from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
CONFIG_FILE = BASE_DIR / "config.yaml"


def _parse_env_file(env_file):
    """Parse a simple .env file into a dictionary."""
    if not env_file.exists():
        return {}

    values = {}
    with env_file.open("r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                values[key] = value

    return values


def _parse_scalar(value):
    """Parse a YAML scalar used by the project config."""
    value = value.strip()
    if not value:
        return ""

    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]

    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none"}:
        return None

    try:
        return int(value)
    except ValueError:
        pass

    try:
        return float(value)
    except ValueError:
        return value


def _parse_simple_yaml(config_file):
    """Parse the simple nested mapping shape used by config.yaml."""
    if not config_file.exists():
        return {}

    root = {}
    stack = [(-1, root)]

    with config_file.open("r", encoding="utf-8") as file:
        for raw_line in file:
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue

            indent = len(raw_line) - len(raw_line.lstrip(" "))
            line = raw_line.strip()
            if ":" not in line:
                continue

            key, raw_value = line.split(":", 1)
            key = key.strip()
            raw_value = raw_value.strip()

            while stack and indent <= stack[-1][0]:
                stack.pop()

            current = stack[-1][1]
            if raw_value:
                current[key] = _parse_scalar(raw_value)
            else:
                current[key] = {}
                stack.append((indent, current[key]))

    return root


def _load_yaml_config(config_file):
    """Load YAML config with PyYAML when available, otherwise use a small parser."""
    try:
        import yaml
    except ImportError:
        return _parse_simple_yaml(config_file)

    if not config_file.exists():
        return {}

    with config_file.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def get_env(key):
    """Return a .env value by key, or None when it is not configured."""
    env_values = _parse_env_file(ENV_FILE)
    return env_values.get(key)


def get_config(key):
    """Return a config value by dotted path, or None when the path is missing."""
    config = _load_yaml_config(CONFIG_FILE)
    current = config

    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]

    return current
