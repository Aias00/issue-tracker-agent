from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


import os

class SQLiteStateStore:
    def __init__(self, path: str):
        self.path = path
        # Ensure parent directory exists
        db_dir = os.path.dirname(os.path.abspath(path))
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)

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
        conn = self._conn()
        try:
            conn.executescript(schema_sql)
            conn.commit()
            print(f"[SQLiteStateStore] Database initialized at {self.path}")
        except Exception as e:
            print(f"[SQLiteStateStore] Failed to initialize database: {e}")
            raise
        finally:
            conn.close()

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
        Issue 实体：不存 body。
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
        analysis: Dict[str, Any],
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

    # ---- notifications ----
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
    def log_run(self, repo: str, status: str, detail: str = "") -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO run_log (run_at, repo, status, detail) VALUES (?, ?, ?, ?);",
                (now, repo, status, detail or None),
            )
            return int(cur.lastrowid)

    @staticmethod
    def _clamp_limit(limit: int, default: int = 100, max_limit: int = 500) -> int:
        if limit is None:
            return default
        try:
            limit_i = int(limit)
        except Exception:
            return default
        if limit_i <= 0:
            return default
        return min(limit_i, max_limit)

    @staticmethod
    def _clamp_offset(offset: int) -> int:
        if offset is None:
            return 0
        try:
            offset_i = int(offset)
        except Exception:
            return 0
        return max(offset_i, 0)

    def list_issues(self, *, repo: Optional[str] = None, state: Optional[str] = None, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        limit = self._clamp_limit(limit)
        offset = self._clamp_offset(offset)
        where = []
        args: List[Any] = []
        if repo:
            where.append("repo = ?")
            args.append(repo)
        if state:
            where.append("state = ?")
            args.append(state)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"""
        SELECT id, repo, issue_number, issue_id, issue_url, title, author_login, state, created_at, first_seen_at, last_seen_at
        FROM issues
        {where_sql}
        ORDER BY created_at DESC, id DESC
        LIMIT ? OFFSET ?;
        """
        with self._conn() as conn:
            rows = conn.execute(sql, (*args, limit, offset)).fetchall()
            return [dict(r) for r in rows]

    def get_issue(self, issue_row_id: int) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT id, repo, issue_number, issue_id, issue_url, title, author_login, state, created_at, first_seen_at, last_seen_at
                FROM issues
                WHERE id = ?
                LIMIT 1;
                """,
                (issue_row_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_issue_analyses(self, *, issue_row_id: int, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        limit = self._clamp_limit(limit)
        offset = self._clamp_offset(offset)
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, issue_row_id, created_at, analysis_json, model_info_json
                FROM issue_analysis
                WHERE issue_row_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?;
                """,
                (issue_row_id, limit, offset),
            ).fetchall()
            out: List[Dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                d["analysis"] = json.loads(d.pop("analysis_json"))
                mi = d.pop("model_info_json")
                d["model_info"] = json.loads(mi) if mi else None
                out.append(d)
            return out

    def get_analysis(self, analysis_id: int) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT id, issue_row_id, created_at, analysis_json, model_info_json
                FROM issue_analysis
                WHERE id = ?
                LIMIT 1;
                """,
                (analysis_id,),
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["analysis"] = json.loads(d.pop("analysis_json"))
            mi = d.pop("model_info_json")
            d["model_info"] = json.loads(mi) if mi else None
            return d

    def list_notifications(self, *, issue_row_id: Optional[int] = None, status: Optional[str] = None, channel: Optional[str] = None, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        limit = self._clamp_limit(limit)
        offset = self._clamp_offset(offset)
        where = []
        args: List[Any] = []
        if issue_row_id is not None:
            where.append("issue_row_id = ?")
            args.append(issue_row_id)
        if status:
            where.append("status = ?")
            args.append(status)
        if channel:
            where.append("channel = ?")
            args.append(channel)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"""
        SELECT id, issue_row_id, analysis_id, sent_at, channel, status, error, provider_response_json
        FROM notifications
        {where_sql}
        ORDER BY sent_at DESC, id DESC
        LIMIT ? OFFSET ?;
        """
        with self._conn() as conn:
            rows = conn.execute(sql, (*args, limit, offset)).fetchall()
            out: List[Dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                pr = d.pop("provider_response_json")
                d["provider_response"] = json.loads(pr) if pr else None
                out.append(d)
            return out

    def get_notification(self, notification_id: int) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT id, issue_row_id, analysis_id, sent_at, channel, status, error, provider_response_json
                FROM notifications
                WHERE id = ?
                LIMIT 1;
                """,
                (notification_id,),
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            pr = d.pop("provider_response_json")
            d["provider_response"] = json.loads(pr) if pr else None
            return d

    def list_runs(self, *, repo: Optional[str] = None, status: Optional[str] = None, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        limit = self._clamp_limit(limit)
        offset = self._clamp_offset(offset)
        where = []
        args: List[Any] = []
        if repo:
            where.append("repo = ?")
            args.append(repo)
        if status:
            where.append("status = ?")
            args.append(status)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"""
        SELECT id, run_at, repo, status, detail
        FROM run_log
        {where_sql}
        ORDER BY run_at DESC, id DESC
        LIMIT ? OFFSET ?;
        """
        with self._conn() as conn:
            rows = conn.execute(sql, (*args, limit, offset)).fetchall()
            return [dict(r) for r in rows]