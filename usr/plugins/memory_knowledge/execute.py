from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path


def _bootstrap_a0_root() -> None:
    plugin_root = Path(__file__).resolve().parent
    for parent in (plugin_root, *plugin_root.parents):
        if (parent / "usr" / "plugins" / "memory_knowledge").exists():
            root = str(parent)
            if root not in sys.path:
                sys.path.insert(0, root)
            return


_bootstrap_a0_root()

try:
    from usr.plugins.memory_knowledge.helpers import db
except ModuleNotFoundError:
    from helpers import db


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_saved_config() -> dict:
    plugin_root = Path(__file__).resolve().parent
    a0_root = None
    for parent in (plugin_root, *plugin_root.parents):
        if (parent / "usr" / "plugins" / "memory_knowledge").exists():
            a0_root = parent
            break

    candidates = []
    explicit_config = os.environ.get("MEMORY_KNOWLEDGE_CONFIG")
    if explicit_config:
        candidates.append(Path(explicit_config))
    candidates.append(plugin_root / "config.json")
    if a0_root:
        candidates.extend(
            [
                a0_root / "usr" / "plugins" / "memory_knowledge" / "config.json",
                *a0_root.glob("usr/agents/*/plugins/memory_knowledge/config.json"),
                *a0_root.glob("projects/*/.a0proj/plugins/memory_knowledge/config.json"),
                *a0_root.glob("projects/*/.a0proj/agents/*/plugins/memory_knowledge/config.json"),
                *a0_root.rglob("plugins/memory_knowledge/config.json"),
            ]
        )

    config: dict = {}
    default_config = plugin_root / "default_config.yaml"
    if default_config.exists():
        try:
            import yaml

            data = yaml.safe_load(default_config.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                config = _deep_merge(config, data)
        except Exception as exc:
            print(f"WARN: failed to read defaults {default_config}: {exc}")
    loaded: list[str] = []
    seen: set[Path] = set()
    for path in candidates:
        path = path.resolve()
        if not path.exists() or path in seen:
            continue
        seen.add(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"WARN: failed to read config {path}: {exc}")
            continue
        if isinstance(data, dict):
            config = _deep_merge(config, data)
            loaded.append(str(path))
    if loaded:
        print("Loaded config:")
        for path in loaded:
            print(f"  {path}")
    else:
        print("No saved plugin config found; using environment/default settings.")
    return config


def main() -> int:
    print("Memory Knowledge setup check")
    if importlib.util.find_spec("psycopg") is None:
        print("ERROR: psycopg is not installed.")
        print("Install in the Agent Zero framework runtime with:")
        print("  python -m pip install 'psycopg[binary]>=3.2,<4'")
        return 1
    try:
        settings = db.load_settings(_load_saved_config())
        print("Database target:")
        if settings.auth_mode == "data_api":
            print(db.dump_json(db.sanitized_data_api_target(settings)))
        else:
            print(db.dump_json(db.sanitized_database_target(settings.database_url or "")))
        print(db.dump_json(db.health(settings)))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
