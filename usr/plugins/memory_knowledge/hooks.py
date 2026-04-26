from __future__ import annotations

import importlib.util


def install() -> None:
    print("Memory Knowledge setup")
    if importlib.util.find_spec("psycopg") is None:
        print("Missing dependency: psycopg. Install with: python -m pip install 'psycopg[binary]>=3.2,<4'")
        return
    print("psycopg is available.")
    print("Configure MEMORY_DATABASE_URL or SUPABASE_DB_URL plus MEMORY_TENANT_ID before enabling autonomous writes.")


def pre_update() -> None:
    print("Memory Knowledge pre-update check complete. Runtime buffers are per-agent and are not persisted locally.")
