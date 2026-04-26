from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

import psycopg
from psycopg.rows import dict_row


class ConfigError(RuntimeError):
    """Raised when required runtime configuration is missing."""


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def database_url() -> str:
    url = (
        os.environ.get("MEMORY_DATABASE_URL")
        or os.environ.get("SUPABASE_DB_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not url:
        raise ConfigError(
            "Set MEMORY_DATABASE_URL, SUPABASE_DB_URL, or DATABASE_URL to a Postgres connection string."
        )
    return url


@contextmanager
def connect() -> Iterator[psycopg.Connection[dict[str, Any]]]:
    load_dotenv()
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        yield conn


def fetch_all(conn: psycopg.Connection[dict[str, Any]], sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def fetch_one(conn: psycopg.Connection[dict[str, Any]], sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(conn: psycopg.Connection[dict[str, Any]], sql: str, params: Sequence[Any] = ()) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def dump_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def parse_json_file(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def require_uuid(value: str | None, name: str) -> str:
    if not value:
        raise ConfigError(f"Missing required {name}. Pass a flag or set the matching environment variable.")
    return value


def vector_literal(values: Sequence[float], dimensions: int = 384) -> str:
    if len(values) != dimensions:
        raise ValueError(f"Expected {dimensions} embedding values, got {len(values)}.")
    return "[" + ",".join(str(float(v)) for v in values) + "]"
