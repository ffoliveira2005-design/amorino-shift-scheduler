"""Operational SQLite helpers. Never imports the Flask application."""
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
APP_TABLES = {"funcionarios", "turnos", "semanas", "disponibilidades", "descargas", "trocas"}


@contextmanager
def readonly(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(f"Database does not exist: {path}")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        yield connection
    finally:
        connection.close()


def validate_database(path, required_tables=APP_TABLES):
    """Validate without creating a missing DB or executing application migrations."""
    with readonly(path) as connection:
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise ValueError("Database failed integrity_check")
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = set(required_tables) - tables
        if missing:
            raise ValueError("Database lacks application tables: " + ", ".join(sorted(missing)))
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Database failed foreign_key_check")
        return {name: connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                for name in sorted(set(required_tables))}


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)


def verify_manifest(database, require_manifest=False):
    manifest = Path(str(database) + ".json")
    if not manifest.is_file():
        if require_manifest:
            raise ValueError("Backup manifest is missing")
        return None
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("sha256") != sha256_file(database):
        raise ValueError("Backup checksum does not match its manifest")
    return data
