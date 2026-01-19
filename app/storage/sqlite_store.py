from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class SQLiteStateStore:
    def __init__(self, path: str):
        self.path = path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init(self) -> None:
        schema_sql = """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS issues (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          repo TEXT NOT NULL,
          issue_number INTEGER NOT NULL,
          issue_id INTEGER NOT NULL,
          issue_url TEXT NOT NULL,
          title TEXT,
          author_login TEXT,
          state TEXT,
          created_at TEXT,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          UNIQUE(repo, issue_number)
        );

        CREATE TABLE IF NOT EXISTS issue_analysis (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          issue_row_id INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          analysis_json TEXT NOT NULL,
          model_info_json TEXT,
          FOREIGN KEY(issue_row_id) REFERENCES issues(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_issue_analysis_issue_row_id_created_at
        ON issue_analysis(issue_row_id, created_at);

        CREATE TABLE IF NOT EXISTS notifications (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          issue_row_id INTEGER NOT NULL,
          analysis_id INTEGER NOT NULL,
          sent_at TEXT NOT NULL,
          channel TEXT NOT NULL,
          status TEXT NOT NULL,
          error TEXT,
          provider_response_json TEXT,
          FOREIGN KEY(issue_row_id) REFERENCES issues(id) ON DELETE CASCADE,
          FOREIGN KEY(analysis_id) REFERENCES issue_analysis(id) ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS idx_notifications_issue_row_id_sent_at
        ON notifications(issue_row_id, sent_at);

        CREATE TABLE IF NOT EXISTS run_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_at TEXT NOT NULL,
          repo TEXT NOT NULL,
          status TEXT NOT NULL,
          detail TEXT
        );
        """
        with self._conn() as conn:
            conn.executescript(schema_sql)

    # ---- dedup: repo + issue_number ----
    def has_issue(self, repo: str, issue_number: int) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM issues WHERE repo = ? AND issue_number = ? LIMIT 1;",
                (repo, issue_number),
            ).fetchone()
            return row is not None

    def upsert_issue(
        self,
        *,
        repo: str,
        issue_number: int,
        issue_id: int,
        issue_url: str,
        title: str,
        author_login: str,
        state: str,
        created_at: str,
    ) -> int:
        """
        Issue 实体：不存 body（按 1A）。
        以 (repo, issue_number) 唯一。
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO issues
                (repo, issue_number, issue_id, issue_url, title, author_login, state, created_at, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (repo, issue_number, issue_id, issue_url, title, author_login, state, created_at, now, now),
            )
            conn.execute(
                """
                UPDATE issues
                SET issue_id = ?, issue_url = ?, title = ?, author_login = ?, state = ?, created_at = ?, last_seen_at = ?
                WHERE repo = ? AND issue_number = ?;
                """,
                (issue_id, issue_url, title, author_login, state, created_at, now, repo, issue_number),
            )
            row = conn.execute(
                "SELECT id FROM issues WHERE repo = ? AND issue_number = ? LIMIT 1;",
                (repo, issue_number),
            ).fetchone()
            if not row:
                raise RuntimeError("Failed to upsert issue")
            return int(row["id"])

    # ---- analysis snapshots ----
    def insert_issue_analysis(
        self,
        *,
        issue_row_id: int,
        analysis: Dict[str, Any],  # 按 2A：不包含原始 title/body
        model_info: Optional[Dict[str, Any]] = None,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO issue_analysis (issue_row_id, created_at, analysis_json, model_info_json)
                VALUES (?, ?, ?, ?);
                """,
                (
                    issue_row_id,
                    now,
                    json.dumps(analysis, ensure_ascii=False),
                    json.dumps(model_info, ensure_ascii=False) if model_info else None,
                ),
            )
            return int(cur.lastrowid)

    # ---- notifications (each push event bound to analysis_id) ----
    def insert_notification(
        self,
        *,
        issue_row_id: int,
        analysis_id: int,
        channel: str,
        status: str,
        error: str = "",
        provider_response: Optional[Dict[str, Any]] = None,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO notifications
                (issue_row_id, analysis_id, sent_at, channel, status, error, provider_response_json)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    issue_row_id,
                    analysis_id,
                    now,
                    channel,
                    status,
                    error or None,
                    json.dumps(provider_response, ensure_ascii=False) if provider_response else None,
                ),
            )
            return int(cur.lastrowid)

    # ---- run log ----
    def log_run(self, repo: str, status: str, detail: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO run_log (run_at, repo, status, detail) VALUES (?, ?, ?, ?);",