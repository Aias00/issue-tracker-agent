from __future__ import annotations

import json
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

class PostgresStateStore:
    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        
    def _conn(self):
        """Get a new database connection"""
        return psycopg2.connect(self.connection_string, cursor_factory=RealDictCursor)

    def init(self) -> None:
        """Initialize database schema"""
        schema_sql = """
        -- Enable pgvector extension
        CREATE EXTENSION IF NOT EXISTS vector;

        -- ============================================
        -- Repos management table
        -- ============================================
        CREATE TABLE IF NOT EXISTS repos (
          id SERIAL PRIMARY KEY,
          full_name TEXT UNIQUE NOT NULL,  -- e.g. "apache/hertzbeat"
          local_path TEXT,                  -- e.g. "/Users/xxx/workspace/hertzbeat"
          is_active BOOLEAN DEFAULT TRUE,
          auto_sync_issues BOOLEAN DEFAULT TRUE,
          auto_sync_prs BOOLEAN DEFAULT FALSE,
          created_at TIMESTAMP DEFAULT NOW(),
          updated_at TIMESTAMP DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_repos_full_name ON repos(full_name);
        CREATE INDEX IF NOT EXISTS idx_repos_active ON repos(is_active) WHERE is_active = TRUE;

        -- ============================================
        -- Issues table
        -- ============================================
        CREATE TABLE IF NOT EXISTS issues (
          id SERIAL PRIMARY KEY,
          repo TEXT NOT NULL,
          issue_number INTEGER NOT NULL,
          issue_id BIGINT NOT NULL,  -- GitHub issue IDs can exceed INTEGER range
          issue_url TEXT NOT NULL,
          title TEXT,
          body TEXT,
          author_login TEXT,
          state TEXT,
          labels JSONB,
          created_at TIMESTAMP,
          first_seen_at TIMESTAMP NOT NULL,
          last_seen_at TIMESTAMP NOT NULL,
          UNIQUE(repo, issue_number)
        );
        
        -- Alter existing table if issue_id is still INTEGER
        DO $$ 
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'issues' AND column_name = 'issue_id' AND data_type = 'integer'
          ) THEN
            ALTER TABLE issues ALTER COLUMN issue_id TYPE BIGINT;
          END IF;
        END $$;

        -- Add body column if not exists
        DO $$ 
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'issues' AND column_name = 'body'
          ) THEN
            ALTER TABLE issues ADD COLUMN body TEXT;
          END IF;
        END $$;

        -- Add labels column if not exists
        DO $$ 
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'issues' AND column_name = 'labels'
          ) THEN
            ALTER TABLE issues ADD COLUMN labels JSONB;
          END IF;
        END $$;

        CREATE TABLE IF NOT EXISTS issue_analysis (
          id SERIAL PRIMARY KEY,
          issue_row_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
          created_at TIMESTAMP NOT NULL,
          analysis_json JSONB NOT NULL,
          model_info_json JSONB,
          code_context TEXT,
          context_hash TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_issue_analysis_issue_row_id_created_at
        ON issue_analysis(issue_row_id, created_at DESC);

        -- ============================================
        -- Pull Requests table
        -- ============================================
        CREATE TABLE IF NOT EXISTS pull_requests (
          id SERIAL PRIMARY KEY,
          repo TEXT NOT NULL,
          pr_number INTEGER NOT NULL,
          pr_id BIGINT NOT NULL,
          pr_url TEXT NOT NULL,
          title TEXT,
          body TEXT,
          author_login TEXT,
          state TEXT,  -- open, closed, merged
          head_ref TEXT,  -- source branch
          base_ref TEXT,  -- target branch
          head_sha TEXT,  -- latest commit SHA
          labels JSONB,
          diff_url TEXT,
          files_changed INTEGER,
          additions INTEGER,
          deletions INTEGER,
          created_at TIMESTAMP,
          updated_at TIMESTAMP,
          merged_at TIMESTAMP,
          first_seen_at TIMESTAMP NOT NULL,
          last_seen_at TIMESTAMP NOT NULL,
          UNIQUE(repo, pr_number)
        );

        CREATE INDEX IF NOT EXISTS idx_pull_requests_repo ON pull_requests(repo);
        CREATE INDEX IF NOT EXISTS idx_pull_requests_state ON pull_requests(state);

        -- Alter issue_number to BIGINT if it is INTEGER
        DO $$ 
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'issues' AND column_name = 'issue_number' AND data_type = 'integer'
          ) THEN
            ALTER TABLE issues ALTER COLUMN issue_number TYPE BIGINT;
          END IF;
        END $$;
        
        -- Same for pr_number in pull_requests
        DO $$ 
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'pull_requests' AND column_name = 'pr_number' AND data_type = 'integer'
          ) THEN
            ALTER TABLE pull_requests ALTER COLUMN pr_number TYPE BIGINT;
          END IF;
        END $$;

        -- ============================================
        -- PR Reviews table (similar to issue_analysis)
        -- ============================================
        CREATE TABLE IF NOT EXISTS pr_reviews (
          id SERIAL PRIMARY KEY,
          pr_row_id INTEGER NOT NULL REFERENCES pull_requests(id) ON DELETE CASCADE,
          created_at TIMESTAMP NOT NULL,
          review_json JSONB NOT NULL,  -- Contains: summary, issues, suggestions, score, etc.
          model_info_json JSONB,
          code_context TEXT,  -- Diff content or related code
          files_reviewed JSONB,  -- List of files included in review
          review_type TEXT DEFAULT 'full'  -- 'full', 'incremental', 'quick'
        );

        CREATE INDEX IF NOT EXISTS idx_pr_reviews_pr_row_id_created_at
        ON pr_reviews(pr_row_id, created_at DESC);

        -- ============================================
        -- Notifications table
        -- ============================================
        CREATE TABLE IF NOT EXISTS notifications (
          id SERIAL PRIMARY KEY,
          issue_row_id INTEGER REFERENCES issues(id) ON DELETE CASCADE,
          pr_row_id INTEGER REFERENCES pull_requests(id) ON DELETE CASCADE,
          analysis_id INTEGER REFERENCES issue_analysis(id) ON DELETE RESTRICT,
          review_id INTEGER REFERENCES pr_reviews(id) ON DELETE RESTRICT,
          sent_at TIMESTAMP NOT NULL,
          channel TEXT NOT NULL,
          status TEXT NOT NULL,
          error TEXT,
          provider_response_json JSONB
        );

        -- Migrate existing notifications table to add new columns
        DO $$ 
        BEGIN
          -- Add pr_row_id column if not exists
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'notifications' AND column_name = 'pr_row_id'
          ) THEN
            ALTER TABLE notifications ADD COLUMN pr_row_id INTEGER REFERENCES pull_requests(id) ON DELETE CASCADE;
          END IF;
          
          -- Add review_id column if not exists
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'notifications' AND column_name = 'review_id'
          ) THEN
            ALTER TABLE notifications ADD COLUMN review_id INTEGER REFERENCES pr_reviews(id) ON DELETE RESTRICT;
          END IF;
          
          -- Make issue_row_id nullable if it's NOT NULL
          -- (for existing tables that had NOT NULL constraint)
          BEGIN
            ALTER TABLE notifications ALTER COLUMN issue_row_id DROP NOT NULL;
          EXCEPTION WHEN others THEN
            -- Column is already nullable, ignore
            NULL;
          END;
          
          -- Make analysis_id nullable if it's NOT NULL
          BEGIN
            ALTER TABLE notifications ALTER COLUMN analysis_id DROP NOT NULL;
          EXCEPTION WHEN others THEN
            NULL;
          END;
        END $$;

        CREATE INDEX IF NOT EXISTS idx_notifications_issue_row_id_sent_at
        ON notifications(issue_row_id, sent_at DESC);
        
        CREATE INDEX IF NOT EXISTS idx_notifications_pr_row_id_sent_at
        ON notifications(pr_row_id, sent_at DESC);


        CREATE TABLE IF NOT EXISTS run_log (
          id SERIAL PRIMARY KEY,
          run_at TIMESTAMP NOT NULL,
          repo TEXT NOT NULL,
          status TEXT NOT NULL,
          run_type TEXT DEFAULT 'issues',  -- 'issues', 'prs', 'pr_review', 'all'
          detail TEXT
        );

        -- Add run_type column if not exists
        DO $$ 
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'run_log' AND column_name = 'run_type'
          ) THEN
            ALTER TABLE run_log ADD COLUMN run_type TEXT DEFAULT 'issues';
          END IF;
        END $$;

        -- Memory tables (vector storage)
        CREATE TABLE IF NOT EXISTS code_embeddings (
          id SERIAL PRIMARY KEY,
          repo TEXT NOT NULL,
          file_path TEXT NOT NULL,
          chunk_text TEXT NOT NULL,
          chunk_hash TEXT UNIQUE NOT NULL,
          embedding vector(1536),
          metadata JSONB,
          created_at TIMESTAMP DEFAULT NOW(),
          updated_at TIMESTAMP DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_code_embeddings_repo ON code_embeddings(repo);
        CREATE INDEX IF NOT EXISTS idx_code_embeddings_hash ON code_embeddings(chunk_hash);

        -- HNSW index for fast similarity search
        CREATE INDEX IF NOT EXISTS idx_code_embeddings_vector 
        ON code_embeddings USING hnsw (embedding vector_cosine_ops);

        CREATE TABLE IF NOT EXISTS analysis_memory (
          id SERIAL PRIMARY KEY,
          issue_id INTEGER REFERENCES issues(id) ON DELETE SET NULL,
          pr_id INTEGER REFERENCES pull_requests(id) ON DELETE SET NULL,
          title TEXT NOT NULL,
          category TEXT,
          solution_summary TEXT,
          embedding vector(1536),
          created_at TIMESTAMP DEFAULT NOW()
        );

        -- Add pr_id column if not exists
        DO $$ 
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'analysis_memory' AND column_name = 'pr_id'
          ) THEN
            ALTER TABLE analysis_memory ADD COLUMN pr_id INTEGER REFERENCES pull_requests(id) ON DELETE SET NULL;
          END IF;
        END $$;

        -- Rename issue_title to title if needed
        DO $$ 
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'analysis_memory' AND column_name = 'issue_title'
          ) THEN
            ALTER TABLE analysis_memory RENAME COLUMN issue_title TO title;
          END IF;
        END $$;

        -- Rename issue_category to category if needed
        DO $$ 
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'analysis_memory' AND column_name = 'issue_category'
          ) THEN
            ALTER TABLE analysis_memory RENAME COLUMN issue_category TO category;
          END IF;
        END $$;

        CREATE INDEX IF NOT EXISTS idx_analysis_memory_vector
        ON analysis_memory USING hnsw (embedding vector_cosine_ops);
        """
        
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(schema_sql)
            conn.commit()
            logger.info(f"PostgreSQL database initialized")
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to initialize database: {e}")
            raise
        finally:
            conn.close()

    # ---- Issue operations ----
    def has_issue(self, repo: str, issue_number: int) -> bool:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM issues WHERE repo = %s AND issue_number = %s LIMIT 1",
                    (repo, issue_number)
                )
                return cur.fetchone() is not None
        finally:
            conn.close()

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
        now = datetime.now(timezone.utc)
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                # Insert or ignore
                cur.execute(
                    """
                    INSERT INTO issues
                    (repo, issue_number, issue_id, issue_url, title, author_login, state, created_at, first_seen_at, last_seen_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (repo, issue_number) DO NOTHING
                    """,
                    (repo, issue_number, issue_id, issue_url, title, author_login, state, created_at, now, now)
                )
                
                # Update
                cur.execute(
                    """
                    UPDATE issues
                    SET issue_id = %s, issue_url = %s, title = %s, author_login = %s, 
                        state = %s, created_at = %s, last_seen_at = %s
                    WHERE repo = %s AND issue_number = %s
                    """,
                    (issue_id, issue_url, title, author_login, state, created_at, now, repo, issue_number)
                )
                
                # Get ID
                cur.execute(
                    "SELECT id FROM issues WHERE repo = %s AND issue_number = %s",
                    (repo, issue_number)
                )
                row = cur.fetchone()
                if not row:
                    raise RuntimeError("Failed to upsert issue")
                
                conn.commit()
                return row['id']
        except Exception as e:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---- Analysis operations ----
    def insert_issue_analysis(
        self,
        *,
        issue_row_id: int,
        analysis: Dict[str, Any],
        model_info: Optional[Dict[str, Any]] = None,
        code_context: Optional[str] = None,
        context_hash: Optional[str] = None,
    ) -> int:
        now = datetime.now(timezone.utc)
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO issue_analysis 
                    (issue_row_id, created_at, analysis_json, model_info_json, code_context, context_hash)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        issue_row_id,
                        now,
                        json.dumps(analysis, ensure_ascii=False),
                        json.dumps(model_info, ensure_ascii=False) if model_info else None,
                        code_context,
                        context_hash
                    )
                )
                result = cur.fetchone()
                conn.commit()
                return result['id']
        except Exception as e:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---- Notification operations ----
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
        now = datetime.now(timezone.utc)
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO notifications
                    (issue_row_id, analysis_id, sent_at, channel, status, error, provider_response_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        issue_row_id,
                        analysis_id,
                        now,
                        channel,
                        status,
                        error or None,
                        json.dumps(provider_response, ensure_ascii=False) if provider_response else None
                    )
                )
                result = cur.fetchone()
                conn.commit()
                return result['id']
        except Exception as e:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---- Run log ----
    def log_run(self, repo: str, status: str, detail: str = "") -> int:
        now = datetime.now(timezone.utc)
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO run_log (run_at, repo, status, detail) VALUES (%s, %s, %s, %s) RETURNING id",
                    (now, repo, status, detail or None)
                )
                result = cur.fetchone()
                conn.commit()
                return result['id']
        except Exception as e:
            conn.rollback()
            raise
        finally:
            conn.close()

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

    def list_issues(
        self, 
        *, 
        repo: Optional[str] = None, 
        state: Optional[str] = None, 
        limit: int = 100, 
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        limit = self._clamp_limit(limit)
        offset = self._clamp_offset(offset)
        
        where_clauses = []
        params = []
        
        if repo:
            where_clauses.append("repo = %s")
            params.append(repo)
        if state:
            where_clauses.append("state = %s")
            params.append(state)
        
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        
        sql = f"""
        SELECT id, repo, issue_number, issue_id, issue_url, title, author_login, state, 
               created_at, first_seen_at, last_seen_at
        FROM issues
        {where_sql}
        ORDER BY created_at DESC, id DESC
        LIMIT %s OFFSET %s
        """
        
        params.extend([limit, offset])
        
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_issue(self, issue_row_id: int) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, repo, issue_number, issue_id, issue_url, title, author_login, state,
                           created_at, first_seen_at, last_seen_at
                    FROM issues
                    WHERE id = %s
                    """,
                    (issue_row_id,)
                )
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()

    def list_issue_analyses(
        self, 
        *, 
        issue_row_id: int, 
        limit: int = 100, 
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        limit = self._clamp_limit(limit)
        offset = self._clamp_offset(offset)
        
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, issue_row_id, created_at, analysis_json, model_info_json
                    FROM issue_analysis
                    WHERE issue_row_id = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (issue_row_id, limit, offset)
                )
                
                out = []
                for row in cur.fetchall():
                    d = dict(row)
                    d["analysis"] = d.pop("analysis_json")
                    d["model_info"] = d.pop("model_info_json")
                    out.append(d)
                return out
        finally:
            conn.close()

    def get_analysis(self, analysis_id: int) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, issue_row_id, created_at, analysis_json, model_info_json
                    FROM issue_analysis
                    WHERE id = %s
                    """,
                    (analysis_id,)
                )
                row = cur.fetchone()
                if not row:
                    return None
                
                d = dict(row)
                d["analysis"] = d.pop("analysis_json")
                d["model_info"] = d.pop("model_info_json")
                return d
        finally:
            conn.close()

    def list_notifications(
        self,
        *,
        issue_row_id: Optional[int] = None,
        status: Optional[str] = None,
        channel: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        limit = self._clamp_limit(limit)
        offset = self._clamp_offset(offset)
        
        where_clauses = []
        params = []
        
        if issue_row_id is not None:
            where_clauses.append("issue_row_id = %s")
            params.append(issue_row_id)
        if status:
            where_clauses.append("status = %s")
            params.append(status)
        if channel:
            where_clauses.append("channel = %s")
            params.append(channel)
        
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        
        sql = f"""
        SELECT id, issue_row_id, analysis_id, sent_at, channel, status, error, provider_response_json
        FROM notifications
        {where_sql}
        ORDER BY sent_at DESC, id DESC
        LIMIT %s OFFSET %s
        """
        
        params.extend([limit, offset])
        
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                out = []
                for row in cur.fetchall():
                    d = dict(row)
                    d["provider_response"] = d.pop("provider_response_json")
                    out.append(d)
                return out
        finally:
            conn.close()

    def get_notification(self, notification_id: int) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, issue_row_id, analysis_id, sent_at, channel, status, error, provider_response_json
                    FROM notifications
                    WHERE id = %s
                    """,
                    (notification_id,)
                )
                row = cur.fetchone()
                if not row:
                    return None
                
                d = dict(row)
                d["provider_response"] = d.pop("provider_response_json")
                return d
        finally:
            conn.close()

    def list_runs(
        self,
        *,
        repo: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        limit = self._clamp_limit(limit)
        offset = self._clamp_offset(offset)
        
        where_clauses = []
        params = []
        
        if repo:
            where_clauses.append("repo = %s")
            params.append(repo)
        if status:
            where_clauses.append("status = %s")
            params.append(status)
        
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        
        sql = f"""
        SELECT id, run_at, repo, status, detail
        FROM run_log
        {where_sql}
        ORDER BY run_at DESC, id DESC
        LIMIT %s OFFSET %s
        """
        
        params.extend([limit, offset])
        
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    # ============================================
    # Repos management operations
    # ============================================
    def list_repos(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """List all repos, optionally filtering by active status"""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                if active_only:
                    cur.execute(
                        """SELECT id, full_name, local_path, is_active, auto_sync_issues, auto_sync_prs, 
                                  created_at, updated_at
                           FROM repos WHERE is_active = TRUE ORDER BY full_name"""
                    )
                else:
                    cur.execute(
                        """SELECT id, full_name, local_path, is_active, auto_sync_issues, auto_sync_prs,
                                  created_at, updated_at
                           FROM repos ORDER BY full_name"""
                    )
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_repo(self, repo_id: int = None, full_name: str = None) -> Optional[Dict[str, Any]]:
        """Get a repo by ID or full_name"""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                if repo_id:
                    cur.execute(
                        """SELECT id, full_name, local_path, is_active, auto_sync_issues, auto_sync_prs,
                                  created_at, updated_at
                           FROM repos WHERE id = %s""",
                        (repo_id,)
                    )
                elif full_name:
                    cur.execute(
                        """SELECT id, full_name, local_path, is_active, auto_sync_issues, auto_sync_prs,
                                  created_at, updated_at
                           FROM repos WHERE full_name = %s""",
                        (full_name,)
                    )
                else:
                    return None
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()

    def upsert_repo(
        self,
        *,
        full_name: str,
        local_path: str = None,
        is_active: bool = True,
        auto_sync_issues: bool = True,
        auto_sync_prs: bool = False,
    ) -> int:
        """Insert or update a repo"""
        now = datetime.now(timezone.utc)
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO repos (full_name, local_path, is_active, auto_sync_issues, auto_sync_prs, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (full_name) DO UPDATE SET
                        local_path = EXCLUDED.local_path,
                        is_active = EXCLUDED.is_active,
                        auto_sync_issues = EXCLUDED.auto_sync_issues,
                        auto_sync_prs = EXCLUDED.auto_sync_prs,
                        updated_at = EXCLUDED.updated_at
                    RETURNING id
                    """,
                    (full_name, local_path, is_active, auto_sync_issues, auto_sync_prs, now, now)
                )
                result = cur.fetchone()
                conn.commit()
                return result['id']
        except Exception as e:
            conn.rollback()
            raise
        finally:
            conn.close()

    def delete_repo(self, repo_id: int) -> bool:
        """Delete a repo by ID"""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM repos WHERE id = %s", (repo_id,))
                conn.commit()
                return cur.rowcount > 0
        except Exception as e:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ============================================
    # Pull Request operations
    # ============================================
    def has_pr(self, repo: str, pr_number: int) -> bool:
        """Check if a PR exists"""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pull_requests WHERE repo = %s AND pr_number = %s LIMIT 1",
                    (repo, pr_number)
                )
                return cur.fetchone() is not None
        finally:
            conn.close()

    def upsert_pr(
        self,
        *,
        repo: str,
        pr_number: int,
        pr_id: int,
        pr_url: str,
        title: str,
        body: str = None,
        author_login: str,
        state: str,
        head_ref: str = None,
        base_ref: str = None,
        head_sha: str = None,
        labels: List[str] = None,
        diff_url: str = None,
        files_changed: int = None,
        additions: int = None,
        deletions: int = None,
        created_at: str = None,
        updated_at: str = None,
        merged_at: str = None,
    ) -> int:
        """Insert or update a pull request"""
        now = datetime.now(timezone.utc)
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pull_requests 
                    (repo, pr_number, pr_id, pr_url, title, body, author_login, state,
                     head_ref, base_ref, head_sha, labels, diff_url, files_changed, 
                     additions, deletions, created_at, updated_at, merged_at, first_seen_at, last_seen_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (repo, pr_number) DO UPDATE SET
                        pr_id = EXCLUDED.pr_id,
                        pr_url = EXCLUDED.pr_url,
                        title = EXCLUDED.title,
                        body = EXCLUDED.body,
                        author_login = EXCLUDED.author_login,
                        state = EXCLUDED.state,
                        head_ref = EXCLUDED.head_ref,
                        base_ref = EXCLUDED.base_ref,
                        head_sha = EXCLUDED.head_sha,
                        labels = EXCLUDED.labels,
                        diff_url = EXCLUDED.diff_url,
                        files_changed = EXCLUDED.files_changed,
                        additions = EXCLUDED.additions,
                        deletions = EXCLUDED.deletions,
                        updated_at = EXCLUDED.updated_at,
                        merged_at = EXCLUDED.merged_at,
                        last_seen_at = EXCLUDED.last_seen_at
                    RETURNING id
                    """,
                    (repo, pr_number, pr_id, pr_url, title, body, author_login, state,
                     head_ref, base_ref, head_sha, json.dumps(labels) if labels else None,
                     diff_url, files_changed, additions, deletions, created_at, updated_at, merged_at, now, now)
                )
                result = cur.fetchone()
                conn.commit()
                return result['id']
        except Exception as e:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_pr(self, pr_row_id: int = None, repo: str = None, pr_number: int = None) -> Optional[Dict[str, Any]]:
        """Get a PR by row ID or repo+pr_number"""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                if pr_row_id:
                    cur.execute(
                        """SELECT id, repo, pr_number, pr_id, pr_url, title, body, author_login, state,
                                  head_ref, base_ref, head_sha, labels, diff_url, files_changed,
                                  additions, deletions, created_at, updated_at, merged_at,
                                  first_seen_at, last_seen_at
                           FROM pull_requests WHERE id = %s""",
                        (pr_row_id,)
                    )
                elif repo and pr_number:
                    cur.execute(
                        """SELECT id, repo, pr_number, pr_id, pr_url, title, body, author_login, state,
                                  head_ref, base_ref, head_sha, labels, diff_url, files_changed,
                                  additions, deletions, created_at, updated_at, merged_at,
                                  first_seen_at, last_seen_at
                           FROM pull_requests WHERE repo = %s AND pr_number = %s""",
                        (repo, pr_number)
                    )
                else:
                    return None
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()

    def list_prs(
        self,
        *,
        repo: Optional[str] = None,
        state: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """List pull requests with optional filters"""
        limit = self._clamp_limit(limit)
        offset = self._clamp_offset(offset)
        
        where_clauses = []
        params = []
        
        if repo:
            where_clauses.append("repo = %s")
            params.append(repo)
        if state:
            where_clauses.append("state = %s")
            params.append(state)
        
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        
        sql = f"""
        SELECT id, repo, pr_number, pr_id, pr_url, title, author_login, state,
               head_ref, base_ref, files_changed, additions, deletions,
               created_at, updated_at, first_seen_at, last_seen_at
        FROM pull_requests
        {where_sql}
        ORDER BY created_at DESC, id DESC
        LIMIT %s OFFSET %s
        """
        
        params.extend([limit, offset])
        
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    # ============================================
    # PR Review operations
    # ============================================
    def insert_pr_review(
        self,
        *,
        pr_row_id: int,
        review: Dict[str, Any],
        model_info: Optional[Dict[str, Any]] = None,
        code_context: Optional[str] = None,
        files_reviewed: Optional[List[str]] = None,
        review_type: str = "full",
    ) -> int:
        """Insert a new PR review"""
        now = datetime.now(timezone.utc)
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pr_reviews 
                    (pr_row_id, created_at, review_json, model_info_json, code_context, files_reviewed, review_type)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        pr_row_id,
                        now,
                        json.dumps(review, ensure_ascii=False),
                        json.dumps(model_info, ensure_ascii=False) if model_info else None,
                        code_context,
                        json.dumps(files_reviewed, ensure_ascii=False) if files_reviewed else None,
                        review_type
                    )
                )
                result = cur.fetchone()
                conn.commit()
                return result['id']
        except Exception as e:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_pr_reviews(
        self,
        *,
        pr_row_id: int,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """List reviews for a PR"""
        limit = self._clamp_limit(limit)
        offset = self._clamp_offset(offset)
        
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, pr_row_id, created_at, review_json, model_info_json, 
                           files_reviewed, review_type
                    FROM pr_reviews
                    WHERE pr_row_id = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (pr_row_id, limit, offset)
                )
                
                out = []
                for row in cur.fetchall():
                    d = dict(row)
                    d["review"] = d.pop("review_json")
                    d["model_info"] = d.pop("model_info_json")
                    out.append(d)
                return out
        finally:
            conn.close()

    def get_pr_review(self, review_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific PR review"""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, pr_row_id, created_at, review_json, model_info_json,
                           code_context, files_reviewed, review_type
                    FROM pr_reviews
                    WHERE id = %s
                    """,
                    (review_id,)
                )
                row = cur.fetchone()
                if not row:
                    return None
                
                d = dict(row)
                d["review"] = d.pop("review_json")
                d["model_info"] = d.pop("model_info_json")
                return d
        finally:
            conn.close()
