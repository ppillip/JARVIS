from __future__ import annotations

"""JARVIS의 SQLite 저장 계층.

MCP registry, prompts, workflow runs, trace, conversation timeline을
하나의 SQLite 저장소에 유지하는 공통 persistence 계층이다.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "jarvis.db"
LEGACY_MCP_REGISTRY_PATH = PROJECT_ROOT / "backend" / "app" / "mcp_registry.json"
LEGACY_PROMPT_DB_PATH = PROJECT_ROOT / "data" / "prompts.json"


def now_iso() -> str:
    """현재 UTC 시각을 ISO 문자열로 반환한다."""
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    """JARVIS SQLite DB 연결을 생성하고 기본 pragma를 적용한다."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_tables(conn: sqlite3.Connection) -> None:
    """필요한 테이블이 없으면 생성한다."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS mcp_registry (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            scope TEXT NOT NULL,
            description TEXT NOT NULL,
            capabilities_json TEXT NOT NULL,
            expected_input TEXT NOT NULL,
            expected_output TEXT NOT NULL,
            source_url TEXT,
            package_name TEXT,
            transport TEXT,
            auth_required INTEGER NOT NULL DEFAULT 0,
            risk_level TEXT NOT NULL DEFAULT 'low',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS prompt_definitions (
            id TEXT PRIMARY KEY,
            active_version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS prompt_versions (
            prompt_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (prompt_id, version),
            FOREIGN KEY (prompt_id) REFERENCES prompt_definitions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS workflow_runs (
            id TEXT PRIMARY KEY,
            command_text TEXT,
            phase TEXT NOT NULL,
            planner_type TEXT,
            fallback_used INTEGER NOT NULL DEFAULT 0,
            plan_json TEXT,
            tasks_json TEXT,
            report_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workflow_trace_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            event TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS conversation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )


def row_count(conn: sqlite3.Connection, table_name: str) -> int:
    """주어진 테이블의 행 개수를 반환한다."""
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
    return int(row["count"]) if row else 0


def normalize_legacy_prompt(entry: Dict[str, Any]) -> Dict[str, Any]:
    """레거시 prompt JSON 구조를 현재 버전 구조로 보정한다."""
    if isinstance(entry.get("versions"), list) and entry.get("active_version"):
        return entry

    updated_at = str(entry.get("updated_at") or now_iso())
    return {
        "id": str(entry.get("id", "")),
        "active_version": 1,
        "versions": [
            {
                "version": 1,
                "name": str(entry.get("name", entry.get("id", ""))),
                "description": str(entry.get("description", "")),
                "content": str(entry.get("content", "")),
                "created_at": updated_at,
            }
        ],
        "updated_at": updated_at,
    }


def migrate_legacy_registry(conn: sqlite3.Connection) -> None:
    """초기 실행 시 legacy MCP JSON을 SQLite 레지스트리로 이관한다."""
    if row_count(conn, "mcp_registry") > 0 or not LEGACY_MCP_REGISTRY_PATH.exists():
        return

    raw = json.loads(LEGACY_MCP_REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return

    timestamp = now_iso()
    for item in raw:
        conn.execute(
            """
            INSERT INTO mcp_registry (
                id, name, scope, description, capabilities_json, expected_input,
                expected_output, source_url, package_name, transport,
                auth_required, risk_level, enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.get("id"),
                item.get("name"),
                item.get("scope"),
                item.get("description"),
                json.dumps(item.get("capabilities", []), ensure_ascii=False),
                item.get("expected_input", ""),
                item.get("expected_output", ""),
                item.get("source_url"),
                item.get("package_name"),
                item.get("transport"),
                1 if item.get("auth_required") else 0,
                item.get("risk_level", "low"),
                1 if item.get("enabled", True) else 0,
                timestamp,
                timestamp,
            ),
        )


def migrate_legacy_prompts(conn: sqlite3.Connection) -> None:
    """초기 실행 시 legacy prompt JSON을 SQLite prompt DB로 이관한다."""
    if row_count(conn, "prompt_definitions") > 0 or not LEGACY_PROMPT_DB_PATH.exists():
        return

    raw = json.loads(LEGACY_PROMPT_DB_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return

    for item in raw:
        normalized = normalize_legacy_prompt(item)
        conn.execute(
            """
            INSERT INTO prompt_definitions (id, active_version, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                normalized["id"],
                int(normalized.get("active_version", 1)),
                normalized.get("updated_at", now_iso()),
                normalized.get("updated_at", now_iso()),
            ),
        )
        for version in normalized.get("versions", []):
            conn.execute(
                """
                INSERT INTO prompt_versions (prompt_id, version, name, description, content, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["id"],
                    int(version.get("version", 1)),
                    version.get("name", normalized["id"]),
                    version.get("description", ""),
                    version.get("content", ""),
                    version.get("created_at", now_iso()),
                ),
            )


def initialize_database() -> None:
    """DB 생성과 legacy 데이터 마이그레이션을 한 번에 수행한다."""
    with connect() as conn:
        create_tables(conn)
        migrate_legacy_registry(conn)
        migrate_legacy_prompts(conn)
        conn.commit()


def list_registry_entries() -> List[Dict[str, Any]]:
    """MCP registry 테이블의 전체 항목을 반환한다."""
    initialize_database()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, name, scope, description, capabilities_json, expected_input,
                   expected_output, source_url, package_name, transport,
                   auth_required, risk_level, enabled, created_at, updated_at
            FROM mcp_registry
            ORDER BY name COLLATE NOCASE
            """
        ).fetchall()

    items: List[Dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "id": row["id"],
                "name": row["name"],
                "scope": row["scope"],
                "description": row["description"],
                "capabilities": json.loads(row["capabilities_json"] or "[]"),
                "expected_input": row["expected_input"],
                "expected_output": row["expected_output"],
                "source_url": row["source_url"],
                "package_name": row["package_name"],
                "transport": row["transport"],
                "auth_required": bool(row["auth_required"]),
                "risk_level": row["risk_level"],
                "enabled": bool(row["enabled"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
    return items


def create_registry_entry(payload: Dict[str, Any]) -> Dict[str, Any]:
    """MCP registry 테이블에 신규 항목을 저장한다."""
    initialize_database()
    timestamp = now_iso()
    with connect() as conn:
        try:
            conn.execute(
                """
                INSERT INTO mcp_registry (
                    id, name, scope, description, capabilities_json, expected_input,
                    expected_output, source_url, package_name, transport,
                    auth_required, risk_level, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["id"],
                    payload["name"],
                    payload["scope"],
                    payload["description"],
                    json.dumps(payload.get("capabilities", []), ensure_ascii=False),
                    payload["expected_input"],
                    payload["expected_output"],
                    payload.get("source_url"),
                    payload.get("package_name"),
                    payload.get("transport"),
                    1 if payload.get("auth_required") else 0,
                    payload.get("risk_level", "low"),
                    1 if payload.get("enabled", True) else 0,
                    timestamp,
                    timestamp,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("MCP id already exists.") from exc

    for item in list_registry_entries():
        if item["id"] == payload["id"]:
            return item
    raise ValueError("Created MCP could not be read back.")


def update_registry_enabled(mcp_id: str, enabled: bool) -> Dict[str, Any]:
    """특정 MCP registry 항목의 enabled 값을 갱신한다."""
    initialize_database()
    timestamp = now_iso()
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE mcp_registry SET enabled = ?, updated_at = ? WHERE id = ?",
            (1 if enabled else 0, timestamp, mcp_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            raise KeyError("MCP not found.")

    for item in list_registry_entries():
        if item["id"] == mcp_id:
            return item
    raise KeyError("MCP not found.")


def list_prompt_entries() -> List[Dict[str, Any]]:
    """Prompt 정의와 활성 버전을 합쳐 전체 목록을 반환한다."""
    initialize_database()
    with connect() as conn:
        defs = conn.execute(
            """
            SELECT id, active_version, created_at, updated_at
            FROM prompt_definitions
            ORDER BY id COLLATE NOCASE
            """
        ).fetchall()

        items: List[Dict[str, Any]] = []
        for row in defs:
            versions = conn.execute(
                """
                SELECT version, name, description, content, created_at
                FROM prompt_versions
                WHERE prompt_id = ?
                ORDER BY version ASC
                """,
                (row["id"],),
            ).fetchall()
            active_version = int(row["active_version"])
            active = next((version for version in versions if int(version["version"]) == active_version), None)
            items.append(
                {
                    "id": row["id"],
                    "name": active["name"] if active else "",
                    "description": active["description"] if active else "",
                    "content": active["content"] if active else "",
                    "active_version": active_version,
                    "updated_at": row["updated_at"],
                    "versions": [
                        {
                            "version": int(version["version"]),
                            "name": version["name"],
                            "description": version["description"],
                            "content": version["content"],
                            "created_at": version["created_at"],
                        }
                        for version in versions
                    ],
                }
            )
    return items


def get_prompt_entry(prompt_id: str) -> Optional[Dict[str, Any]]:
    """단일 프롬프트의 현재 활성 버전 정보를 반환한다."""
    for item in list_prompt_entries():
        if item["id"] == prompt_id:
            return item
    return None


def create_prompt_entry(payload: Dict[str, Any]) -> Dict[str, Any]:
    """새 프롬프트와 v1 버전을 생성한다."""
    initialize_database()
    timestamp = now_iso()
    with connect() as conn:
        try:
            conn.execute(
                """
                INSERT INTO prompt_definitions (id, active_version, created_at, updated_at)
                VALUES (?, 1, ?, ?)
                """,
                (payload["id"], timestamp, timestamp),
            )
            conn.execute(
                """
                INSERT INTO prompt_versions (prompt_id, version, name, description, content, created_at)
                VALUES (?, 1, ?, ?, ?, ?)
                """,
                (
                    payload["id"],
                    payload["name"],
                    payload["description"],
                    payload["content"],
                    timestamp,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("Prompt id already exists.") from exc

    item = get_prompt_entry(payload["id"])
    if not item:
        raise ValueError("Created prompt could not be read back.")
    return item


def append_prompt_version(prompt_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """프롬프트 새 버전을 추가하고 활성 버전으로 전환한다."""
    initialize_database()
    timestamp = now_iso()
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM prompt_definitions WHERE id = ?",
            (prompt_id,),
        ).fetchone()
        if not row:
            raise KeyError("Prompt not found.")

        next_version_row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS next_version FROM prompt_versions WHERE prompt_id = ?",
            (prompt_id,),
        ).fetchone()
        next_version = int(next_version_row["next_version"])

        conn.execute(
            """
            INSERT INTO prompt_versions (prompt_id, version, name, description, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                prompt_id,
                next_version,
                payload["name"],
                payload["description"],
                payload["content"],
                timestamp,
            ),
        )
        conn.execute(
            "UPDATE prompt_definitions SET active_version = ?, updated_at = ? WHERE id = ?",
            (next_version, timestamp, prompt_id),
        )
        conn.commit()

    item = get_prompt_entry(prompt_id)
    if not item:
        raise KeyError("Prompt not found.")
    return item


def activate_prompt_version(prompt_id: str, version: int) -> Dict[str, Any]:
    """기존 프롬프트 버전을 다시 활성 버전으로 지정한다."""
    initialize_database()
    timestamp = now_iso()
    with connect() as conn:
        version_row = conn.execute(
            "SELECT version FROM prompt_versions WHERE prompt_id = ? AND version = ?",
            (prompt_id, version),
        ).fetchone()
        if not version_row:
            raise KeyError("Prompt version not found.")

        cursor = conn.execute(
            "UPDATE prompt_definitions SET active_version = ?, updated_at = ? WHERE id = ?",
            (version, timestamp, prompt_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            raise KeyError("Prompt not found.")

    item = get_prompt_entry(prompt_id)
    if not item:
        raise KeyError("Prompt not found.")
    return item


def delete_prompt_entry(prompt_id: str) -> Dict[str, Any]:
    """프롬프트 정의와 모든 버전을 삭제한다."""
    initialize_database()
    current = get_prompt_entry(prompt_id)
    if not current:
        raise KeyError("Prompt not found.")

    with connect() as conn:
        cursor = conn.execute("DELETE FROM prompt_definitions WHERE id = ?", (prompt_id,))
        conn.commit()
        if cursor.rowcount == 0:
            raise KeyError("Prompt not found.")
    return current


def upsert_workflow_run(
    run_id: str,
    *,
    phase: str,
    command_text: Optional[str] = None,
    planner_type: Optional[str] = None,
    fallback_used: bool = False,
    plan: Optional[Dict[str, Any]] = None,
    tasks: Optional[List[Dict[str, Any]]] = None,
    report: Optional[Dict[str, Any]] = None,
) -> None:
    """워크플로우 실행 단위의 최신 스냅샷을 저장한다."""
    initialize_database()
    timestamp = now_iso()
    with connect() as conn:
        existing = conn.execute("SELECT id FROM workflow_runs WHERE id = ?", (run_id,)).fetchone()
        payload = (
            command_text,
            phase,
            planner_type,
            1 if fallback_used else 0,
            json.dumps(plan, ensure_ascii=False) if plan is not None else None,
            json.dumps(tasks, ensure_ascii=False) if tasks is not None else None,
            json.dumps(report, ensure_ascii=False) if report is not None else None,
            timestamp,
            run_id,
        )
        if existing:
            conn.execute(
                """
                UPDATE workflow_runs
                SET command_text = COALESCE(?, command_text),
                    phase = ?,
                    planner_type = COALESCE(?, planner_type),
                    fallback_used = ?,
                    plan_json = COALESCE(?, plan_json),
                    tasks_json = COALESCE(?, tasks_json),
                    report_json = COALESCE(?, report_json),
                    updated_at = ?
                WHERE id = ?
                """,
                payload,
            )
        else:
            conn.execute(
                """
                INSERT INTO workflow_runs (
                    id, command_text, phase, planner_type, fallback_used,
                    plan_json, tasks_json, report_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    command_text,
                    phase,
                    planner_type,
                    1 if fallback_used else 0,
                    json.dumps(plan, ensure_ascii=False) if plan is not None else None,
                    json.dumps(tasks, ensure_ascii=False) if tasks is not None else None,
                    json.dumps(report, ensure_ascii=False) if report is not None else None,
                    timestamp,
                    timestamp,
                ),
            )
        conn.commit()


def replace_workflow_trace(run_id: str, trace: List[Dict[str, Any]]) -> None:
    """주어진 run의 trace 이벤트를 순서대로 교체 저장한다."""
    initialize_database()
    timestamp = now_iso()
    with connect() as conn:
        conn.execute("DELETE FROM workflow_trace_events WHERE run_id = ?", (run_id,))
        for index, item in enumerate(trace, start=1):
            conn.execute(
                """
                INSERT INTO workflow_trace_events (run_id, sequence_no, event, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    index,
                    str(item.get("event", "unknown")),
                    json.dumps(item, ensure_ascii=False),
                    timestamp,
                ),
            )
        conn.commit()


def get_workflow_run(run_id: str) -> Optional[Dict[str, Any]]:
    """저장된 단일 워크플로우 실행 스냅샷과 trace를 반환한다."""
    initialize_database()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id, command_text, phase, planner_type, fallback_used,
                   plan_json, tasks_json, report_json, created_at, updated_at
            FROM workflow_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if not row:
            return None
        trace_rows = conn.execute(
            """
            SELECT payload_json
            FROM workflow_trace_events
            WHERE run_id = ?
            ORDER BY sequence_no ASC
            """,
            (run_id,),
        ).fetchall()
    return {
        "id": row["id"],
        "command_text": row["command_text"],
        "phase": row["phase"],
        "planner_type": row["planner_type"],
        "fallback_used": bool(row["fallback_used"]),
        "plan": json.loads(row["plan_json"]) if row["plan_json"] else None,
        "tasks": json.loads(row["tasks_json"]) if row["tasks_json"] else [],
        "report": json.loads(row["report_json"]) if row["report_json"] else None,
        "trace": [json.loads(item["payload_json"]) for item in trace_rows],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_workflow_runs(limit: int = 20) -> List[Dict[str, Any]]:
    """최근 워크플로우 실행 목록을 최신순으로 반환한다."""
    initialize_database()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, command_text, phase, planner_type, fallback_used,
                   created_at, updated_at
            FROM workflow_runs
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "command_text": row["command_text"],
            "phase": row["phase"],
            "planner_type": row["planner_type"],
            "fallback_used": bool(row["fallback_used"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def append_conversation_event(conversation_id: str, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """세션 대화 타임라인에 새 이벤트를 순서대로 추가한다."""
    initialize_database()
    timestamp = now_iso()
    with connect() as conn:
        next_row = conn.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_sequence FROM conversation_events WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        sequence_no = int(next_row["next_sequence"]) if next_row else 1
        cursor = conn.execute(
            """
            INSERT INTO conversation_events (conversation_id, sequence_no, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                sequence_no,
                event_type,
                json.dumps(payload, ensure_ascii=False),
                timestamp,
            ),
        )
        conn.commit()
    return {
        "id": int(cursor.lastrowid),
        "conversation_id": conversation_id,
        "sequence_no": sequence_no,
        "event_type": event_type,
        "payload": payload,
        "created_at": timestamp,
    }


def list_conversation_events(conversation_id: str) -> List[Dict[str, Any]]:
    """세션 대화 타임라인 이벤트를 시간순으로 반환한다."""
    initialize_database()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, conversation_id, sequence_no, event_type, payload_json, created_at
            FROM conversation_events
            WHERE conversation_id = ?
            ORDER BY sequence_no ASC, id ASC
            """,
            (conversation_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "conversation_id": row["conversation_id"],
            "sequence_no": row["sequence_no"],
            "event_type": row["event_type"],
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]
