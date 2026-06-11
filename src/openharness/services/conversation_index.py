"""Derived SQLite FTS5 search index over saved session snapshots.

The JSON session snapshots (``services/session_storage.py``) remain the
source of truth; this index is a rebuildable cache. That choice removes the
hardest problems of hermes-agent's equivalent (``hermes_state.py``), whose
SQLite database is primary storage: corruption recovery here is "delete the
file and rebuild", and schema changes are a version bump plus reindex.

Ported from hermes because they are right: WAL with DELETE-journal fallback
on network filesystems, ``BEGIN IMMEDIATE`` writes with jittered busy
retries, FTS5 module probing with graceful degradation, and id-ordered
message windows. Deliberately different: secrets are redacted before
indexing, indexed bodies are capped, counters are real ``COUNT(*)`` values,
and the model-facing query sanitizer is a parser that re-emits a
guaranteed-valid FTS5 expression instead of regex surgery.
"""

from __future__ import annotations

import json
import logging
import random
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from openharness.config.paths import get_data_dir
from openharness.memory.team import SECRET_RULES

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DB_FILE_NAME = "conversation_index.db"

# Indexed bodies are for discovery, not archival: full text stays in the
# snapshots. Cap keeps the index small and search responses bounded.
INDEXED_BODY_CAP = 8_000
# Trigram tokenizer cannot match shorter needles; route those to LIKE.
MIN_FTS_QUERY_CHARS = 3

_WAL_INCOMPAT_MARKERS = ("locking protocol", "not authorized")
_BUSY_MARKERS = ("locked", "busy")
_WRITE_RETRIES = 15

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'local',
    model TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    started_at REAL,
    last_active REAL,
    message_count INTEGER NOT NULL DEFAULT 0,
    indexed_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    project TEXT NOT NULL,
    snapshot_idx INTEGER NOT NULL,
    role TEXT NOT NULL,
    ts REAL,
    body TEXT NOT NULL DEFAULT '',
    tool_name TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project, last_active DESC);
"""

_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    body, tool_name,
    content='messages', content_rowid='id',
    tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, body, tool_name)
    VALUES (new.id, new.body, new.tool_name);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, body, tool_name)
    VALUES ('delete', old.id, old.body, old.tool_name);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, body, tool_name)
    VALUES ('delete', old.id, old.body, old.tool_name);
    INSERT INTO messages_fts(rowid, body, tool_name)
    VALUES (new.id, new.body, new.tool_name);
END;
"""


def redact_secrets(text: str) -> str:
    """Replace anything matching the team-memory secret rules.

    Secrets that appear in tool output must never become permanently
    searchable — the index outlives the conversation.
    """
    for rule_id, _label, pattern in SECRET_RULES:
        text = pattern.sub(f"[redacted:{rule_id}]", text)
    return text


def sanitize_fts_query(raw: str) -> str | None:
    """Re-emit model-supplied text as a guaranteed-valid FTS5 expression.

    Every term is emitted quoted (with a preserved trailing ``*`` prefix
    operator); only AND/OR/NOT between terms are honored. Returns None when
    nothing searchable remains — callers should surface that explicitly
    instead of reporting fake "no matches".
    """
    tokens = re.findall(r'"[^"]*"|\S+', raw or "")
    parts: list[str] = []
    pending_op: str | None = None
    for token in tokens:
        if token in {"AND", "OR", "NOT"}:
            if parts:
                pending_op = token
            continue
        if token.startswith('"') and token.endswith('"') and len(token) >= 2:
            term = token[1:-1]
            prefix = False
        else:
            prefix = token.endswith("*")
            term = token.rstrip("*")
        term = term.replace('"', " ").strip()
        if not term:
            continue
        rendered = f'"{term}"*' if prefix else f'"{term}"'
        if parts and pending_op:
            parts.append(pending_op)
        parts.append(rendered)
        pending_op = None
    return " ".join(parts) if parts else None


def _flatten_message(message: dict[str, Any]) -> tuple[str, str]:
    """Reduce a snapshot message dict to (body, tool_names)."""
    bodies: list[str] = []
    tool_names: list[str] = []
    for block in message.get("content") or []:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                bodies.append(text)
        elif block_type == "tool_result":
            content = block.get("content")
            if isinstance(content, str) and content.strip():
                bodies.append(content)
        elif block_type == "tool_use":
            name = block.get("name")
            if isinstance(name, str) and name:
                tool_names.append(name)
    body = redact_secrets("\n".join(bodies))[:INDEXED_BODY_CAP]
    return body, " ".join(tool_names)


class ConversationIndex:
    """Thread-safe index handle. One instance per database file."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else get_data_dir() / DB_FILE_NAME
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self.fts_enabled = True
        self._open()

    # -- connection / schema -------------------------------------------------

    def _open(self) -> None:
        try:
            self._conn = self._connect_and_init()
        except sqlite3.DatabaseError as exc:
            # Derived cache: recovery is delete-and-recreate, never surgery.
            log.warning("conversation index unreadable (%s); rebuilding file", exc)
            from openharness.diagnostics import record

            record("index", "rebuild", "completed", level="warning", attrs={"reason": "corrupt"})
            try:
                self.db_path.unlink(missing_ok=True)
                for suffix in ("-wal", "-shm"):
                    Path(str(self.db_path) + suffix).unlink(missing_ok=True)
            except OSError:
                pass
            self._conn = self._connect_and_init()

    def _connect_and_init(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.db_path), timeout=1.0, isolation_level=None, check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as exc:
            if any(marker in str(exc).lower() for marker in _WAL_INCOMPAT_MARKERS):
                conn.execute("PRAGMA journal_mode=DELETE")
            else:
                raise
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA_VERSION):
            # Old schema: rebuildable cache, so recreate rather than migrate.
            conn.close()
            self.db_path.unlink(missing_ok=True)
            return self._connect_and_init()
        conn.executescript(_SCHEMA_SQL)
        self.fts_enabled = self._probe_fts(conn)
        if self.fts_enabled:
            conn.executescript(_FTS_SQL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return conn

    @staticmethod
    def _probe_fts(conn: sqlite3.Connection) -> bool:
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS temp._fts_probe USING fts5(x, tokenize='trigram')"
            )
            conn.execute("DROP TABLE IF EXISTS temp._fts_probe")
            return True
        except sqlite3.OperationalError:
            log.warning("SQLite FTS5 (trigram) unavailable; conversation search degrades to LIKE")
            from openharness.diagnostics import record

            record("index", "fts_probe", "failed", level="warning", attrs={"reason": "fts5_unavailable"})
            return False

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _write(self, fn) -> Any:
        """Run ``fn(conn)`` inside BEGIN IMMEDIATE with jittered busy retry."""
        assert self._conn is not None
        for attempt in range(_WRITE_RETRIES):
            with self._lock:
                try:
                    self._conn.execute("BEGIN IMMEDIATE")
                    try:
                        result = fn(self._conn)
                        self._conn.execute("COMMIT")
                        return result
                    except BaseException:
                        self._conn.execute("ROLLBACK")
                        raise
                except sqlite3.OperationalError as exc:
                    if attempt < _WRITE_RETRIES - 1 and any(
                        marker in str(exc).lower() for marker in _BUSY_MARKERS
                    ):
                        from openharness.diagnostics import record

                        record("index", "write", "retry", level="debug", attrs={"reason": "busy"})
                    else:
                        raise
            time.sleep(random.uniform(0.02, 0.15))
        raise sqlite3.OperationalError("conversation index busy")

    # -- indexing -------------------------------------------------------------

    def index_snapshot(self, payload: dict[str, Any], *, source: str = "local") -> None:
        """Incrementally index one saved snapshot payload.

        Appends messages beyond the session's ``indexed_count``; a shrunken
        snapshot (compaction rewrote history) triggers a full reindex of
        that session.
        """
        session_id = payload.get("session_id")
        messages = payload.get("messages")
        if not isinstance(session_id, str) or not session_id or not isinstance(messages, list):
            return
        project = str(payload.get("cwd") or "")
        model = str(payload.get("model") or "")
        title = str(payload.get("summary") or "")[:120]
        started_at = payload.get("created_at")

        def _apply(conn: sqlite3.Connection) -> None:
            row = conn.execute(
                "SELECT indexed_count FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            indexed = int(row["indexed_count"]) if row is not None else 0
            if indexed > len(messages):
                conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
                indexed = 0
            now = time.time()
            for idx in range(indexed, len(messages)):
                message = messages[idx]
                if not isinstance(message, dict):
                    continue
                body, tool_names = _flatten_message(message)
                if not body and not tool_names:
                    continue
                conn.execute(
                    "INSERT INTO messages(session_id, project, snapshot_idx, role, ts, body, tool_name)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (session_id, project, idx, str(message.get("role") or ""), now, body, tool_names),
                )
            conn.execute(
                "INSERT INTO sessions(session_id, project, source, model, title, started_at,"
                " last_active, message_count, indexed_count)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(session_id) DO UPDATE SET project=excluded.project,"
                " source=excluded.source, model=excluded.model, title=excluded.title,"
                " last_active=excluded.last_active, message_count=excluded.message_count,"
                " indexed_count=excluded.indexed_count",
                (
                    session_id,
                    project,
                    source,
                    model,
                    title,
                    float(started_at) if isinstance(started_at, (int, float)) else now,
                    now,
                    len(messages),
                    len(messages),
                ),
            )

        update_start = time.monotonic()
        self._write(_apply)
        from openharness.diagnostics import record

        record(
            "index",
            "index_update",
            "completed",
            level="debug",
            duration_ms=(time.monotonic() - update_start) * 1000.0,
            session_id=session_id,
        )

    def rebuild(self) -> int:
        """Drop everything and reindex every snapshot on disk."""
        from openharness.config.paths import get_sessions_dir

        def _clear(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM messages")
            conn.execute("DELETE FROM sessions")

        self._write(_clear)
        count = 0
        sessions_root = get_sessions_dir()
        for snapshot_path in sorted(sessions_root.glob("*/session-*.json")):
            try:
                payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            self.index_snapshot(payload)
            count += 1
        return count

    # -- queries --------------------------------------------------------------

    def _session_meta(self, conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT session_id, project, source, model, title, started_at, last_active,"
            " message_count FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def search(
        self,
        raw_query: str,
        *,
        project: str | None = None,
        limit: int = 3,
        sort: str | None = None,
        role_filter: list[str] | None = None,
        exclude_session: str | None = None,
    ) -> dict[str, Any]:
        """Discovery search. Returns {"hits": [...]} or {"error": "..."}."""
        start = time.monotonic()
        result = self._search_impl(
            raw_query,
            project=project,
            limit=limit,
            sort=sort,
            role_filter=role_filter,
            exclude_session=exclude_session,
        )
        from openharness.diagnostics import record

        record(
            "index",
            "index_search",
            "failed" if "error" in result else "completed",
            status="error" if "error" in result else "ok",
            duration_ms=(time.monotonic() - start) * 1000.0,
            attrs={
                "operation_kind": "discover",
                "fts_enabled": self.fts_enabled,
                "hits": len(result.get("hits", [])),
                "reason": str(result.get("error", ""))[:80] or None,
            },
        )
        return result

    def _search_impl(
        self,
        raw_query: str,
        *,
        project: str | None = None,
        limit: int = 3,
        sort: str | None = None,
        role_filter: list[str] | None = None,
        exclude_session: str | None = None,
    ) -> dict[str, Any]:
        assert self._conn is not None
        limit = max(1, min(int(limit), 10))
        roles = [r.strip() for r in (role_filter or ["user", "assistant"]) if r.strip()]
        needle = (raw_query or "").strip()
        use_fts = self.fts_enabled and len(needle.replace('"', "")) >= MIN_FTS_QUERY_CHARS

        filters = []
        params: list[Any] = []
        if project and project != "all":
            filters.append("m.project = ?")
            params.append(project)
        if roles:
            filters.append(f"m.role IN ({','.join('?' * len(roles))})")
            params.extend(roles)
        if exclude_session:
            filters.append("m.session_id != ?")
            params.append(exclude_session)
        filter_sql = (" AND " + " AND ".join(filters)) if filters else ""

        with self._lock:
            if use_fts:
                match_expr = sanitize_fts_query(needle)
                if match_expr is None:
                    return {"error": "Query contained no searchable terms after sanitization."}
                order = {
                    "newest": "m.ts DESC, rank",
                    "oldest": "m.ts ASC, rank",
                }.get(sort or "", "rank")
                sql = (
                    "SELECT m.id, m.session_id, m.role, m.snapshot_idx,"
                    " snippet(messages_fts, 0, '>>>', '<<<', '...', 12) AS snip,"
                    " bm25(messages_fts, 10.0, 2.0) AS rank"
                    " FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid"
                    f" WHERE messages_fts MATCH ?{filter_sql}"
                    f" ORDER BY {order} LIMIT 50"
                )
                try:
                    rows = self._conn.execute(sql, [match_expr, *params]).fetchall()
                except sqlite3.OperationalError as exc:
                    return {"error": f"Search failed: {exc}"}
            else:
                if not needle:
                    return {"error": "Query is empty."}
                escaped = needle.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
                sql = (
                    "SELECT m.id, m.session_id, m.role, m.snapshot_idx,"
                    " substr(m.body, 1, 160) AS snip, 0 AS rank"
                    " FROM messages m"
                    f" WHERE (m.body LIKE ? ESCAPE '\\' OR m.tool_name LIKE ? ESCAPE '\\'){filter_sql}"
                    " ORDER BY m.ts DESC LIMIT 50"
                )
                like = f"%{escaped}%"
                rows = self._conn.execute(sql, [like, like, *params]).fetchall()

            hits: list[dict[str, Any]] = []
            seen_sessions: set[str] = set()
            for row in rows:
                sid = row["session_id"]
                if sid in seen_sessions:
                    continue
                seen_sessions.add(sid)
                meta = self._session_meta(self._conn, sid) or {}
                window = self._around_locked(self._conn, sid, int(row["id"]), 5)
                bookends = self._bookends_locked(self._conn, sid, window)
                hits.append(
                    {
                        "session_id": sid,
                        "session": meta,
                        "match_message_id": int(row["id"]),
                        "matched_role": row["role"],
                        "snippet": row["snip"],
                        **bookends,
                        **window,
                    }
                )
                if len(hits) >= limit:
                    break
        return {"hits": hits}

    @staticmethod
    def _shape_row(row: sqlite3.Row, *, anchor_id: int | None = None, body_cap: int = 2_000) -> dict[str, Any]:
        body = row["body"] or ""
        shaped: dict[str, Any] = {
            "id": int(row["id"]),
            "role": row["role"],
            "content": body[:body_cap],
        }
        if len(body) > body_cap:
            shaped["truncated"] = True
        if row["tool_name"]:
            shaped["tool_name"] = row["tool_name"]
        if anchor_id is not None and int(row["id"]) == anchor_id:
            shaped["anchor"] = True
        return shaped

    def _around_locked(
        self, conn: sqlite3.Connection, session_id: str, anchor_id: int, window: int
    ) -> dict[str, Any]:
        """Id-ordered window around an anchor with honest global counts."""
        exists = conn.execute(
            "SELECT 1 FROM messages WHERE session_id = ? AND id = ?", (session_id, anchor_id)
        ).fetchone()
        if exists is None:
            return {"messages": [], "messages_before": 0, "messages_after": 0, "anchor_missing": True}
        before = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? AND id <= ? ORDER BY id DESC LIMIT ?",
            (session_id, anchor_id, window + 1),
        ).fetchall()[::-1]
        after = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
            (session_id, anchor_id, window),
        ).fetchall()
        total_before = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ? AND id < ?",
            (session_id, anchor_id),
        ).fetchone()[0]
        total_after = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ? AND id > ?",
            (session_id, anchor_id),
        ).fetchone()[0]
        rows = [*before, *after]
        return {
            "messages": [self._shape_row(row, anchor_id=anchor_id) for row in rows],
            "messages_before": int(total_before),
            "messages_after": int(total_after),
        }

    def _bookends_locked(
        self, conn: sqlite3.Connection, session_id: str, window: dict[str, Any], count: int = 3
    ) -> dict[str, Any]:
        messages = window.get("messages") or []
        if not messages:
            return {"bookend_start": [], "bookend_end": []}
        lo = messages[0]["id"]
        hi = messages[-1]["id"]
        start = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? AND id < ? AND role IN ('user','assistant')"
            " AND length(body) > 0 ORDER BY id ASC LIMIT ?",
            (session_id, lo, count),
        ).fetchall()
        end = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? AND id > ? AND role IN ('user','assistant')"
            " AND length(body) > 0 ORDER BY id DESC LIMIT ?",
            (session_id, hi, count),
        ).fetchall()[::-1]
        return {
            "bookend_start": [self._shape_row(row, body_cap=300) for row in start],
            "bookend_end": [self._shape_row(row, body_cap=300) for row in end],
        }

    def around(self, session_id: str, anchor_id: int, *, window: int = 5) -> dict[str, Any]:
        assert self._conn is not None
        window = max(1, min(int(window), 20))
        with self._lock:
            meta = self._session_meta(self._conn, session_id)
            if meta is None:
                return {"error": f"Session not found in index: {session_id}"}
            result = self._around_locked(self._conn, session_id, int(anchor_id), window)
        if result.pop("anchor_missing", False):
            return {"error": f"Message id {anchor_id} is not in session {session_id}."}
        return {"session": meta, **result}

    def read_session(self, session_id: str, *, head: int = 20, tail: int = 10) -> dict[str, Any]:
        assert self._conn is not None
        with self._lock:
            meta = self._session_meta(self._conn, session_id)
            if meta is None:
                return {"error": f"Session not found in index: {session_id}"}
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,)
            ).fetchall()
        if len(rows) <= head + tail:
            shaped = [self._shape_row(row) for row in rows]
            return {"session": meta, "messages": shaped, "truncated": False}
        shaped = [self._shape_row(row) for row in [*rows[:head], *rows[-tail:]]]
        return {
            "session": meta,
            "messages": shaped,
            "truncated": True,
            "message": (
                f"Session has {len(rows)} indexed messages; showing first {head} and last {tail}."
                " Pass around_message_id to scroll the middle."
            ),
        }

    def browse(self, *, project: str | None = None, limit: int = 10, exclude_session: str | None = None) -> dict[str, Any]:
        assert self._conn is not None
        limit = max(1, min(int(limit), 25))
        filters = []
        params: list[Any] = []
        if project and project != "all":
            filters.append("s.project = ?")
            params.append(project)
        if exclude_session:
            filters.append("s.session_id != ?")
            params.append(exclude_session)
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT s.*, (SELECT substr(body, 1, 80) FROM messages m"
                f" WHERE m.session_id = s.session_id AND m.role = 'user' ORDER BY m.id ASC LIMIT 1)"
                f" AS preview FROM sessions s {where} ORDER BY s.last_active DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
        return {"sessions": [dict(row) for row in rows]}


_INDEX: ConversationIndex | None = None
_INDEX_LOCK = threading.Lock()
_INDEX_EXECUTOR: Any = None


def get_conversation_index() -> ConversationIndex:
    """Return the process-wide index, creating it lazily."""
    global _INDEX
    with _INDEX_LOCK:
        if _INDEX is None:
            _INDEX = ConversationIndex()
        return _INDEX


def _index_executor():
    """Single-worker queue: indexing must never add latency to saves."""
    global _INDEX_EXECUTOR
    with _INDEX_LOCK:
        if _INDEX_EXECUTOR is None:
            from concurrent.futures import ThreadPoolExecutor

            _INDEX_EXECUTOR = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="conversation-index"
            )
        return _INDEX_EXECUTOR


def reset_conversation_index() -> None:
    """Drop the cached handle and drain the queue (tests and reindex)."""
    global _INDEX, _INDEX_EXECUTOR
    with _INDEX_LOCK:
        executor = _INDEX_EXECUTOR
        _INDEX_EXECUTOR = None
    if executor is not None:
        executor.shutdown(wait=True)
    with _INDEX_LOCK:
        if _INDEX is not None:
            _INDEX.close()
        _INDEX = None


def flush_index_queue() -> None:
    """Block until previously queued index updates are applied (tests/CLI)."""
    _index_executor().submit(lambda: None).result()


INDEX_DISABLED_MESSAGE = "Conversation indexing is disabled (conversation_index_enabled=false)."

def index_enabled() -> bool:
    """Single gate consulted by every surface (tool, CLI, headless, MCP)."""
    try:
        from openharness.config import load_settings

        return bool(load_settings().conversation_index_enabled)
    except Exception:
        return True


def index_snapshot_best_effort(payload: dict[str, Any], *, source: str = "local") -> None:
    """Queue a snapshot for indexing without ever blocking the save path."""

    def _apply() -> None:
        try:
            get_conversation_index().index_snapshot(payload, source=source)
        except Exception as exc:
            log.debug("conversation index update failed: %s", exc)

    try:
        _index_executor().submit(_apply)
    except RuntimeError:
        pass
