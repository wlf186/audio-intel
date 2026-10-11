"""Single-group membership and submission-time voiceprint scope snapshots."""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

UNGROUPED_ID = "voiceprint_ungrouped"
UNGROUPED_NAME = "未分组"


def normalize_group_name(value: str) -> str:
    from .db import normalize_person_name
    name = normalize_person_name(value)
    if len(name) > 80:
        raise ValueError("Group name must contain 1–80 characters")
    return name


def migrate(connection: sqlite3.Connection) -> None:
    from .db import person_name_key, utcnow
    connection.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS voiceprint_groups (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS voiceprint_memberships (
            person_id TEXT PRIMARY KEY REFERENCES voiceprint_people(id) ON DELETE CASCADE,
            group_id TEXT NOT NULL REFERENCES voiceprint_groups(id)
        );
        CREATE INDEX IF NOT EXISTS idx_voiceprint_memberships_group ON voiceprint_memberships(group_id);
    """)
    now = utcnow()
    connection.execute("INSERT OR IGNORE INTO voiceprint_groups VALUES(?,?,?,?,?)",
                       (UNGROUPED_ID, UNGROUPED_NAME, person_name_key(UNGROUPED_NAME), now, now))
    connection.execute("INSERT OR IGNORE INTO voiceprint_memberships SELECT id,? FROM voiceprint_people", (UNGROUPED_ID,))
    # Imports and legacy voice creation use the same database invariant.
    connection.execute(f"""CREATE TRIGGER IF NOT EXISTS voiceprint_default_membership
        AFTER INSERT ON voiceprint_people BEGIN
        INSERT INTO voiceprint_memberships(person_id,group_id) VALUES(NEW.id,'{UNGROUPED_ID}'); END""")
    connection.execute("UPDATE schema_meta SET version=14 WHERE version<14")
    connection.commit()


def require_group(connection: sqlite3.Connection, group_id: str) -> None:
    if not isinstance(group_id, str) or not connection.execute("SELECT 1 FROM voiceprint_groups WHERE id=?", (group_id,)).fetchone():
        raise ValueError("Unknown voiceprint group ID")


def list_groups() -> list[dict[str, Any]]:
    from .db import connect
    with connect() as connection:
        return [dict(row) | {"builtin": row["id"] == UNGROUPED_ID} for row in connection.execute(
            """SELECT g.*,COUNT(m.person_id) AS person_count FROM voiceprint_groups g
               LEFT JOIN voiceprint_memberships m ON m.group_id=g.id GROUP BY g.id
               ORDER BY (g.id=?) DESC,g.name_key,g.id""", (UNGROUPED_ID,))]


def create_group(name: str) -> dict[str, Any]:
    from .db import connect, person_name_key, utcnow
    name = normalize_group_name(name)
    identifier, now = "group_" + uuid.uuid4().hex[:16], utcnow()
    with connect() as connection:
        connection.execute("INSERT INTO voiceprint_groups VALUES(?,?,?,?,?)", (identifier, name, person_name_key(name), now, now))
    return {"id": identifier, "name": name, "builtin": False, "person_count": 0, "created_at": now, "updated_at": now}


def rename_group(identifier: str, name: str) -> dict[str, Any]:
    from .db import connect, person_name_key, utcnow
    name = normalize_group_name(name)
    if identifier == UNGROUPED_ID:
        raise ValueError("The built-in ungrouped group cannot be renamed")
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        require_group(connection, identifier)
        connection.execute("UPDATE voiceprint_groups SET name=?,name_key=?,updated_at=? WHERE id=?", (name, person_name_key(name), utcnow(), identifier))
        result = dict(connection.execute("SELECT g.*,(SELECT COUNT(*) FROM voiceprint_memberships WHERE group_id=g.id) AS person_count FROM voiceprint_groups g WHERE id=?", (identifier,)).fetchone())
        connection.commit()
    return result | {"builtin": False}


def delete_group(identifier: str) -> None:
    from .db import connect
    if identifier == UNGROUPED_ID:
        raise ValueError("The built-in ungrouped group cannot be deleted")
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        require_group(connection, identifier)
        connection.execute("UPDATE voiceprint_memberships SET group_id=? WHERE group_id=?", (UNGROUPED_ID, identifier))
        connection.execute("DELETE FROM voiceprint_groups WHERE id=?", (identifier,))
        connection.commit()


def move_people(identifiers: list[str], group_id: str) -> None:
    from .db import connect
    if not identifiers or len(set(identifiers)) != len(identifiers):
        raise ValueError("Provide unique person IDs")
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        require_group(connection, group_id)
        for identifier in identifiers:
            if not connection.execute("SELECT 1 FROM voiceprint_memberships WHERE person_id=?", (identifier,)).fetchone():
                raise ValueError(f"Unknown voiceprint person ID: {identifier}")
        connection.executemany("UPDATE voiceprint_memberships SET group_id=? WHERE person_id=?", [(group_id, identifier) for identifier in identifiers])
        connection.commit()


def scope_snapshot(raw_ids: str | list[str] | None, enabled: bool, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    from .db import connect
    try:
        ids = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
    except (ValueError, TypeError) as exc:
        raise ValueError("voiceprint_group_ids must be a JSON array") from exc
    if ids is not None and (not isinstance(ids, list) or any(not isinstance(item, str) or not item for item in ids)):
        raise ValueError("voiceprint_group_ids must contain nonempty string IDs")
    ids = sorted(set(ids)) if ids is not None else None
    selection = {"voiceprint_group_ids": ids}
    if existing is not None:
        stored = existing.get("request") or {}
        return {**selection, **{key: stored[key] for key in ("voiceprint_groups", "voiceprint_person_ids") if key in stored}}
    if not enabled:
        return {**selection, "voiceprint_groups": [], "voiceprint_person_ids": []}
    if ids == []:
        raise ValueError("Select at least one voiceprint group")
    with connect() as connection:
        # One read transaction fixes both group names and eligible identities.
        connection.execute("BEGIN")
        if ids is not None:
            for identifier in ids:
                require_group(connection, identifier)
        groups = [dict(row) for row in connection.execute("SELECT id,name FROM voiceprint_groups ORDER BY id") if ids is None or row["id"] in ids]
        selected = {group["id"] for group in groups}
        people = [row["person_id"] for row in connection.execute("SELECT person_id,group_id FROM voiceprint_memberships ORDER BY person_id") if row["group_id"] in selected]
    return {**selection, "voiceprint_groups": groups, "voiceprint_person_ids": people}
