from __future__ import annotations

import importlib.util
import sys

from usr.plugins.memory_knowledge.helpers import db


def main() -> int:
    print("Memory Knowledge setup check")
    if importlib.util.find_spec("psycopg") is None:
        print("ERROR: psycopg is not installed.")
        print("Install in the Agent Zero framework runtime with:")
        print("  python -m pip install 'psycopg[binary]>=3.2,<4'")
        return 1

    try:
        settings = db.load_settings()
        summary = db.health(settings)
    except Exception as exc:
        print(f"ERROR: setup check failed: {exc}")
        return 1

    print(db.dump_json(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
