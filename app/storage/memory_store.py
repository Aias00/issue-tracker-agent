from __future__ import annotations

import hashlib
import logging
from typing import List, Dict, Any, Optional
import psycopg2
from psycopg2.extras import RealDictCursor
import numpy as np

logger = logging.getLogger(__name__)

class MemoryStore:
    """Vector-based memory store for code embeddings and analysis history"""
    
    def __init__(self, connection_string: str, embedding_function=None):
        self.connection_string = connection_string
        self.embedding_function = embedding_function
        
    def _conn(self):
        return psycopg2.connect(self.connection_string, cursor_factory=RealDictCursor)
    
    # ---- Code Embeddings ----
    
    def upsert_code_embedding(
        self,
        *,
        repo: str,
        file_path: str,
        chunk_text: str,
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """Insert or update code embedding"""
        chunk_hash = hashlib.sha256(chunk_text.encode()).hexdigest()
        
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                # Check if exists
                cur.execute(
                    "SELECT id FROM code_embeddings WHERE chunk_hash = %s",
                    (chunk_hash,)
                )
                existing = cur.fetchone()
                
                if existing:
                    # Update
                    cur.execute(
                        """
                        UPDATE code_embeddings
                        SET embedding = %s, metadata = %s, updated_at = NOW()
                        WHERE chunk_hash = %s
                        RETURNING id
                        """,
                        (embedding, metadata, chunk_hash)
                    )
                else:
                    # Insert
                    cur.execute(
                        """
                        INSERT INTO code_embeddings
                        (repo, file_path, chunk_text, chunk_hash, embedding, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (repo, file_path, chunk_text, chunk_hash, embedding, metadata)
                    )
                
                result = cur.fetchone()
                conn.commit()
                return result['id']
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to upsert code embedding: {e}")
            raise
        finally:
            conn.close()
    
    def search_code_embeddings(
        self,
        *,
        query_embedding: List[float],
        repo: Optional[str] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Search for similar code chunks using vector similarity"""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                if repo:
                    cur.execute(
                        """
                        SELECT 
                            file_path, 
                            chunk_text, 
                            metadata,
                            1 - (embedding <=> %s::vector) AS similarity
                        FROM code_embeddings
                        WHERE repo = %s
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                        """,
                        (query_embedding, repo, query_embedding, limit)
                    )
                else:
                    cur.execute(
                        """
                        SELECT 
                            repo,
                            file_path, 
                            chunk_text, 
                            metadata,
                            1 - (embedding <=> %s::vector) AS similarity
                        FROM code_embeddings
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                        """,
                        (query_embedding, query_embedding, limit)
                    )
                
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()
    
    def delete_repo_embeddings(self, repo: str) -> int:
        """Delete all embeddings for a repository"""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM code_embeddings WHERE repo = %s", (repo,))
                deleted_count = cur.rowcount
                conn.commit()
                return deleted_count
        except Exception as e:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    # ---- Analysis Memory ----
    
    def insert_analysis_memory(
        self,
        *,
        issue_id: Optional[int],
        issue_title: str,
        issue_category: Optional[str],
        solution_summary: str,
        embedding: List[float]
    ) -> int:
        """Store analysis result as episodic memory"""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO analysis_memory
                    (issue_id, issue_title, issue_category, solution_summary, embedding)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (issue_id, issue_title, issue_category, solution_summary, embedding)
                )
                result = cur.fetchone()
                conn.commit()
                return result['id']
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to insert analysis memory: {e}")
            raise
        finally:
            conn.close()
    
    def search_similar_analyses(
        self,
        *,
        query_embedding: List[float],
        limit: int = 3
    ) -> List[Dict[str, Any]]:
        """Search for similar past analyses"""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 
                        issue_title,
                        issue_category,
                        solution_summary,
                        1 - (embedding <=> %s::vector) AS similarity
                    FROM analysis_memory
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (query_embedding, query_embedding, limit)
                )
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()
    
    # ---- Helper: Generate embedding ----
    
    def embed_text(self, text: str) -> List[float]:
        """Generate embedding for text using configured embedding function"""
        if not self.embedding_function:
            raise RuntimeError("Embedding function not configured")
        
        return self.embedding_function(text)
    
    # ---- Context retrieval with caching ----
    
    def get_cached_context(self, issue_id: int, context_hash: str) -> Optional[str]:
        """Check if we have cached context for this issue"""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT code_context
                    FROM issue_analysis
                    WHERE issue_row_id = %s AND context_hash = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (issue_id, context_hash)
                )
                row = cur.fetchone()
                return row['code_context'] if row else None
        finally:
            conn.close()
